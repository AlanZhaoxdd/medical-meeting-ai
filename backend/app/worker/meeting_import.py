from __future__ import annotations

import asyncio
import hashlib
import re
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from celery import Task
from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.core.exceptions import AppException
from app.models.kb import (
    Document,
    DocumentBlock,
    IngestionJob,
    MeetingImport,
    MeetingImportStatus,
    TranscriptRevision,
    TranscriptRevisionBlock,
    TranscriptRevisionStatus,
)
from app.services.storage import ObjectStorage
from app.worker.celery_app import celery_app
from app.worker.parser import (
    clean_table_markdown,
    parse_document_bytes,
    table_rows_to_markdown,
)

_MEETING_INFO_ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("会议名称", "会议名", "会议标题"),
    "meeting_purpose": ("会议目的", "会议目标"),
    "discussion_topics": ("讨论题目", "讨论主题", "讨论议题"),
    "meeting_date": ("会议日期", "会议时间", "召开日期"),
    "advisor_selection_criteria": ("顾问选择标准", "顾问选择条件", "专家选择标准"),
    "advisor_names": ("参会顾问姓名", "参会顾问", "顾问姓名", "专家姓名"),
    "internal_attendees": (
        "诺和诺德内部参会人及参会原因",
        "诺和诺德内部参会人（Initial）以及参会原因",
        "诺和诺德内部参会人Initial以及参会原因",
        "诺和诺德内部参会人",
        "内部参会人及参会原因",
        "内部参会人",
    ),
    "recorder": ("记录人", "纪要记录人", "记录人员"),
}
_MEETING_INFO_LABELS = {
    re.sub(r"[\s:：、,，。.!！?？()（）\[\]【】_-]+", "", alias).casefold(): key
    for key, aliases in _MEETING_INFO_ALIASES.items()
    for alias in aliases
}


def vector_job_id(import_id: UUID, revision_id: UUID, revision_version: int) -> str:
    return f"meeting-vector-{import_id}-{revision_id}-v{revision_version}"


def document_lock_key(document_id: UUID) -> int:
    """Stable signed int64 key shared by revision writers and vector workers."""
    raw = hashlib.sha256(document_id.bytes).digest()[:8]
    value = int.from_bytes(raw, byteorder="big", signed=False)
    return value - 2**64 if value >= 2**63 else value


async def ensure_vectorization_job(
    session: AsyncSession, item: MeetingImport, revision: TranscriptRevision, document: Document
) -> tuple[IngestionJob, bool]:
    """Create the single revision-scoped chunk/embed job, safely under a lock."""
    job_id = vector_job_id(item.id, revision.id, revision.version)
    job = await session.scalar(
        select(IngestionJob).where(IngestionJob.job_id == job_id).with_for_update()
    )
    created = job is None
    if job is None:
        job = IngestionJob(
            job_id=job_id,
            organization_id=item.organization_id,
            knowledge_base_id=item.knowledge_base_id,
            document_id=document.id,
            status="QUEUED",
            current_node="build_chunks",
            progress=0,
            result_summary={
                "source": "meeting_transcript_revision",
                "revision_id": str(revision.id),
                "revision_version": revision.version,
                "mode": "vector_only",
            },
        )
        session.add(job)
    elif (job.result_summary or {}).get("revision_id") != str(revision.id):
        raise AppException(409, "vectorization_revision_mismatch", "向量任务修订版本不一致")
    return job, created


def _normalize_info_label(value: str) -> str:
    return re.sub(r"[\s:：、,，。.!！?？()（）\[\]【】_-]+", "", value).casefold()


def _block_source(block: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            key: block[key]
            for key in (
                "block_id",
                "page_number",
                "slide_number",
                "speaker",
                "start_ms",
                "end_ms",
            )
            if block.get(key) is not None
        }
    ] if block.get("block_id") else []


def _table_rows(block: dict[str, Any]) -> list[list[str]]:
    structured_rows = block.get("table_rows")
    if isinstance(structured_rows, list):
        return [
            [str(cell or "").strip() for cell in row]
            for row in structured_rows
            if isinstance(row, list)
        ]
    raw_text = clean_table_markdown(
        str(block.get("table_markdown") or block.get("text") or "")
    )
    rows: list[list[str]] = []
    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if "|" not in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if cells and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        rows.append(cells)
    return rows


