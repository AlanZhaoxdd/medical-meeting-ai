from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, Header, UploadFile, status
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthContext, CurrentUserDependency, require_kb_access, require_role
from app.core.config import get_settings
from app.core.exceptions import AppException, ConflictError, ForbiddenError, NotFoundError
from app.db.session import get_session
from app.ingestion.validation import ALLOWED_INPUTS, safe_filename, validate_upload
from app.models.kb import (
    BatchReplaceOperation,
    Chunk,
    Document,
    DocumentBlock,
    IngestionJob,
    MeetingImport,
    MeetingImportStatus,
    OutboxEvent,
    TranscriptRevision,
    TranscriptRevisionBlock,
    TranscriptRevisionStatus,
)
from app.models.meeting import AiTask, Meeting
from app.schemas.kb import DocumentStatus, Role
from app.schemas.meeting_import import (
    ConfirmRead,
    ConfirmRequest,
    FindRequest,
    MeetingImportConfig,
    MeetingImportRead,
    MeetingMetadataPatch,
    ReplaceRequest,
    ReviewRead,
    RevisionBlockRead,
    RevisionPatch,
    RevisionRead,
    SourceRefRead,
    UndoRequest,
    VectorizationRead,
    VectorizationReadResponse,
    VectorizeRequest,
)
from app.services.audit import record_audit
from app.services.question_generation import thread_id
from app.services.storage import ObjectStorage
from app.worker.meeting_import import document_lock_key, ensure_vectorization_job, vector_job_id

