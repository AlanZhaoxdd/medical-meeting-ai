from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    AuthContext,
    CurrentUserDependency,
    require_kb_access,
    require_role,
)
from app.core.config import get_settings
from app.core.exceptions import AppException, NotFoundError
from app.db.session import get_session
from app.ingestion.state import ensure_transition
from app.ingestion.validation import safe_filename, validate_upload
from app.models.kb import (
    Chunk,
    Document,
    DocumentBlock,
    ExtractionTemplate,
    ExtractionTemplateVersion,
    IngestionJob,
    OutboxEvent,
)
from app.models.meeting import Meeting
from app.schemas.kb import (
    DocumentStatus,
    DocumentRead,
    JobRead,
    Role,
    UploadResponse,
)
from app.services.audit import record_audit
from app.services.storage import ObjectStorage
from app.services.vector_store import VectorStore

router = APIRouter(prefix="/knowledge-bases/{kb_id}/documents", tags=["文档"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
EditorDependency = Annotated[AuthContext, Depends(require_role(Role.EDITOR))]
AdminDependency = Annotated[AuthContext, Depends(require_role(Role.ADMIN))]


def serialize_document(document: Document) -> DocumentRead:
    return DocumentRead(
        id=str(document.id),
        organization_id=str(document.organization_id),
        knowledge_base_id=str(document.knowledge_base_id),
        meeting_id=str(document.meeting_id) if document.meeting_id else None,
        filename=document.filename,
        safe_filename=document.safe_filename,
        mime_type=document.mime_type,
        source_type=document.source_type,
        sha256=document.sha256,
        version=document.version,
        previous_version_id=(
            str(document.previous_version_id) if document.previous_version_id else None
        ),
        template_id=str(document.template_id),
        template_version=document.template_version,
        status=DocumentStatus(document.status),
        vector_sync_status=document.vector_sync_status,
        error_code=document.error_code,
        error_message=document.error_message,
        created_at=document.created_at,
        updated_at=document.updated_at,
        published_at=document.published_at,
    )


def serialize_job(job: IngestionJob) -> JobRead:
    return JobRead(
        job_id=job.job_id,
        document_id=str(job.document_id),
        status=job.status,
        current_node=job.current_node,
        progress=job.progress,
        error_code=job.error_code,
        error_message=job.error_message,
        result_summary=job.result_summary,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


async def _read_bounded_upload(file: UploadFile, maximum: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while data := await file.read(1024 * 1024):
        total += len(data)
        if total > maximum:
            raise AppException(413, "file_too_large", f"文件超过 {maximum} 字节限制")
        chunks.append(data)
    return b"".join(chunks)


async def _resolve_template(
    session: AsyncSession, kb_id: UUID, template_id: UUID
) -> tuple[ExtractionTemplate, ExtractionTemplateVersion]:
    template = await session.scalar(
        select(ExtractionTemplate).where(
            ExtractionTemplate.id == template_id,
            ExtractionTemplate.knowledge_base_id == kb_id,
            ExtractionTemplate.deleted_at.is_(None),
        )
    )
    if template is None:
        raise NotFoundError("模板", "template_not_found")
    version = await session.scalar(
        select(ExtractionTemplateVersion).where(
            ExtractionTemplateVersion.template_id == template.id,
            ExtractionTemplateVersion.version == template.latest_version,
        )
    )
    if version is None:
        raise NotFoundError("模板版本", "template_version_not_found")
    return template, version


def _dispatch_job(job_id: str) -> None:
    from app.worker.celery_app import celery_app

    celery_app.send_task("app.worker.tasks.run_ingestion", args=[job_id])


@router.post("", response_model=UploadResponse, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    kb_id: UUID,
    session: SessionDependency,
    current: EditorDependency,
    file: UploadFile = File(...),
    meeting_id: UUID | None = Form(default=None),
    template_id: UUID | None = Form(default=None),
    force_new_version: bool = Form(default=False),
) -> UploadResponse:
    settings = get_settings()
    kb = await require_kb_access(session, current, kb_id)
    original_name = file.filename or ""
    sanitized = safe_filename(original_name)
    content = await _read_bounded_upload(file, settings.max_upload_bytes)
    mime_type = file.content_type or "application/octet-stream"
    sha256 = validate_upload(
        original_name, mime_type, content, settings.max_upload_bytes
    )
    duplicate = await session.scalar(
        select(Document)
        .where(
            Document.organization_id == current.organization_id,
            Document.knowledge_base_id == kb_id,
            Document.sha256 == sha256,
            Document.deleted_at.is_(None),
        )
        .order_by(Document.version.desc())
    )
    if duplicate is not None and not force_new_version:
        return UploadResponse(document=serialize_document(duplicate), duplicate=True)

    selected_template_id = template_id or kb.default_template_id
    if selected_template_id is None:
        raise AppException(409, "template_required", "知识库尚未设置默认模板")
    template, template_version = await _resolve_template(
        session, kb_id, selected_template_id
    )
    previous = await session.scalar(
        select(Document)
        .where(
            Document.knowledge_base_id == kb_id,
            Document.safe_filename == sanitized,
            Document.deleted_at.is_(None),
        )
        .order_by(Document.version.desc())
    )
    if force_new_version and duplicate is not None:
        previous = duplicate
    version = previous.version + 1 if previous else 1

    if meeting_id is not None:
        meeting = await session.scalar(
            select(Meeting).where(Meeting.id == meeting_id, Meeting.deleted_at.is_(None))
        )
        if meeting is None:
            raise NotFoundError("会议")
        if meeting.knowledge_base_id not in (None, kb_id):
            raise AppException(409, "meeting_already_linked", "会议已属于其他知识库")
        meeting.knowledge_base_id = kb_id

    new_document_id = uuid4()
    object_key = (
        f"org/{current.organization_id}/kb/{kb_id}/documents/"
        f"{new_document_id}/v{version}/{sanitized}"
    )
    document = Document(
        id=new_document_id,
        organization_id=current.organization_id,
        knowledge_base_id=kb_id,
        meeting_id=meeting_id,
        filename=original_name,
        safe_filename=sanitized,
        mime_type=mime_type,
        source_type="transcript" if Path(sanitized).suffix.lower() == ".json" else "document",
        minio_bucket=settings.minio_bucket,
        minio_object_key=object_key,
        sha256=sha256,
        version=version,
        previous_version_id=previous.id if previous else None,
        parser_name="docling",
        parser_version="2.x",
        template_id=template.id,
        template_version=template_version.version,
        status=DocumentStatus.UPLOADED.value,
        vector_sync_status="PENDING",
        created_by=current.user_id,
    )
    session.add(document)
    await session.flush()
    try:
        await ObjectStorage().put(object_key, content, mime_type)
    except Exception as exc:
        await session.rollback()
        raise AppException(503, "object_storage_unavailable", "原件保存失败，请稍后重试") from exc
    document.status = DocumentStatus.UPLOADED.value
    job = IngestionJob(
        job_id=str(uuid4()),
        organization_id=current.organization_id,
        knowledge_base_id=kb_id,
        document_id=document.id,
        status="QUEUED",
        current_node="validate_source",
        progress=5,
        result_summary={},
    )
    session.add(job)
    await record_audit(
        session,
        organization_id=current.organization_id,
        actor_id=current.user_id,
        action="document.upload",
        resource_type="document",
        resource_id=str(document.id),
        metadata={"sha256": sha256, "version": version},
    )
    await session.commit()
    await session.refresh(document)
    try:
        _dispatch_job(job.job_id)
    except Exception as exc:
        job.status = "DISPATCH_FAILED"
        job.error_code = "celery_unavailable"
        job.error_message = str(exc)[:1000]
        await session.commit()
    return UploadResponse(
        document=serialize_document(document), job_id=job.job_id, duplicate=False
    )


@router.get("", response_model=list[DocumentRead])
async def list_documents(
    kb_id: UUID, session: SessionDependency, current: CurrentUserDependency
) -> list[DocumentRead]:
    await require_kb_access(session, current, kb_id)
    documents = (
        await session.scalars(
            select(Document)
            .where(
                Document.organization_id == current.organization_id,
                Document.knowledge_base_id == kb_id,
                Document.deleted_at.is_(None),
            )
            .order_by(Document.created_at.desc())
        )
    ).all()
    return [serialize_document(document) for document in documents]


async def _get_document(
    session: AsyncSession,
    current: AuthContext,
    kb_id: UUID,
    document_id: UUID,
    *,
    for_update: bool = False,
) -> Document:
    await require_kb_access(session, current, kb_id)
    statement = select(Document).where(
        Document.id == document_id,
        Document.organization_id == current.organization_id,
        Document.knowledge_base_id == kb_id,
        Document.deleted_at.is_(None),
    )
    if for_update:
        statement = statement.with_for_update()
    document = await session.scalar(statement)
    if document is None:
        raise NotFoundError("文档", "document_not_found")
    return document


@router.get("/{document_id}", response_model=DocumentRead)
async def get_document(
    kb_id: UUID,
    document_id: UUID,
    session: SessionDependency,
    current: CurrentUserDependency,
) -> DocumentRead:
    return serialize_document(
        await _get_document(session, current, kb_id, document_id)
    )


@router.get("/{document_id}/blocks", response_model=list[dict[str, Any]])
async def list_blocks(
    kb_id: UUID,
    document_id: UUID,
    session: SessionDependency,
    current: CurrentUserDependency,
) -> list[dict[str, Any]]:
    await _get_document(session, current, kb_id, document_id)
    blocks = (
        await session.scalars(
            select(DocumentBlock)
            .where(DocumentBlock.document_id == document_id)
            .order_by(DocumentBlock.order)
        )
    ).all()
    return [
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
        }
        for block in blocks
    ]


@router.get("/{document_id}/chunks", response_model=list[dict[str, Any]])
async def list_chunks(
    kb_id: UUID,
    document_id: UUID,
    session: SessionDependency,
    current: CurrentUserDependency,
) -> list[dict[str, Any]]:
    await _get_document(session, current, kb_id, document_id)
    chunks = (
        await session.scalars(
            select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.chunk_index)
        )
    ).all()
    return [
        {
            "chunk_id": chunk.chunk_id,
            "chunk_index": chunk.chunk_index,
            "content": chunk.content,
            "heading_path": chunk.heading_path,
            "content_type": chunk.content_type,
            "token_count": chunk.token_count,
            "source_block_ids": chunk.source_block_ids,
            "source_locator": chunk.source_locator,
            "publication_status": chunk.publication_status,
        }
        for chunk in chunks
    ]


async def _new_job(
    session: AsyncSession, document: Document, start_node: str
) -> IngestionJob:
    job = IngestionJob(
        job_id=str(uuid4()),
        organization_id=document.organization_id,
        knowledge_base_id=document.knowledge_base_id,
        document_id=document.id,
        status="QUEUED",
        current_node=start_node,
        progress=5,
        result_summary={},
    )
    session.add(job)
    return job


@router.post("/{document_id}/retry", response_model=JobRead, status_code=202)
async def retry_document(
    kb_id: UUID,
    document_id: UUID,
    session: SessionDependency,
    current: EditorDependency,
) -> JobRead:
    document = await _get_document(session, current, kb_id, document_id)
    if document.status != DocumentStatus.FAILED.value:
        raise AppException(409, "document_not_failed", "只有失败文档可以重试")
    ensure_transition(document.status, DocumentStatus.PARSING)
    document.error_code = document.error_message = None
    job = await _new_job(session, document, "parse_document")
    await session.commit()
    _dispatch_job(job.job_id)
    return serialize_job(job)


@router.post("/{document_id}/reindex", response_model=JobRead, status_code=202)
async def reindex_document(
    kb_id: UUID,
    document_id: UUID,
    session: SessionDependency,
    current: EditorDependency,
) -> JobRead:
    document = await _get_document(
        session, current, kb_id, document_id, for_update=True
    )
    if document.status == DocumentStatus.PUBLISHED.value:
        raise AppException(
            409,
            "published_document_reindex_requires_new_version",
            "已发布文档请创建新版本后再执行语义分块",
        )
    # Reuse the stored normalized blocks and rebuild semantic boundaries. This
    # path intentionally does not re-run parsing/OCR.
    ensure_transition(document.status, DocumentStatus.CHUNKING)
    document.status = DocumentStatus.CHUNKING.value
    document.vector_sync_status = "PENDING"
    job = await _new_job(session, document, "build_chunks")
    await session.commit()
    _dispatch_job(job.job_id)
    return serialize_job(job)


@router.delete("/{document_id}", status_code=204)
async def delete_document(
    kb_id: UUID,
    document_id: UUID,
    session: SessionDependency,
    current: EditorDependency,
    purge: bool = Query(default=False),
) -> Response:
    document = await _get_document(session, current, kb_id, document_id)
    if purge:
        if current.role not in {Role.OWNER, Role.ADMIN}:
            raise AppException(403, "permission_denied", "彻底清除仅限 owner/admin")
        await ObjectStorage().delete(document.minio_object_key)
        await VectorStore().delete_document(str(document.id))
        await session.delete(document)
        action = "document.purge"
    else:
        document.deleted_at = datetime.now(timezone.utc)
        document.status = DocumentStatus.DELETED.value
        session.add(
            OutboxEvent(
                idempotency_key=f"document.delete:{document.id}:{document.version}",
                event_type="vector.delete_document",
                aggregate_id=str(document.id),
                payload={
                    "organization_id": str(document.organization_id),
                    "knowledge_base_id": str(document.knowledge_base_id),
                    "document_id": str(document.id),
                },
            )
        )
        action = "document.delete"
    await record_audit(
        session,
        organization_id=current.organization_id,
        actor_id=current.user_id,
        action=action,
        resource_type="document",
        resource_id=str(document_id),
    )
    await session.commit()
    if not purge:
        from app.worker.celery_app import celery_app

        celery_app.send_task("app.worker.tasks.sync_outbox")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