def _first_page_table_indexes(blocks: list[dict[str, Any]]) -> list[int]:
    table_indexes = [
        index for index, block in enumerate(blocks) if block.get("block_type") == "table"
    ]
    numbered_pages = [
        int(block["page_number"])
        for block in blocks
        if block.get("page_number") is not None
    ]
    if not numbered_pages:
        return table_indexes
    first_page = min(numbered_pages)
    return [
        index
        for index in table_indexes
        if blocks[index].get("page_number") is not None
        and int(blocks[index]["page_number"]) == first_page
    ]


def _extract_labeled_values(
    blocks: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], int | None]:
    """Read only the value column of the first-page meeting-information table."""
    found: dict[str, dict[str, Any]] = {}
    table_end_index: int | None = None
    for block_index in _first_page_table_indexes(blocks):
        block = blocks[block_index]
        rows = _table_rows(block)
        policy_column = next(
            (
                cell_index
                for row in rows
                for cell_index, cell in enumerate(row)
                if _normalize_info_label(cell) == _normalize_info_label("政策指引")
            ),
            None,
        )
        table_values: dict[str, str] = {}
        for cells in rows:
            for cell_index, cell in enumerate(cells[:-1]):
                key = _MEETING_INFO_LABELS.get(_normalize_info_label(cell))
                if key is None:
                    continue
                value_end = (
                    policy_column
                    if policy_column is not None and policy_column > cell_index
                    else cell_index + 2
                )
                value_parts: list[str] = []
                for raw_value in cells[cell_index + 1 : value_end]:
                    value = raw_value.strip()
                    if value and value not in value_parts:
                        value_parts.append(value)
                if key == "internal_attendees" and len(value_parts) == 2:
                    value = f"{value_parts[0]}：{value_parts[1]}"
                else:
                    value = "\n".join(value_parts)
                if value and value not in table_values.get(key, "").splitlines():
                    table_values[key] = (
                        f"{table_values[key]}\n{value}" if key in table_values else value
                    )
                break
        if len(table_values) < 2:
            continue
        table_end_index = block_index
        for key, value in table_values.items():
            if value and key not in found:
                found[key] = {"value": value, "source": _block_source(block)}

    if table_end_index is not None:
        return found, table_end_index

    # Preserve compatibility with plain-text imports that use `标签：值`
    # instead of a table. First match wins so later discussion text cannot
    # overwrite the meeting envelope.
    for block in blocks:
        for line in str(block.get("text") or "").splitlines():
            match = re.match(r"^(.{1,40}?)[：:]\s*(.+)$", line.strip())
            if match is None:
                continue
            key = _MEETING_INFO_LABELS.get(_normalize_info_label(match.group(1)))
            value = match.group(2).strip()
            if key and value and key not in found:
                found[key] = {"value": value, "source": _block_source(block)}
    return found, None


_DISCUSSION_HEADING_RE = re.compile(
    r"^(?:会议)?(?:具体)?讨论(?:内容|详情|纪要|部分)(?:如下)?$"
)


def _is_discussion_heading(block: dict[str, Any]) -> bool:
    raw_text = str(block.get("text") or "").strip()
    if not raw_text or len(raw_text) > 40 or "讨论题目" in raw_text:
        return False
    normalized = re.sub(r"[\s:：、,，。.!！?？()（）\[\]【】_-]+", "", raw_text)
    return bool(_DISCUSSION_HEADING_RE.fullmatch(normalized))


def _find_transcript_start(
    blocks: list[dict[str, Any]], *, meeting_info_table_end: int | None
) -> int:
    """Return the first body block, excluding the discussion-content heading."""
    search_start = meeting_info_table_end + 1 if meeting_info_table_end is not None else 0
    for index in range(search_start, len(blocks)):
        if _is_discussion_heading(blocks[index]):
            return index + 1
    if meeting_info_table_end is not None:
        return meeting_info_table_end + 1
    return 0