router = APIRouter(tags=["会议导入"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
EditorDependency = Annotated[AuthContext, Depends(require_role(Role.EDITOR))]
ACTIVE_IMPORT_STATUSES = (
    MeetingImportStatus.UPLOADED,
    MeetingImportStatus.PARSING,
    MeetingImportStatus.EXTRACTING_METADATA,
)


def _advisory_lock_key(sha256: str) -> int:
    value = int(sha256[:16], 16)
    return value - 2**64 if value >= 2**63 else value


def _document_has_derived_state(
    document: Document, *, has_ingestion_job: bool, has_chunks: bool
) -> bool:
    return has_ingestion_job or has_chunks or document.status == DocumentStatus.PUBLISHED.value


async def _lock_document_vector_state(session: AsyncSession, document_id: UUID) -> None:
    """Serialize revision mutations with same-document chunk/vector publication."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": document_lock_key(document_id)},
    )


async def _active_import_for_document(
    session: AsyncSession,
    *,
    document_id: UUID,
    organization_id: UUID,
) -> MeetingImport | None:
    item: MeetingImport | None = await session.scalar(
        select(MeetingImport)
        .where(
            MeetingImport.document_id == document_id,
            MeetingImport.organization_id == organization_id,
            MeetingImport.status.in_(ACTIVE_IMPORT_STATUSES),
        )
        .order_by(MeetingImport.created_at.desc())
    )
    return item


def serialize_meeting_import(item: MeetingImport) -> MeetingImportRead:
    failure = None
    if item.failure_code or item.failure_message:
        failure = {
            "code": item.failure_code,
            "message": item.failure_message,
            "displayable": item.failure_message or "导入失败，请重试",
        }
    return MeetingImportRead(
        import_id=str(item.id),
        org_id=str(item.organization_id),
        kb_id=str(item.knowledge_base_id),
        organization_id=str(item.organization_id),
        knowledge_base_id=str(item.knowledge_base_id),
        document_id=str(item.document_id),
        file={
            "filename": item.filename,
            "safe_filename": item.safe_filename,
            "mime_type": item.mime_type,
            "sha256": item.sha256,
            "size_bytes": item.size_bytes,
        },
        status=item.status,
        current_step=item.current_step,
        progress={
            MeetingImportStatus.UPLOADED: 20,
            MeetingImportStatus.PARSING: 55,
            MeetingImportStatus.EXTRACTING_METADATA: 85,
            MeetingImportStatus.READY_FOR_REVIEW: 100,
            MeetingImportStatus.FAILED: 100,
            MeetingImportStatus.CANCELLED: 100,
            MeetingImportStatus.CONFIRMED: 100,
        }[item.status],
        failure=failure,
        can_retry=bool(item.can_retry),
        created_at=item.created_at,
        updated_at=item.updated_at,
        metadata=item.metadata_json or {},
    )


def _config() -> MeetingImportConfig:
    settings = get_settings()
    mime_types = {suffix: sorted(mimes) for suffix, mimes in ALLOWED_INPUTS.items()}
    return MeetingImportConfig(
        max_upload_bytes=settings.max_upload_bytes,
        allowed_extensions=sorted(ALLOWED_INPUTS),
        allowed_mime_types=sorted({mime for mimes in ALLOWED_INPUTS.values() for mime in mimes}),
        mime_types=mime_types,
        statuses=list(MeetingImportStatus),
    )


def _dispatch_import(import_id: UUID) -> None:
    from app.worker.celery_app import celery_app

    celery_app.send_task("app.worker.tasks.run_meeting_import", args=[str(import_id)])


@router.get("/meeting-imports/config", response_model=MeetingImportConfig)
async def get_meeting_import_config(current: CurrentUserDependency) -> MeetingImportConfig:
    return _config()


@router.get("/knowledge-bases/{kb_id}/meeting-imports/config", response_model=MeetingImportConfig)
async def get_kb_meeting_import_config(
    kb_id: UUID, session: SessionDependency, current: CurrentUserDependency
) -> MeetingImportConfig:
    await require_kb_access(session, current, kb_id)
    return _config()


async def _upload_import(
    *,
    kb_id: UUID,
    session: AsyncSession,
    current: AuthContext,
    file: UploadFile | None,
    document_id: UUID | None,
    confirm_duplicate: bool,
    associate_existing: bool,
) -> MeetingImportRead:
    kb = await require_kb_access(session, current, kb_id)
    settings = get_settings()
    content = b""
    original_name = ""
    mime_type = "application/octet-stream"
    if file is not None:
        original_name = file.filename or ""
        sanitized = safe_filename(original_name)
        chunks: list[bytes] = []
        total = 0
        while data := await file.read(1024 * 1024):
            total += len(data)
            if total > settings.max_upload_bytes:
                raise AppException(
                    413, "file_too_large", f"文件超过 {settings.max_upload_bytes} 字节限制"
                )
            chunks.append(data)
        content = b"".join(chunks)
        mime_type = file.content_type or "application/octet-stream"
        sha256 = validate_upload(original_name, mime_type, content, settings.max_upload_bytes)
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": _advisory_lock_key(sha256)},
        )
        # Legal JSON is checked synchronously; schema/business validation remains
        # in the parser worker so malformed segments become a displayable failure.
        if Path(sanitized).suffix.lower() == ".json":
            try:
                json.loads(content)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise AppException(422, "invalid_json", "JSON 文件格式无效") from exc
    elif document_id is None:
        raise AppException(422, "file_or_document_required", "请上传文件或提供 document_id")
    else:
        sha256 = "linked-document"
        sanitized = "linked-document"

    document: Document | None = None
    active_import: MeetingImport | None = None
    if document_id is not None:
        document = await session.scalar(
            select(Document).where(
                Document.id == document_id,
                Document.organization_id == current.organization_id,
                Document.knowledge_base_id == kb_id,
                Document.deleted_at.is_(None),
            )
        )
        if document is None:
            raise NotFoundError("文档", "document_not_found")
        if file is not None and not (confirm_duplicate or associate_existing):
            raise ConflictError(
                "document_id_with_file_requires_confirmation",
                "同时提供 document_id 和文件时必须确认关联",
                {"document_id": str(document.id), "actions": ["associate_existing"]},
            )
        active_import = await _active_import_for_document(
            session,
            document_id=document.id,
            organization_id=current.organization_id,
        )
    elif file is not None:
        document = await session.scalar(
            select(Document)
            .where(
                Document.organization_id == current.organization_id,
                Document.knowledge_base_id == kb_id,
                Document.sha256 == sha256,
                Document.deleted_at.is_(None),
            )
            .order_by(Document.version.desc())
        )
        if document is not None:
            active_import = await _active_import_for_document(
                session,
                document_id=document.id,
                organization_id=current.organization_id,
            )
            if not (confirm_duplicate or associate_existing):
                raise ConflictError(
                    "duplicate_document",
                    "检测到相同原件，确认后可关联既有文档",
                    {
                        "duplicate_document_id": str(document.id),
                        "existing_document_id": str(document.id),
                        "existing_import_id": (
                            str(active_import.id) if active_import is not None else None
                        ),
                        "sha256": sha256,
                        "actions": ["associate_existing", "confirm_duplicate"],
                    },
                )

    if active_import is not None:
        return serialize_meeting_import(active_import)

    confirmed_import = (
        await session.scalar(
            select(MeetingImport).where(
                MeetingImport.document_id == document.id,
                MeetingImport.organization_id == current.organization_id,
                MeetingImport.status == MeetingImportStatus.CONFIRMED,
            )
        )
        if document is not None
        else None
    )
    if confirmed_import is not None:
        raise ConflictError(
            "document_already_confirmed",
            "该原件已建立正式会议，不能再次创建导入草稿",
            {
                "existing_import_id": str(confirmed_import.id),
                "meeting_id": (
                    str(confirmed_import.meeting_id) if confirmed_import.meeting_id else None
                ),
            },
        )

    if document is not None:
        derived_job = await session.scalar(
            select(IngestionJob.id).where(IngestionJob.document_id == document.id).limit(1)
        )
        derived_chunk = await session.scalar(
            select(Chunk.chunk_id).where(Chunk.document_id == document.id).limit(1)
        )
        if _document_has_derived_state(
            document,
            has_ingestion_job=derived_job is not None,
            has_chunks=derived_chunk is not None,
        ):
            raise ConflictError(
                "document_has_derived_state",
                "该文档已进入知识库处理，不能作为会议导入草稿复用，请重新上传原件",
                {"document_id": str(document.id)},
            )

    created_document = False
    if document is None:
        # A default template is not needed by meeting import. Keep the legacy
        # Document contract satisfied with its configured template or a stable
        # generated marker; no template contents are consulted by this flow.
        template_id = kb.default_template_id or uuid4()
        document_id_new = uuid4()
        suffix = Path(sanitized).suffix.lower()
        document = Document(
            id=document_id_new,
            organization_id=current.organization_id,
            knowledge_base_id=kb_id,
            filename=original_name,
            safe_filename=sanitized,
            mime_type=mime_type,
            source_type="transcript" if suffix == ".json" else "document",
            minio_bucket=settings.minio_bucket,
            minio_object_key=(
                f"org/{current.organization_id}/kb/{kb_id}/meeting-imports/{document_id_new}/{sanitized}"
            ),
            sha256=sha256,
            version=1,
            parser_name="meeting-import",
            parser_version="1",
            template_id=template_id,
            template_version=1,
            status=DocumentStatus.UPLOADED.value,
            vector_sync_status="PENDING",
            created_by=current.user_id,
        )
        session.add(document)
        created_document = True
        await session.flush()
        try:
            await ObjectStorage().put(document.minio_object_key, content, mime_type)
        except Exception as exc:
            await session.rollback()
            raise AppException(
                503, "object_storage_unavailable", "原件保存失败，请稍后重试"
            ) from exc

    uploaded_object_key = document.minio_object_key if created_document else None
    try:
        item = MeetingImport(
            organization_id=current.organization_id,
            knowledge_base_id=kb_id,
            document_id=document.id,
            filename=document.filename,
            safe_filename=document.safe_filename,
            mime_type=document.mime_type,
            sha256=document.sha256,
            size_bytes=len(content) if content else 0,
            status=MeetingImportStatus.UPLOADED,
            current_step="upload",
            metadata_json={},
            can_retry=False,
            created_by=current.user_id,
        )
        session.add(item)
        await session.flush()
        await record_audit(
            session,
            organization_id=current.organization_id,
            actor_id=current.user_id,
            action="meeting_import.create",
            resource_type="meeting_import",
            resource_id=str(item.id),
            metadata={"document_id": str(document.id), "knowledge_base_id": str(kb_id)},
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        existing = await _active_import_for_document(
            session,
            document_id=document.id,
            organization_id=current.organization_id,
        )
        if existing is not None:
            return serialize_meeting_import(existing)
        if uploaded_object_key:
            try:
                await ObjectStorage().delete(uploaded_object_key)
            except Exception:
                pass
        raise ConflictError("meeting_import_conflict", "导入任务已存在，请刷新后继续") from exc
    except Exception:
        await session.rollback()
        if uploaded_object_key:
            try:
                await ObjectStorage().delete(uploaded_object_key)
            except Exception:
                pass
        raise
    await session.refresh(item)
    try:
        _dispatch_import(item.id)
    except Exception:
        # Upload is durable even if the broker is temporarily unavailable. GET
        # callers can retry once the worker is back.
        pass
    return serialize_meeting_import(item)


@router.post(
    "/meeting-imports", response_model=MeetingImportRead, status_code=status.HTTP_202_ACCEPTED
)
async def create_meeting_import(
    session: SessionDependency,
    current: EditorDependency,
    knowledge_base_id: UUID | None = Form(default=None),
    kb_id: UUID | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    document_id: UUID | None = Form(default=None),
    confirm_duplicate: bool = Form(default=False),
    associate_existing: bool = Form(default=False),
) -> MeetingImportRead:
    selected_kb_id = knowledge_base_id or kb_id
    if selected_kb_id is None:
        raise AppException(422, "knowledge_base_required", "请提供 knowledge_base_id")
    return await _upload_import(
        kb_id=selected_kb_id,
        session=session,
        current=current,
        file=file,
        document_id=document_id,
        confirm_duplicate=confirm_duplicate,
        associate_existing=associate_existing,
    )


@router.post(
    "/knowledge-bases/{kb_id}/meeting-imports",
    response_model=MeetingImportRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_kb_meeting_import(
    kb_id: UUID,
    session: SessionDependency,
    current: EditorDependency,
    file: UploadFile | None = File(default=None),
    document_id: UUID | None = Form(default=None),
    confirm_duplicate: bool = Form(default=False),
    associate_existing: bool = Form(default=False),
) -> MeetingImportRead:
    return await _upload_import(
        kb_id=kb_id,
        session=session,
        current=current,
        file=file,
        document_id=document_id,
        confirm_duplicate=confirm_duplicate,
        associate_existing=associate_existing,
    )


async def _get_import(
    session: AsyncSession,
    current: AuthContext,
    import_id: UUID,
    *,
    for_update: bool = False,
) -> MeetingImport:
    statement = select(MeetingImport).where(
        MeetingImport.id == import_id,
        MeetingImport.organization_id == current.organization_id,
    )
    if for_update:
        statement = statement.with_for_update()
    item = await session.scalar(statement)
    if item is None:
        raise NotFoundError("导入任务", "meeting_import_not_found")
    return item


@router.get("/meeting-imports/{import_id}", response_model=MeetingImportRead)
@router.get("/meeting-imports/{import_id}/status", response_model=MeetingImportRead)
async def get_meeting_import(
    import_id: UUID, session: SessionDependency, current: CurrentUserDependency
) -> MeetingImportRead:
    item = await _get_import(session, current, import_id)
    # GET doubles as a safe recovery point after a broker/network timeout. The
    # worker is idempotent and terminal states are no-ops.
    now = datetime.now(timezone.utc)
    if (
        item.status in ACTIVE_IMPORT_STATUSES
        and item.lease_expires_at is not None
        and item.lease_expires_at <= now
    ):
        item = await _get_import(session, current, import_id, for_update=True)
        now = datetime.now(timezone.utc)
        if (
            item.status in ACTIVE_IMPORT_STATUSES
            and item.lease_expires_at is not None
            and item.lease_expires_at <= now
        ):
            item.status = MeetingImportStatus.UPLOADED
            item.current_step = "recovery_queued"
            item.can_retry = False
            item.attempt_token = None
            item.lease_expires_at = None
            await session.commit()
            await session.refresh(item)
    if item.status is MeetingImportStatus.UPLOADED:
        try:
            _dispatch_import(item.id)
        except Exception:
            pass
    return serialize_meeting_import(item)


@router.post(
    "/meeting-imports/{import_id}/retry",
    response_model=MeetingImportRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_meeting_import(
    import_id: UUID, session: SessionDependency, current: EditorDependency
) -> MeetingImportRead:
    item = await _get_import(session, current, import_id, for_update=True)
    if item.status is not MeetingImportStatus.FAILED or not item.can_retry:
        raise ConflictError("meeting_import_not_retryable", "当前导入任务不可重试")
    item.status = MeetingImportStatus.UPLOADED
    item.current_step = "retry_queued"
    item.failure_code = item.failure_message = None
    item.can_retry = False
    item.cancel_requested = False
    item.attempt_token = None
    item.lease_expires_at = None
    await record_audit(
        session,
        organization_id=current.organization_id,
        actor_id=current.user_id,
        action="meeting_import.retry",
        resource_type="meeting_import",
        resource_id=str(item.id),
    )
    await session.commit()
    await session.refresh(item)
    try:
        _dispatch_import(item.id)
    except Exception:
        pass
    return serialize_meeting_import(item)


@router.post("/meeting-imports/{import_id}/cancel", response_model=MeetingImportRead)
async def cancel_meeting_import(
    import_id: UUID, session: SessionDependency, current: EditorDependency
) -> MeetingImportRead:
    item = await _get_import(session, current, import_id, for_update=True)
    if item.status in {MeetingImportStatus.READY_FOR_REVIEW, MeetingImportStatus.CANCELLED}:
        return serialize_meeting_import(item)
    item.cancel_requested = True
    item.status = MeetingImportStatus.CANCELLED
    item.current_step = "cancelled"
    item.can_retry = False
    item.attempt_token = None
    item.lease_expires_at = None
    await record_audit(
        session,
        organization_id=current.organization_id,
        actor_id=current.user_id,
        action="meeting_import.cancel",
        resource_type="meeting_import",
        resource_id=str(item.id),
    )
    await session.commit()
    await session.refresh(item)
    return serialize_meeting_import(item)


# ---- Transcript review -------------------------------------------------


def _content_hash(text_value: str) -> str:
    return hashlib.sha256(text_value.encode("utf-8")).hexdigest()


def _source_ref(block: TranscriptRevisionBlock | DocumentBlock) -> SourceRefRead:
    return SourceRefRead(
        block_id=block.block_id,
        page_number=block.page_number,
        slide_number=block.slide_number,
        speaker=block.speaker,
        start_ms=block.start_ms,
        end_ms=block.end_ms,
    )


def _serialize_block(block: TranscriptRevisionBlock | DocumentBlock) -> RevisionBlockRead:
    return RevisionBlockRead(
        block_id=block.block_id,
        block_type=block.block_type,
        order=block.order,
        text=block.text,
        heading_path=block.heading_path or [],
        table_markdown=block.table_markdown,
        source_ref=_source_ref(block),
    )


async def _revision_blocks(
    session: AsyncSession, revision_id: UUID
) -> list[TranscriptRevisionBlock]:
    return list(
        (
            await session.scalars(
                select(TranscriptRevisionBlock)
                .where(TranscriptRevisionBlock.revision_id == revision_id)
                .order_by(TranscriptRevisionBlock.order)
            )
        ).all()
    )


def _serialize_revision(
    revision: TranscriptRevision, blocks: list[TranscriptRevisionBlock]
) -> RevisionRead:
    return RevisionRead(
        revision_id=revision.id,
        document_id=revision.document_id,
        version=revision.version,
        revision_number=revision.version,
        status=revision.status.value
        if isinstance(revision.status, TranscriptRevisionStatus)
        else str(revision.status),
        blocks=[_serialize_block(block) for block in blocks],
        created_at=revision.created_at,
        confirmed_at=revision.confirmed_at,
        confirmed_by=revision.confirmed_by,
        created_by=revision.created_by,
    )


async def _get_review_import(
    session: AsyncSession,
    current: AuthContext,
    import_id: UUID,
    *,
    write: bool = False,
    for_update: bool | None = None,
) -> MeetingImport:
    item = await _get_import(
        session, current, import_id, for_update=write if for_update is None else for_update
    )
    if write and ROLE_LEVEL_FOR_REVIEW(current.role) < ROLE_LEVEL_FOR_REVIEW(Role.EDITOR):
        raise ForbiddenError()
    if write and item.status is not MeetingImportStatus.READY_FOR_REVIEW:
        raise ConflictError("import_not_editable", "导入任务当前不可编辑")
    return item


def ROLE_LEVEL_FOR_REVIEW(role: Role) -> int:
    return {Role.VIEWER: 10, Role.REVIEWER: 20, Role.EDITOR: 30, Role.ADMIN: 40, Role.OWNER: 50}[
        role
    ]


async def _current_draft(
    session: AsyncSession, item: MeetingImport, *, for_update: bool = False
) -> TranscriptRevision:
    query = (
        select(TranscriptRevision)
        .where(
            TranscriptRevision.document_id == item.document_id,
            TranscriptRevision.status == TranscriptRevisionStatus.DRAFT,
        )
        .order_by(TranscriptRevision.version.desc())
    )
    if for_update:
        query = query.with_for_update()
    revision = await session.scalar(query)
    if revision is None:
        # Lazy recovery for imports created before draft initialization.
        source = list(
            (
                await session.scalars(
                    select(DocumentBlock)
                    .where(DocumentBlock.document_id == item.document_id)
                    .order_by(DocumentBlock.order)
                )
            ).all()
        )
        if not source:
            raise ConflictError("review_not_ready", "原件尚未解析完成")
        revision = TranscriptRevision(
            document_id=item.document_id,
            import_id=item.id,
            version=1,
            status=TranscriptRevisionStatus.DRAFT,
            created_by=item.created_by,
        )
        session.add(revision)
        await session.flush()
        for block in source:
            session.add(
                TranscriptRevisionBlock(
                    revision_id=revision.id,
                    block_id=block.block_id,
                    block_type=block.block_type,
                    order=block.order,
                    heading_path=block.heading_path or [],
                    text=block.text,
                    table_markdown=block.table_markdown,
                    page_number=block.page_number,
                    slide_number=block.slide_number,
                    speaker=block.speaker,
                    start_ms=block.start_ms,
                    end_ms=block.end_ms,
                    bbox=block.bbox,
                    content_hash=block.content_hash,
                )
            )
        await session.flush()
    return revision


async def _new_revision(
    session: AsyncSession,
    old: TranscriptRevision,
    blocks: list[TranscriptRevisionBlock],
    actor_id: UUID,
) -> tuple[TranscriptRevision, list[TranscriptRevisionBlock]]:
    old.status = TranscriptRevisionStatus.SUPERSEDED
    revision = TranscriptRevision(
        document_id=old.document_id,
        import_id=old.import_id,
        version=old.version + 1,
        status=TranscriptRevisionStatus.DRAFT,
        created_by=actor_id,
    )
    session.add(revision)
    await session.flush()
    new_blocks: list[TranscriptRevisionBlock] = []
    for block in sorted(blocks, key=lambda value: value.order):
        new_block = TranscriptRevisionBlock(
            revision_id=revision.id,
            block_id=block.block_id,
            block_type=block.block_type,
            order=block.order,
            heading_path=block.heading_path or [],
            text=block.text,
            table_markdown=block.table_markdown,
            page_number=block.page_number,
            slide_number=block.slide_number,
            speaker=block.speaker,
            start_ms=block.start_ms,
            end_ms=block.end_ms,
            bbox=block.bbox,
            content_hash=block.content_hash,
        )
        session.add(new_block)
        new_blocks.append(new_block)
    await session.flush()
    return revision, new_blocks


def _match_positions(text_value: str, query: str, case_sensitive: bool) -> list[tuple[int, int]]:
    if case_sensitive:
        return [(match.start(), match.end()) for match in re.finditer(re.escape(query), text_value)]
    lowered, target = text_value.casefold(), query.casefold()
    positions: list[tuple[int, int]] = []
    start = 0
    while target:
        found = lowered.find(target, start)
        if found < 0:
            break
        positions.append((found, found + len(query)))
        start = found + len(target)
    return positions


def _metadata_fields(
    item: MeetingImport, meeting: Meeting | None = None
) -> tuple[dict[str, Any], int, int]:
    raw = dict(item.metadata_json or {})
    user_values = raw.get("user_values", {})
    meeting_values: dict[str, Any] | None = None
    suggestions = {
        "title": raw.get("title"),
        "starts_at": raw.get("starts_at"),
        "ends_at": raw.get("ends_at"),
        "location": raw.get("location"),
        "online_url": raw.get("online_url"),
        "organizer": raw.get("organizer"),
        "topic": raw.get("topic") or ((raw.get("topics") or [None])[0]),
        "description": raw.get("description"),
        "meeting_purpose": raw.get("meeting_purpose") or raw.get("description"),
        "discussion_topics": raw.get("discussion_topics")
        or raw.get("topic")
        or ((raw.get("topics") or [None])[0]),
        "meeting_date": raw.get("meeting_date"),
        "advisor_selection_criteria": raw.get("advisor_selection_criteria"),
        "advisor_names": raw.get("advisor_names"),
        "internal_attendees": raw.get("internal_attendees"),
        "recorder": raw.get("recorder"),
    }
    effective_meeting_date = user_values.get("meeting_date", suggestions.get("meeting_date"))
    has_meeting_date = _meeting_date_bounds(effective_meeting_date) is not None
    # `starts_at`/`ends_at` are legacy storage fields.  A reliable extracted
    # meeting date is sufficient for imported minutes, so do not expose stale
    # parser defaults (often today's date) as selectable times in review.
    if has_meeting_date:
        suggestions["starts_at"] = None
        suggestions["ends_at"] = None
    if meeting is not None:
        meeting_values = {
            "title": meeting.title,
            "starts_at": meeting.starts_at.isoformat(),
            "ends_at": meeting.ends_at.isoformat(),
            "location": meeting.location,
            "online_url": meeting.online_url,
            "organizer": meeting.organizer,
            "topic": meeting.topic,
            "description": meeting.description,
        }
        stored_meeting_info = dict(getattr(meeting, "meeting_info", {}) or {})
        meeting_values.update(
            {
                key: stored_meeting_info[key]
                for key in (
                    "meeting_purpose",
                    "discussion_topics",
                    "meeting_date",
                    "advisor_selection_criteria",
                    "advisor_names",
                    "internal_attendees",
                    "recorder",
                )
                if key in stored_meeting_info
            }
        )
    fields: dict[str, Any] = {}
    for key in (
        "title",
        "starts_at",
        "ends_at",
        "location",
        "online_url",
        "organizer",
        "topic",
        "description",
        "meeting_purpose",
        "discussion_topics",
        "meeting_date",
        "advisor_selection_criteria",
        "advisor_names",
        "internal_attendees",
        "recorder",
    ):
        value = (
            meeting_values.get(key)
            if meeting_values is not None and key in meeting_values
            else user_values.get(key, suggestions.get(key))
        )
        source = raw.get(f"{key}_source", [])
        if not isinstance(source, list):
            source = []
        confidence = raw.get(f"{key}_confidence", raw.get("confidence"))
        confidence_label = raw.get(f"{key}_confidence_label", raw.get("confidence_label"))
        if confidence_label is None:
            if isinstance(confidence, (int, float)) and confidence >= 0.85:
                confidence_label = "高置信度"
            elif value in (None, ""):
                confidence_label = "无法可靠识别"
            else:
                confidence_label = "建议确认"
        time_required = key in {"starts_at", "ends_at"} and not has_meeting_date
        required_value_missing = value in (None, "") and key not in {"starts_at", "ends_at"}
        confidence_needs_review = confidence_label != "高置信度" and not (
            has_meeting_date and key in {"starts_at", "ends_at"}
        )
        needs_confirmation = (
            meeting_values is None
            and key not in user_values
            and (
                required_value_missing or key == "title" or time_required or confidence_needs_review
            )
        )
        fields[key] = {
            "value": value,
            "suggested_value": suggestions.get(key),
            "confidence": confidence,
            "confidence_label": confidence_label,
            "source": source,
            "needs_confirmation": needs_confirmation,
            "user_modified": key in user_values
            or (meeting_values is not None and value != suggestions.get(key)),
        }
    return (
        fields,
        int(raw.get("metadata_version", 1)),
        sum(1 for value in fields.values() if value["needs_confirmation"]),
    )


async def _serialize_review(session: AsyncSession, item: MeetingImport) -> ReviewRead:
    document = await session.get(Document, item.document_id)
    if document is None:
        raise NotFoundError("文档", "document_not_found")
    source_blocks = list(
        (
            await session.scalars(
                select(DocumentBlock)
                .where(DocumentBlock.document_id == document.id)
                .order_by(DocumentBlock.order)
            )
        ).all()
    )
    revisions = list(
        (
            await session.scalars(
                select(TranscriptRevision)
                .where(TranscriptRevision.document_id == document.id)
                .order_by(TranscriptRevision.version)
            )
        ).all()
    )
    serialized = [_serialize_revision(revision, []) for revision in revisions]
    current_model = next(
        (
            revision
            for revision in reversed(revisions)
            if revision.status is TranscriptRevisionStatus.DRAFT
        ),
        None,
    )
    current = (
        _serialize_revision(current_model, await _revision_blocks(session, current_model.id))
        if current_model is not None
        else None
    )
    meeting = await session.get(Meeting, item.meeting_id) if item.meeting_id else None
    metadata, metadata_version, needs_count = _metadata_fields(item, meeting)
    vector_job = None
    if current_model is not None:
        vector_job = await session.scalar(
            select(IngestionJob).where(
                IngestionJob.job_id
                == vector_job_id(item.id, current_model.id, current_model.version)
            )
        )
    vectorization = _vectorization_status(item, document, current_model, vector_job)
    return ReviewRead(
        import_id=item.id,
        organization_id=item.organization_id,
        knowledge_base_id=item.knowledge_base_id,
        document={
            "id": document.id,
            "meeting_id": document.meeting_id,
            "status": document.status,
        },
        file={
            "filename": item.filename,
            "safe_filename": item.safe_filename,
            "mime_type": item.mime_type,
            "sha256": item.sha256,
            "size_bytes": item.size_bytes,
        },
        status=item.status.value,
        meeting_id=item.meeting_id,
        original_blocks=[_serialize_block(block) for block in source_blocks],
        current_revision=current,
        revision_history=serialized,
        metadata=metadata,
        metadata_version=metadata_version,
        needs_confirmation_count=needs_count,
        vectorization=vectorization,
    )


def _vectorization_status(
    item: MeetingImport,
    document: Document,
    revision: TranscriptRevision | None,
    job: IngestionJob | None,
) -> VectorizationRead:
    revision_id = revision.id if revision is not None else None
    if revision is None:
        return VectorizationRead(status="PENDING", revision_id=None)
    if job is None:
        return VectorizationRead(
            status="STALE", revision_id=revision_id,
            current_revision_version=revision.version if revision else None,
            retryable=True,
        )
    if (
        (job.result_summary or {}).get("revision_id") != str(revision_id)
        or (
            revision is not None
            and (job.result_summary or {}).get("revision_version") != revision.version
        )
    ):
        return VectorizationRead(
            status="STALE", revision_id=revision_id,
            current_revision_version=revision.version if revision else None,
            vectorized_revision_version=(job.result_summary or {}).get("revision_version"),
            retryable=True,
        )
    status = job.status
    state: Literal["PENDING", "RUNNING", "SYNCED", "STALE", "FAILED"]
    if status == "COMPLETED":
        state = "SYNCED"
    elif status == "FAILED":
        state = "FAILED"
    elif status in {"RUNNING", "WAITING_REVIEW"}:
        state = "RUNNING"
    else:
        state = "PENDING"
    vectorized_version = (job.result_summary or {}).get("revision_version")
    return VectorizationRead(
        job_id=job.job_id,
        status=state,
        revision_id=revision_id,
        current_revision_version=revision.version if revision else None,
        vectorized_revision_version=vectorized_version,
        current_node=job.current_node,
        progress=job.progress,
        error_code=job.error_code,
        error_message=job.error_message,
        error=(
            {"code": job.error_code, "message": job.error_message}
            if job.error_code or job.error_message
            else None
        ),
        retryable=state in {"PENDING", "FAILED", "STALE"},
    )


@router.get("/meeting-imports/{import_id}/review", response_model=ReviewRead)
async def get_review(
    import_id: UUID, session: SessionDependency, current: CurrentUserDependency
) -> ReviewRead:
    item = await _get_import(session, current, import_id)
    if item.status is not MeetingImportStatus.CONFIRMED and ROLE_LEVEL_FOR_REVIEW(
        current.role
    ) < ROLE_LEVEL_FOR_REVIEW(Role.EDITOR):
        raise ForbiddenError()
    return await _serialize_review(session, item)


@router.get(
    "/meeting-imports/{import_id}/vectorization",
    response_model=VectorizationReadResponse,
)
async def get_vectorization(
    import_id: UUID,
    session: SessionDependency,
    current: CurrentUserDependency,
) -> VectorizationReadResponse:
    """Return lightweight vector state without serializing the full review document."""
    item = await _get_import(session, current, import_id)
    if item.status is not MeetingImportStatus.CONFIRMED and ROLE_LEVEL_FOR_REVIEW(
        current.role
    ) < ROLE_LEVEL_FOR_REVIEW(Role.EDITOR):
        raise ForbiddenError()
    document = await session.get(Document, item.document_id)
    if document is None:
        raise NotFoundError("文档", "document_not_found")
    revision = await session.scalar(
        select(TranscriptRevision)
        .where(
            TranscriptRevision.document_id == item.document_id,
            TranscriptRevision.status == TranscriptRevisionStatus.DRAFT,
        )
        .order_by(TranscriptRevision.version.desc())
    )
    job = None
    if revision is not None:
        job = await session.scalar(
            select(IngestionJob).where(
                IngestionJob.job_id
                == vector_job_id(item.id, revision.id, revision.version)
            )
        )
    state = _vectorization_status(item, document, revision, job)
    return VectorizationReadResponse(import_id=item.id, **state.model_dump())


@router.post(
    "/meeting-imports/{import_id}/vectorize",
    response_model=VectorizationReadResponse,
)
async def ensure_vectorization(
    import_id: UUID,
    payload: VectorizeRequest,
    session: SessionDependency,
    current: EditorDependency,
) -> VectorizationReadResponse:
    """Ensure one chunk/embed job exists for the current draft revision."""
    item = await _get_review_import(session, current, import_id, write=True)
    revision = await _current_draft(session, item, for_update=True)
    if revision.version != payload.expected_version:
        raise ConflictError(
            "stale_revision",
            "修订版本已变化，请刷新后重试",
            {"expected_version": payload.expected_version, "current_version": revision.version},
        )
    document = await session.scalar(
        select(Document).where(Document.id == item.document_id).with_for_update()
    )
    if document is None:
        raise NotFoundError("文档", "document_not_found")
    job, created = await ensure_vectorization_job(session, item, revision, document)
    requeued = False
    if job.status in {"FAILED", "STALE"} or (
        job.result_summary or {}
    ).get("revision_version") != revision.version:
        job.status = "QUEUED"
        job.current_node = "build_chunks"
        job.progress = 0
        job.error_code = job.error_message = None
        job.result_summary = {
            **(job.result_summary or {}),
            "revision_id": str(revision.id),
            "revision_version": revision.version,
            "mode": "vector_only",
        }
        requeued = True
    if job.status != "COMPLETED":
        document.vector_sync_status = "PENDING"
    await session.commit()
    if job.status == "QUEUED" and (created or requeued):
        try:
            from app.worker.celery_app import celery_app

            celery_app.send_task("app.worker.tasks.run_ingestion", args=[job.job_id])
        except Exception:
            pass
    status_read = _vectorization_status(item, document, revision, job)
    return VectorizationReadResponse(import_id=item.id, **status_read.model_dump())


@router.get("/meeting-imports/{import_id}/revisions/{revision_id}", response_model=RevisionRead)
async def get_revision(
    import_id: UUID, revision_id: UUID, session: SessionDependency, current: CurrentUserDependency
) -> RevisionRead:
    item = await _get_import(session, current, import_id)
    revision = await session.scalar(
        select(TranscriptRevision).where(
            TranscriptRevision.id == revision_id, TranscriptRevision.document_id == item.document_id
        )
    )
    if revision is None:
        raise NotFoundError("修订版本", "revision_not_found")
    if revision.status is not TranscriptRevisionStatus.CONFIRMED and ROLE_LEVEL_FOR_REVIEW(
        current.role
    ) < ROLE_LEVEL_FOR_REVIEW(Role.EDITOR):
        raise ForbiddenError()
    return _serialize_revision(revision, await _revision_blocks(session, revision.id))


@router.patch("/meeting-imports/{import_id}/revisions/{revision_id}", response_model=RevisionRead)
async def patch_revision(
    import_id: UUID,
    revision_id: UUID,
    payload: RevisionPatch,
    session: SessionDependency,
    current: EditorDependency,
) -> RevisionRead:
    item = await _get_review_import(session, current, import_id, write=True)
    await _lock_document_vector_state(session, item.document_id)
    revision = await session.scalar(
        select(TranscriptRevision)
        .where(
            TranscriptRevision.id == revision_id, TranscriptRevision.document_id == item.document_id
        )
        .with_for_update()
    )
    if revision is None or revision.status is not TranscriptRevisionStatus.DRAFT:
        raise ConflictError("revision_not_editable", "当前修订版本不可编辑")
    if revision.version != payload.expected_version:
        raise ConflictError(
            "stale_revision",
            "修订版本已变化，请刷新后重试",
            {"expected_version": payload.expected_version, "current_version": revision.version},
        )
    blocks = await _revision_blocks(session, revision.id)
    by_id = {block.block_id: block for block in blocks}
    for edit in payload.block_edits:
        block = by_id.get(edit.block_id)
        if block is None:
            raise AppException(422, "block_not_found", f"Block 不存在: {edit.block_id}")
        if edit.text is not None:
            block.text = edit.text
            block.content_hash = _content_hash(edit.text)
        if edit.block_type is not None:
            block.block_type = edit.block_type
        if edit.heading_path is not None:
            block.heading_path = edit.heading_path
        if edit.table_markdown is not None:
            block.table_markdown = edit.table_markdown
    revision.version += 1
    await session.commit()
    return _serialize_revision(revision, blocks)


@router.post("/meeting-imports/{import_id}/find")
async def find_in_revision(
    import_id: UUID,
    payload: FindRequest,
    session: SessionDependency,
    current: CurrentUserDependency,
) -> dict[str, Any]:
    item = await _get_import(session, current, import_id)
    if item.status is not MeetingImportStatus.CONFIRMED and ROLE_LEVEL_FOR_REVIEW(
        current.role
    ) < ROLE_LEVEL_FOR_REVIEW(Role.EDITOR):
        raise ForbiddenError()
    if item.status is MeetingImportStatus.CONFIRMED:
        document = await session.get(Document, item.document_id)
        revision = (
            await session.get(TranscriptRevision, document.active_transcript_revision_id)
            if document and document.active_transcript_revision_id
            else None
        )
        if revision is None:
            raise NotFoundError("修订版本", "revision_not_found")
    else:
        revision = await _current_draft(session, item)
    blocks = await _revision_blocks(session, revision.id)
    if payload.scope == "BLOCK" and not payload.block_id:
        raise AppException(422, "block_required", "BLOCK 范围需要 block_id")
    matches: list[dict[str, Any]] = []
    for block in blocks:
        if payload.block_id and block.block_id != payload.block_id:
            continue
        positions = _match_positions(block.text, payload.query, payload.case_sensitive)
        for start, end in positions:
            matches.append(
                {
                    "block_id": block.block_id,
                    "start": start,
                    "end": end,
                    "text": block.text[max(0, start - 40) : min(len(block.text), end + 40)],
                }
            )
    return {
        "query": payload.query,
        "count": len(matches),
        "matches": matches,
        "revision_id": revision.id,
        "version": revision.version,
    }


@router.post("/meeting-imports/{import_id}/replace")
async def replace_in_revision(
    import_id: UUID, payload: ReplaceRequest, session: SessionDependency, current: EditorDependency
) -> dict[str, Any]:
    item = await _get_review_import(session, current, import_id, write=True)
    await _lock_document_vector_state(session, item.document_id)
    revision = await _current_draft(session, item, for_update=True)
    if revision.version != payload.expected_version:
        raise ConflictError(
            "stale_revision",
            "修订版本已变化，请刷新后重试",
            {"expected_version": payload.expected_version, "current_version": revision.version},
        )
    if payload.scope == "BLOCK" and not payload.block_id:
        raise AppException(422, "block_required", "BLOCK 范围需要 block_id")
    blocks = await _revision_blocks(session, revision.id)
    selected = [
        block for block in blocks if not payload.block_id or block.block_id == payload.block_id
    ]
    current_occurrence_index = 0
    if payload.mode == "CURRENT":
        if payload.match_index is None:
            selected = selected[:1]
        else:
            seen = 0
            chosen: list[TranscriptRevisionBlock] = []
            for block in selected:
                count_in_block = len(
                    _match_positions(block.text, payload.query, payload.case_sensitive)
                )
                if seen <= payload.match_index < seen + count_in_block:
                    chosen = [block]
                    current_occurrence_index = payload.match_index - seen
                    break
                seen += count_in_block
            selected = chosen
    snapshots: list[dict[str, Any]] = []
    affected: list[str] = []
    count = 0
    for block in selected:
        positions = _match_positions(block.text, payload.query, payload.case_sensitive)
        if not positions:
            continue
        snapshots.append(
            {
                "block_id": block.block_id,
                "text": block.text,
                "table_markdown": block.table_markdown,
                "content_hash": block.content_hash,
            }
        )
        if payload.mode == "CURRENT":
            if current_occurrence_index >= len(positions):
                continue
            occurrence = positions[current_occurrence_index]
            replacement_text = (
                block.text[: occurrence[0]] + payload.replacement + block.text[occurrence[1] :]
            )
        else:
            replacement_text = (
                block.text.replace(payload.query, payload.replacement)
                if payload.case_sensitive
                else re.sub(
                    re.escape(payload.query),
                    lambda _: payload.replacement,
                    block.text,
                    flags=re.IGNORECASE,
                )
            )
        block.text = replacement_text
        if block.block_type == "table":
            block.table_markdown = replacement_text
        block.content_hash = _content_hash(block.text)
        count += 1 if payload.mode == "CURRENT" else len(positions)
        affected.append(block.block_id)
    if payload.preview:
        await session.rollback()
        return {
            "operation_id": None,
            "count": count,
            "affected_block_ids": affected,
            "revision_id": revision.id,
            "version": revision.version,
            "preview": True,
        }
    new_revision, _ = await _new_revision(session, revision, blocks, current.user_id)
    operation = BatchReplaceOperation(
        revision_id=new_revision.id,
        mode=payload.mode,
        query=payload.query,
        replacement=payload.replacement,
        scope=payload.scope,
        case_sensitive=payload.case_sensitive,
        match_count=count,
        affected_block_ids=affected,
        snapshots=snapshots,
        created_by=current.user_id,
    )
    session.add(operation)
    await session.commit()
    return {
        "operation_id": operation.id,
        "count": count,
        "affected_block_ids": affected,
        "revision_id": new_revision.id,
        "version": new_revision.version,
    }


@router.post("/meeting-imports/{import_id}/replace/{operation_id}/undo")
async def undo_replace(
    import_id: UUID,
    operation_id: UUID,
    payload: UndoRequest,
    session: SessionDependency,
    current: EditorDependency,
) -> dict[str, Any]:
    item = await _get_review_import(session, current, import_id, write=True)
    await _lock_document_vector_state(session, item.document_id)
    revision = await _current_draft(session, item, for_update=True)
    if revision.version != payload.expected_version:
        raise ConflictError("stale_revision", "修订版本已变化，请刷新后重试")
    operation = await session.scalar(
        select(BatchReplaceOperation).where(
            BatchReplaceOperation.id == operation_id,
            BatchReplaceOperation.revision_id == revision.id,
        )
    )
    if operation is None:
        raise NotFoundError("替换操作", "replace_operation_not_found")
    blocks = await _revision_blocks(session, revision.id)
    snapshots = {str(value["block_id"]): value for value in operation.snapshots}
    for block in blocks:
        if block.block_id in snapshots:
            block.text = snapshots[block.block_id]["text"]
            if "table_markdown" in snapshots[block.block_id]:
                block.table_markdown = snapshots[block.block_id]["table_markdown"]
            block.content_hash = snapshots[block.block_id]["content_hash"]
    new_revision, _ = await _new_revision(session, revision, blocks, current.user_id)
    await session.commit()
    return {
        "operation_id": operation.id,
        "undone": True,
        "revision_id": new_revision.id,
        "version": new_revision.version,
    }


def _validate_meeting_values(values: dict[str, Any]) -> None:
    if not str(values.get("title") or "").strip():
        raise AppException(422, "title_required", "会议标题不能为空")

    # Imported meeting minutes normally provide a date but not a start/end
    # time.  The review screen must not force the reviewer to invent times for
    # those documents.  Keep the legacy datetime columns populated with a
    # timezone-aware full-day range for compatibility with the existing
    # Meeting model and list filters.
    meeting_date = _meeting_date_bounds(values.get("meeting_date"))
    if meeting_date is not None:
        values["starts_at"], values["ends_at"] = meeting_date

    starts_at, ends_at = values.get("starts_at"), values.get("ends_at")
    if isinstance(starts_at, str):
        try:
            starts_at = datetime.fromisoformat(starts_at)
            values["starts_at"] = starts_at
        except ValueError:
            starts_at = None
    if isinstance(ends_at, str):
        try:
            ends_at = datetime.fromisoformat(ends_at)
            values["ends_at"] = ends_at
        except ValueError:
            ends_at = None
    if (
        not isinstance(starts_at, datetime)
        or starts_at.tzinfo is None
        or starts_at.utcoffset() is None
    ):
        raise AppException(422, "starts_at_timezone_required", "开始时间必须携带时区信息")
    if not isinstance(ends_at, datetime) or ends_at.tzinfo is None or ends_at.utcoffset() is None:
        raise AppException(422, "ends_at_timezone_required", "结束时间必须携带时区信息")
    if ends_at <= starts_at:
        raise AppException(422, "invalid_time_range", "结束时间必须晚于开始时间")


def _meeting_date_bounds(value: Any) -> tuple[datetime, datetime] | None:
    """Convert common Chinese/ISO date text to a UTC full-day range."""
    text_value = str(value or "").strip()
    if not text_value:
        return None
    match = re.search(
        r"(?P<year>\d{4})\s*[年\-/\.]\s*(?P<month>\d{1,2})\s*[月\-/\.]\s*(?P<day>\d{1,2})\s*日?",
        text_value,
    )
    if match is None:
        return None
    try:
        parsed = date(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )
    except ValueError:
        return None
    start = datetime.combine(parsed, time.min, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def _apply_metadata(
    item: MeetingImport, payload: MeetingMetadataPatch | ConfirmRequest
) -> dict[str, Any]:
    raw = dict(item.metadata_json or {})
    current, _, _ = _metadata_fields(item)
    values = {key: current[key]["value"] for key in current}
    user_values = dict(raw.get("user_values", {}))
    for key in payload.model_fields_set - {"expected_version"}:
        if key not in values:
            continue
        provided = getattr(payload, key, None)
        normalized = (
            provided.isoformat()
            if isinstance(provided, datetime)
            else str(provided)
            if key == "online_url" and provided is not None
            else provided
        )
        suggestion = current[key]["suggested_value"]
        # Accepted AI suggestions remain unmodified; an explicit clear is a
        # user edit even when the suggestion was already empty.
        if normalized is None or normalized != suggestion or key in user_values:
            values[key] = normalized
            user_values[key] = normalized
    raw["user_values"] = user_values
    raw["metadata_version"] = int(raw.get("metadata_version", 1)) + 1
    item.metadata_json = raw
    return values


@router.patch("/meeting-imports/{import_id}/metadata")
async def patch_metadata(
    import_id: UUID,
    payload: MeetingMetadataPatch,
    session: SessionDependency,
    current: EditorDependency,
) -> dict[str, Any]:
    item = await _get_review_import(session, current, import_id, write=True)
    fields, version, _ = _metadata_fields(item)
    if payload.expected_version != version:
        raise ConflictError(
            "stale_metadata", "会议元数据已变化，请刷新后重试", {"current_version": version}
        )
    values = _apply_metadata(item, payload)
    # Validate only when complete; title is always required and times are
    # required before confirmation.
    if values.get("title") and values.get("starts_at") and values.get("ends_at"):
        _validate_meeting_values(values)
    await session.commit()
    return {"metadata": _metadata_fields(item)[0], "metadata_version": _metadata_fields(item)[1]}


def _enqueue_confirmed_job(
    session: AsyncSession, item: MeetingImport, document: Document
) -> IngestionJob:
    job = IngestionJob(
        job_id=f"meeting-import-{item.id}",
        organization_id=item.organization_id,
        knowledge_base_id=item.knowledge_base_id,
        document_id=document.id,
        status="QUEUED",
        current_node="extract_knowledge",
        progress=0,
        result_summary={
            "source": "confirmed_transcript_revision",
            "revision_id": str(document.active_transcript_revision_id),
        },
    )
    session.add(job)
    return job


@router.post("/meeting-imports/{import_id}/confirm", response_model=ConfirmRead)
async def confirm_import(
    import_id: UUID,
    payload: ConfirmRequest,
    session: SessionDependency,
    current: EditorDependency,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ConfirmRead:
    item = await _get_import(session, current, import_id, for_update=True)
    key = idempotency_key or f"meeting-import-confirm:{item.id}"
    if (
        item.status is MeetingImportStatus.CONFIRMED
        and item.meeting_id
        and item.confirmed_revision_id
    ):
        existing_job = await session.scalar(
            select(IngestionJob).where(IngestionJob.job_id == f"meeting-import-{item.id}")
        )
        existing_task = await session.scalar(
            select(AiTask)
            .where(
                AiTask.meeting_id == item.meeting_id,
                AiTask.task_type == "QUESTION_GENERATION",
            )
            .order_by(AiTask.created_at.desc())
        )
        existing_task_status = (
            existing_task.status.value
            if existing_task and hasattr(existing_task.status, "value")
            else (existing_task.status if existing_task else None)
        )
        return ConfirmRead(
            meeting_id=item.meeting_id,
            import_id=item.id,
            revision_id=item.confirmed_revision_id,
            status=item.status.value,
            rag_job_id=existing_job.job_id if existing_job else f"meeting-import-{item.id}",
            rag_status=existing_job.status if existing_job else "QUEUED",
            rag_error=existing_job.error_message if existing_job else None,
            rag_retryable=bool(existing_job and existing_job.result_summary.get("retryable")),
            ai_task_id=existing_task.id if existing_task else None,
            question_generation_status=existing_task_status,
            meeting_status=(
                f"QUESTION_GENERATION_{existing_task_status}"
                if existing_task_status
                else "QUESTION_GENERATION_QUEUED"
            ),
        )
    if not key.strip() or len(key) > 255:
        raise AppException(422, "invalid_idempotency_key", "Idempotency-Key 长度必须为 1 到 255")
    key_owner = await session.scalar(
        select(MeetingImport.id).where(
            MeetingImport.confirmation_idempotency_key == key,
            MeetingImport.id != item.id,
        )
    )
    if key_owner is not None:
        raise ConflictError("idempotency_key_reused", "该幂等键已用于其他导入任务")
    if item.status is not MeetingImportStatus.READY_FOR_REVIEW:
        raise ConflictError("import_not_confirmable", "导入任务当前不可确认")
    revision = await _current_draft(session, item, for_update=True)
    if revision.version != payload.expected_version:
        raise ConflictError(
            "stale_revision", "修订版本已变化，请刷新后重试", {"current_version": revision.version}
        )
    _, metadata_version, _ = _metadata_fields(item)
    if metadata_version != payload.expected_metadata_version:
        raise ConflictError(
            "stale_metadata",
            "会议信息已变化，请刷新后重试",
            {"current_version": metadata_version},
        )
    values = _apply_metadata(item, payload)
    # Metadata extracted by old workers may not contain dates.  Confirmation
    # requires the complete meeting envelope.
    try:
        _validate_meeting_values(values)
    except AppException:
        await session.rollback()
        raise
    document = await session.scalar(
        select(Document).where(Document.id == item.document_id).with_for_update()
    )
    if document is None:
        raise NotFoundError("文档", "document_not_found")
    vector_job = await session.scalar(
        select(IngestionJob).where(
            IngestionJob.job_id == vector_job_id(item.id, revision.id, revision.version)
        )
    )
    vector_state = _vectorization_status(item, document, revision, vector_job)
    if vector_state.status != "SYNCED" or getattr(document, "vector_sync_status", None) != "SYNCED":
        raise ConflictError(
            "vectorization_required",
            "当前修订版本尚未完成向量同步，请先重试向量化",
            {
                "revision_id": str(revision.id),
                "vectorization": vector_state.model_dump(mode="json"),
            },
        )
    meeting_info = {
        key: values.get(key)
        for key in (
            "meeting_purpose",
            "discussion_topics",
            "meeting_date",
            "advisor_selection_criteria",
            "advisor_names",
            "internal_attendees",
            "recorder",
        )
    }
    discussion_topics = str(values.get("discussion_topics") or "").strip() or None
    meeting = Meeting(
        organization_id=current.organization_id,
        knowledge_base_id=item.knowledge_base_id,
        title=str(values["title"]),
        starts_at=values["starts_at"],
        ends_at=values["ends_at"],
        location=values.get("location"),
        online_url=values.get("online_url"),
        organizer=values.get("organizer"),
        topic=discussion_topics[:255] if discussion_topics else values.get("topic"),
        description=values.get("meeting_purpose") or values.get("description"),
        meeting_info=meeting_info,
    )
    session.add(meeting)
    await session.flush()
    revision.status = TranscriptRevisionStatus.CONFIRMED
    revision.confirmed_by = current.user_id
    revision.confirmed_at = datetime.now(timezone.utc)
    document.active_transcript_revision_id = revision.id
    document.meeting_id = meeting.id
    if hasattr(session, "execute"):
        await session.execute(
            update(Chunk).where(Chunk.document_id == document.id).values(meeting_id=meeting.id)
        )
    item.status = MeetingImportStatus.CONFIRMED
    item.meeting_id = meeting.id
    item.confirmed_revision_id = revision.id
    item.confirmation_idempotency_key = key
    await session.flush()
    job = _enqueue_confirmed_job(session, item, document)
    # The import idempotency lock above guarantees one task for this revision;
    # the database unique constraint is the final race-safe guard.
    ai_task = AiTask(
        meeting_id=meeting.id,
        organization_id=current.organization_id,
        task_type="QUESTION_GENERATION",
        source_version=revision.version,
        thread_id=thread_id(meeting.id, revision.version),
        status="QUEUED",
        current_stage="queued",
        progress=0,
        model_name=get_settings().llm_model or "unconfigured",
        prompt_version="question-generation-v2",
    )
    session.add(ai_task)
    await session.flush()
    session.add(
        OutboxEvent(
            idempotency_key=f"question-generation:{meeting.id}:{revision.version}",
            event_type="question_generation.requested",
            aggregate_id=str(meeting.id),
            payload={"task_id": str(ai_task.id), "meeting_id": str(meeting.id)},
            status="PENDING",
        )
    )
    await session.commit()
    try:
        from app.worker.celery_app import celery_app

        celery_app.send_task("app.worker.tasks.run_ingestion", args=[job.job_id])
    except Exception as exc:
        # The Meeting/revision commit remains durable. Keep the job QUEUED so
        # the scheduled reconciler can redeliver it after a transient outage.
        async with session.begin():
            persisted = await session.scalar(
                select(IngestionJob).where(IngestionJob.id == job.id).with_for_update()
            )
            if persisted is not None:
                persisted.status = "QUEUED"
                persisted.error_code = "enqueue_failed"
                persisted.error_message = str(exc)[:2000]
                persisted.result_summary = {**(persisted.result_summary or {}), "retryable": True}
    return ConfirmRead(
        meeting_id=meeting.id,
        import_id=item.id,
        revision_id=revision.id,
        status=item.status.value,
        rag_job_id=job.job_id,
        rag_status=job.status,
        rag_error=job.error_message,
        rag_retryable=bool(job.result_summary.get("retryable")) if job.result_summary else False,
        ai_task_id=ai_task.id,
        question_generation_status=str(
            ai_task.status.value if hasattr(ai_task.status, "value") else ai_task.status
        ),
        meeting_status="QUESTION_GENERATION_QUEUED",
    )