def _without_policy_column(block: dict[str, Any]) -> dict[str, Any]:
    if block.get("block_type") != "table":
        return block
    rows = _table_rows(block)
    policy_column = next(
        (
            cell_index
            for row in rows
            for cell_index, cell in enumerate(row)
            if _normalize_info_label(cell) == _normalize_info_label("政策指引")
        ),
        None,
    )
    if policy_column is None:
        return block
    cleaned_rows = [
        row[:policy_column] + row[policy_column + 1 :]
        for row in rows
        if len(row) > policy_column
    ]
    markdown = clean_table_markdown(table_rows_to_markdown(cleaned_rows))
    return {
        **block,
        "text": markdown,
        "table_markdown": markdown,
        "table_rows": cleaned_rows,
        "content_hash": hashlib.sha256(markdown.encode()).hexdigest(),
    }


def prepare_transcript_blocks(
    blocks: list[dict[str, Any]], *, transcript_start_index: int
) -> list[dict[str, Any]]:
    """Build the editable body while retaining complete immutable source blocks."""
    source_blocks = blocks[transcript_start_index:] if transcript_start_index else blocks
    return [_without_policy_column(block) for block in source_blocks]


def extract_deterministic_metadata(
    blocks: list[dict[str, Any]], *, filename: str
) -> dict[str, Any]:
    """Extract metadata without an LLM or any non-deterministic dependency."""

    labeled, meeting_info_table_end = _extract_labeled_values(blocks)
    title = labeled.get("title", {}).get("value")
    title_source = labeled.get("title", {}).get("source", [])
    result = {
        "title": title[:500] if title else None,
        "title_source": title_source,
        "title_confidence_label": "高置信度" if title_source else "建议确认",
        "meeting_purpose": labeled.get("meeting_purpose", {}).get("value"),
        "meeting_purpose_source": labeled.get("meeting_purpose", {}).get("source", []),
        "discussion_topics": labeled.get("discussion_topics", {}).get("value"),
        "discussion_topics_source": labeled.get("discussion_topics", {}).get("source", []),
        "meeting_date": labeled.get("meeting_date", {}).get("value"),
        "meeting_date_source": labeled.get("meeting_date", {}).get("source", []),
        "advisor_selection_criteria": labeled.get("advisor_selection_criteria", {}).get(
            "value"
        ),
        "advisor_selection_criteria_source": labeled.get(
            "advisor_selection_criteria", {}
        ).get("source", []),
        "advisor_names": labeled.get("advisor_names", {}).get("value"),
        "advisor_names_source": labeled.get("advisor_names", {}).get("source", []),
        "internal_attendees": labeled.get("internal_attendees", {}).get("value"),
        "internal_attendees_source": labeled.get("internal_attendees", {}).get("source", []),
        "recorder": labeled.get("recorder", {}).get("value"),
        "recorder_source": labeled.get("recorder", {}).get("source", []),
        "transcript_start_index": _find_transcript_start(
            blocks, meeting_info_table_end=meeting_info_table_end
        ),
    }
    for field_name in _MEETING_INFO_ALIASES:
        source = result.get(f"{field_name}_source", [])
        result[f"{field_name}_confidence_label"] = (
            "高置信度" if source else "无法可靠识别"
        )
    # Keep the existing meeting envelope populated only from explicit fields.
    result["topic"] = result["discussion_topics"]
    result["description"] = result["meeting_purpose"]
    return result


async def _get_import(session: AsyncSession, import_id: UUID) -> MeetingImport:
    item = await session.scalar(
        select(MeetingImport)
        .where(MeetingImport.id == import_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if item is None:
        raise AppException(404, "meeting_import_not_found", "导入任务不存在")
    return item


async def _set_status(
    session: AsyncSession,
    item: MeetingImport,
    status: MeetingImportStatus,
    *,
    step: str,
    attempt_token: UUID,
    lease_seconds: int,
) -> bool:
    # Cancellation is terminal and must win races with worker updates.
    locked_item = await _get_import(session, item.id)
    if locked_item.attempt_token != attempt_token or locked_item.status in {
        MeetingImportStatus.CANCELLED,
        MeetingImportStatus.READY_FOR_REVIEW,
    }:
        return False
    if locked_item.cancel_requested or status is MeetingImportStatus.CANCELLED:
        locked_item.status = MeetingImportStatus.CANCELLED
        locked_item.current_step = "cancelled"
        locked_item.can_retry = False
        locked_item.attempt_token = None
        locked_item.lease_expires_at = None
        await session.commit()
        return False
    locked_item.status = status
    locked_item.current_step = step
    locked_item.lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
    await session.commit()
    return True


async def _claim_import(
    session: AsyncSession,
    import_id: UUID,
    *,
    attempt_token: UUID,
    lease_seconds: int,
) -> MeetingImport | None:
    item = await _get_import(session, import_id)
    now = datetime.now(timezone.utc)
    if item.status in {
        MeetingImportStatus.CANCELLED,
        MeetingImportStatus.READY_FOR_REVIEW,
        MeetingImportStatus.CONFIRMED,
    }:
        return None
    lease_active = item.lease_expires_at is not None and item.lease_expires_at > now
    if item.status is not MeetingImportStatus.UPLOADED and lease_active:
        return None
    item.status = MeetingImportStatus.PARSING
    item.current_step = "parse"
    item.can_retry = False
    item.attempt_token = attempt_token
    item.lease_expires_at = now + timedelta(seconds=lease_seconds)
    await session.commit()
    return item


async def _heartbeat_import(
    factory: async_sessionmaker[AsyncSession],
    import_id: UUID,
    *,
    attempt_token: UUID,
    lease_seconds: int,
) -> None:
    interval = max(5, min(60, lease_seconds // 3))
    while True:
        await asyncio.sleep(interval)
        async with factory() as session:
            await session.execute(
                update(MeetingImport)
                .where(
                    MeetingImport.id == import_id,
                    MeetingImport.attempt_token == attempt_token,
                    MeetingImport.status.in_(
                        (
                            MeetingImportStatus.PARSING,
                            MeetingImportStatus.EXTRACTING_METADATA,
                        )
                    ),
                    MeetingImport.cancel_requested.is_(False),
                )
                .values(
                    lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
                )
            )
            await session.commit()


async def run_meeting_import_async(import_id: str) -> dict[str, Any]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    attempt_token = uuid4()
    heartbeat: asyncio.Task[None] | None = None
    try:
        async with factory() as session:
            item = await _claim_import(
                session,
                UUID(import_id),
                attempt_token=attempt_token,
                lease_seconds=settings.meeting_import_stale_seconds,
            )
            if item is None:
                return {"import_id": import_id, "status": "not_claimed"}
            heartbeat = asyncio.create_task(
                _heartbeat_import(
                    factory,
                    item.id,
                    attempt_token=attempt_token,
                    lease_seconds=settings.meeting_import_stale_seconds,
                )
            )

            document = await session.scalar(
                select(Document).where(
                    Document.id == item.document_id,
                    Document.organization_id == item.organization_id,
                    Document.knowledge_base_id == item.knowledge_base_id,
                    Document.deleted_at.is_(None),
                )
            )
            if document is None:
                raise AppException(404, "document_not_found", "关联文档不存在")

            existing_blocks = (
                await session.scalars(
                    select(DocumentBlock)
                    .where(DocumentBlock.document_id == document.id)
                    .order_by(DocumentBlock.order)
                )
            ).all()
            blocks: list[dict[str, Any]]
            if existing_blocks:
                blocks = [
                    {
                        "block_id": block.block_id,
                        "block_type": block.block_type,
                        "order": block.order,
                        "heading_path": block.heading_path,
                        "text": block.text,
                        "table_markdown": block.table_markdown,
                        "page_number": block.page_number,
                        "slide_number": block.slide_number,
                        "speaker": block.speaker,
                        "start_ms": block.start_ms,
                        "end_ms": block.end_ms,
                        "bbox": block.bbox,
                        "content_hash": block.content_hash,
                    }
                    for block in existing_blocks
                ]
            else:
                content = await ObjectStorage().get(document.minio_object_key)
                blocks = await parse_document_bytes(
                    content, document.safe_filename, document.source_type
                )
                if not blocks:
                    raise AppException(422, "no_document_content", "文档没有可解析内容")

            if not await _set_status(
                session,
                item,
                MeetingImportStatus.EXTRACTING_METADATA,
                step="extract_metadata",
                attempt_token=attempt_token,
                lease_seconds=settings.meeting_import_stale_seconds,
            ):
                return {"import_id": import_id, "status": item.status.value}
            metadata = extract_deterministic_metadata(blocks, filename=item.safe_filename)

            # Insert all immutable blocks in one transaction only after parsing the
            # complete source. Existing documents are never rewritten.
            locked_item = await _get_import(session, item.id)
            if locked_item.attempt_token != attempt_token:
                await session.rollback()
                return {"import_id": import_id, "status": "superseded"}
            if locked_item.cancel_requested or locked_item.status is MeetingImportStatus.CANCELLED:
                locked_item.status = MeetingImportStatus.CANCELLED
                locked_item.current_step = "cancelled"
                locked_item.can_retry = False
                locked_item.attempt_token = None
                locked_item.lease_expires_at = None
                await session.commit()
                return {"import_id": import_id, "status": locked_item.status.value}
            if not existing_blocks:
                for block in blocks:
                    session.add(
                        DocumentBlock(
                            document_id=document.id,
                            block_id=block["block_id"],
                            block_type=block.get("block_type", "paragraph"),
                            order=block["order"],
                            heading_path=block.get("heading_path", []),
                            text=block.get("text", ""),
                            table_markdown=block.get("table_markdown"),
                            page_number=block.get("page_number"),
                            slide_number=block.get("slide_number"),
                            speaker=block.get("speaker"),
                            start_ms=block.get("start_ms"),
                            end_ms=block.get("end_ms"),
                            bbox=block.get("bbox"),
                            content_hash=block["content_hash"],
                        )
                    )
            # A review draft is a copy of the immutable source.  The source
            # DocumentBlock rows are never mutated by review edits.
            existing_revision = await session.scalar(
                select(TranscriptRevision)
                .where(TranscriptRevision.document_id == document.id)
                .order_by(TranscriptRevision.version.desc())
            )
            if existing_revision is None:
                revision = TranscriptRevision(
                    document_id=document.id,
                    import_id=item.id,
                    version=1,
                    status=TranscriptRevisionStatus.DRAFT,
                    created_by=item.created_by,
                )
                session.add(revision)
                await session.flush()
                # The immutable DocumentBlocks retain the complete original
                # file. The editable/confirmable transcript starts after the
                # meeting-information section's discussion-content heading.
                transcript_start = int(metadata.get("transcript_start_index", 0) or 0)
                source_blocks = prepare_transcript_blocks(
                    blocks, transcript_start_index=transcript_start
                )
                for source in source_blocks:
                    session.add(
                        TranscriptRevisionBlock(
                            revision_id=revision.id,
                            block_id=source["block_id"],
                            block_type=source.get("block_type", "paragraph"),
                            order=source["order"],
                            heading_path=source.get("heading_path", []),
                            text=source.get("text", ""),
                            table_markdown=source.get("table_markdown"),
                            page_number=source.get("page_number"),
                            slide_number=source.get("slide_number"),
                            speaker=source.get("speaker"),
                            start_ms=source.get("start_ms"),
                            end_ms=source.get("end_ms"),
                            bbox=source.get("bbox"),
                            content_hash=source["content_hash"],
                        )
                    )
            else:
                revision = existing_revision
            locked_item.metadata_json = metadata
            locked_item.status = MeetingImportStatus.READY_FOR_REVIEW
            locked_item.current_step = "ready_for_review"
            locked_item.can_retry = False
            locked_item.failure_code = locked_item.failure_message = None
            locked_item.attempt_token = None
            locked_item.lease_expires_at = None
            # Blocks are ready. Enqueue a revision-scoped vector-only job in the
            # same durable transaction; dispatch happens only after commit below.
            vector_job, _created = await ensure_vectorization_job(
                session, locked_item, revision, document
            )
            document.vector_sync_status = "PENDING"
            # no chunks, embeddings or knowledge drafts exist until vector-only job runs.
            if document.status in {
                "UPLOADED",
                "PARSING",
                "PARSED",
                "FAILED",
            }:
                document.status = "PARSED"
            await session.commit()
            try:
                from app.worker.celery_app import celery_app

                celery_app.send_task("app.worker.tasks.run_ingestion", args=[vector_job.job_id])
            except Exception:
                # The durable QUEUED marker is enough for the scheduler/operator
                # to retry dispatch without losing the parsed revision.
                pass
            return {
                "import_id": import_id,
                "status": locked_item.status.value,
                "metadata": metadata,
            }
    except Exception as exc:
        async with factory() as session:
            failed_item = await session.scalar(
                select(MeetingImport).where(MeetingImport.id == UUID(import_id)).with_for_update()
            )
            if failed_item is not None:
                if (
                    failed_item.attempt_token == attempt_token
                    and failed_item.status is not MeetingImportStatus.CANCELLED
                    and not failed_item.cancel_requested
                ):
                    failed_item.status = MeetingImportStatus.FAILED
                    failed_item.current_step = "failed"
                    failed_item.failure_code = (
                        exc.code if isinstance(exc, AppException) else "meeting_import_failed"
                    )
                    failed_item.failure_message = (
                        exc.message
                        if isinstance(exc, AppException)
                        else "解析服务暂时不可用，请稍后重试"
                    )
                    failed_item.can_retry = True
                    failed_item.attempt_token = None
                    failed_item.lease_expires_at = None
                    await session.commit()
        raise
    finally:
        if heartbeat is not None:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
        await engine.dispose()


async def _prepare_automatic_retry(import_id: str) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            item = await session.scalar(
                select(MeetingImport).where(MeetingImport.id == UUID(import_id)).with_for_update()
            )
            if (
                item is not None
                and item.status is MeetingImportStatus.FAILED
                and not item.cancel_requested
            ):
                item.status = MeetingImportStatus.UPLOADED
                item.current_step = "automatic_retry_queued"
                item.failure_code = item.failure_message = None
                item.can_retry = False
                item.attempt_token = None
                item.lease_expires_at = None
                await session.commit()
    finally:
        await engine.dispose()


async def _reconcile_stale_imports() -> list[str]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    recovered: list[str] = []
    try:
        async with factory() as session:
            now = datetime.now(timezone.utc)
            items = (
                await session.scalars(
                    select(MeetingImport)
                    .where(
                        or_(
                            and_(
                                MeetingImport.status == MeetingImportStatus.UPLOADED,
                                MeetingImport.updated_at <= now - timedelta(seconds=60),
                            ),
                            and_(
                                MeetingImport.status.in_(
                                    (
                                        MeetingImportStatus.PARSING,
                                        MeetingImportStatus.EXTRACTING_METADATA,
                                    )
                                ),
                                MeetingImport.lease_expires_at.is_not(None),
                                MeetingImport.lease_expires_at <= now,
                            ),
                        ),
                        MeetingImport.cancel_requested.is_(False),
                    )
                    .limit(50)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for item in items:
                item.status = MeetingImportStatus.UPLOADED
                item.current_step = "recovery_queued"
                item.can_retry = False
                item.attempt_token = None
                item.lease_expires_at = None
                recovered.append(str(item.id))
            await session.commit()
    finally:
        await engine.dispose()
    return recovered


@celery_app.task(name="app.worker.tasks.reconcile_meeting_imports")  # type: ignore[untyped-decorator]
def reconcile_meeting_imports() -> dict[str, int]:
    recovered = asyncio.run(_reconcile_stale_imports())
    for import_id in recovered:
        celery_app.send_task("app.worker.tasks.run_meeting_import", args=[import_id])
    return {"recovered": len(recovered)}


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True, max_retries=5, name="app.worker.tasks.run_meeting_import"
)
def run_meeting_import(self: Task, import_id: str) -> dict[str, Any]:
    try:
        return asyncio.run(run_meeting_import_async(import_id))
    except Exception as exc:
        if self.request.retries < self.max_retries and not isinstance(exc, AppException):
            asyncio.run(_prepare_automatic_retry(import_id))
            raise self.retry(exc=exc, countdown=min(300, 2 ** (self.request.retries + 1))) from exc
        raise
