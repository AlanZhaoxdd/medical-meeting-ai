from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    AuthContext,
    CurrentUserDependency,
    require_kb_access,
    require_role,
)
from app.core.exceptions import AppException, NotFoundError
from app.db.session import get_session
from app.models.kb import (
    Chunk,
    Document,
    DocumentBlock,
    IngestionJob,
    KnowledgeItem,
    OutboxEvent,
    ReviewEvent,
)
from app.schemas.kb import (
    DocumentStatus,
    KnowledgeItemUpdate,
    ReviewRequest,
    ReviewStatus,
    Role,
)
from app.services.audit import record_audit

router = APIRouter(prefix="/knowledge-bases/{kb_id}", tags=["知识审核"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
EditorDependency = Annotated[AuthContext, Depends(require_role(Role.EDITOR))]
ReviewerDependency = Annotated[AuthContext, Depends(require_role(Role.REVIEWER))]


def serialize_item(item: KnowledgeItem) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "document_id": str(item.document_id),
        "knowledge_base_id": str(item.knowledge_base_id),
        "meeting_id": str(item.meeting_id) if item.meeting_id else None,
        "item_type": item.item_type,
        "title": item.title,
        "normalized_content": item.normalized_content,
        "structured_data": item.structured_data,
        "source_refs": item.source_refs,
        "confidence": item.confidence,
        "extraction_template_id": str(item.extraction_template_id),
        "extraction_template_version": item.extraction_template_version,
        "prompt_version": item.prompt_version,
        "model_name": item.model_name,
        "review_status": item.review_status,
        "reviewer_id": str(item.reviewer_id) if item.reviewer_id else None,
        "review_comment": item.review_comment,
        "reviewed_at": item.reviewed_at,
        "publication_status": item.publication_status,
        "revision": item.revision,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


async def _get_item(
    session: AsyncSession, current: AuthContext, kb_id: UUID, item_id: UUID
) -> KnowledgeItem:
    await require_kb_access(session, current, kb_id)
    item = await session.scalar(
        select(KnowledgeItem).where(
            KnowledgeItem.id == item_id,
            KnowledgeItem.organization_id == current.organization_id,
            KnowledgeItem.knowledge_base_id == kb_id,
        )
    )
    if item is None:
        raise NotFoundError("知识项", "knowledge_item_not_found")
    return item


@router.get("/knowledge-items", response_model=list[dict[str, Any]])
async def list_knowledge_items(
    kb_id: UUID,
    session: SessionDependency,
    current: CurrentUserDependency,
    document_id: UUID | None = Query(default=None),
    item_type: str | None = Query(default=None),
    review_status: ReviewStatus | None = Query(default=None),
) -> list[dict[str, Any]]:
    await require_kb_access(session, current, kb_id)
    statement = select(KnowledgeItem).where(
        KnowledgeItem.organization_id == current.organization_id,
        KnowledgeItem.knowledge_base_id == kb_id,
    )
    if document_id is not None:
        statement = statement.where(KnowledgeItem.document_id == document_id)
    if item_type is not None:
        statement = statement.where(KnowledgeItem.item_type == item_type)
    if review_status is not None:
        statement = statement.where(KnowledgeItem.review_status == review_status.value)
    items = (
        await session.scalars(statement.order_by(KnowledgeItem.created_at))
    ).all()
    if current.role == Role.VIEWER:
        items = [item for item in items if item.publication_status == "PUBLISHED"]
    return [serialize_item(item) for item in items]


@router.patch("/knowledge-items/{item_id}", response_model=dict[str, Any])
async def update_knowledge_item(
    kb_id: UUID,
    item_id: UUID,
    payload: KnowledgeItemUpdate,
    session: SessionDependency,
    current: EditorDependency,
) -> dict[str, Any]:
    item = await _get_item(session, current, kb_id, item_id)
    if item.publication_status == "PUBLISHED":
        raise AppException(
            409, "published_item_immutable", "已发布知识不可直接覆盖，请创建修订"
        )
    before = serialize_item(item)
    changes = payload.model_dump(exclude_unset=True, mode="json")
    for key, value in changes.items():
        setattr(item, key, value)
    item.revision += 1
    item.review_status = ReviewStatus.PENDING.value
    session.add(
        ReviewEvent(
            organization_id=current.organization_id,
            actor_id=current.user_id,
            action="knowledge_item.edit",
            resource_type="knowledge_item",
            resource_id=str(item.id),
            metadata_json={"before": before, "revision": item.revision},
        )
    )
    await record_audit(
        session,
        organization_id=current.organization_id,
        actor_id=current.user_id,
        action="knowledge_item.edit",
        resource_type="knowledge_item",
        resource_id=str(item.id),
    )
    await session.commit()
    await session.refresh(item)
    return serialize_item(item)


@router.post("/knowledge-items/{item_id}/review", response_model=dict[str, Any])
async def review_knowledge_item(
    kb_id: UUID,
    item_id: UUID,
    payload: ReviewRequest,
    session: SessionDependency,
    current: ReviewerDependency,
) -> dict[str, Any]:
    item = await _get_item(session, current, kb_id, item_id)
    if payload.status == ReviewStatus.APPROVED and not item.source_refs:
        raise AppException(422, "evidence_required", "没有证据定位的知识项不能批准")
    now = datetime.now(timezone.utc)
    previous_status = item.review_status
    item.review_status = payload.status.value
    item.review_comment = payload.comment
    item.reviewer_id = current.user_id
    item.reviewed_at = now
    document = await session.get(Document, item.document_id)
    if document and document.status == DocumentStatus.AWAITING_REVIEW.value:
        document.status = DocumentStatus.IN_REVIEW.value
    session.add(
        ReviewEvent(
            organization_id=current.organization_id,
            actor_id=current.user_id,
            action=f"knowledge_item.{payload.status.value.lower()}",
            resource_type="knowledge_item",
            resource_id=str(item.id),
            metadata_json={
                "previous_status": previous_status,
                "comment": payload.comment,
                "revision": item.revision,
            },
        )
    )
    await record_audit(
        session,
        organization_id=current.organization_id,
        actor_id=current.user_id,
        action="knowledge_item.review",
        resource_type="knowledge_item",
        resource_id=str(item.id),
        metadata={"status": payload.status.value},
    )
    await session.commit()
    await session.refresh(item)
    return serialize_item(item)


async def _validate_approved_evidence(
    session: AsyncSession, document_id: UUID, approved: list[KnowledgeItem]
) -> None:
    block_ids = set(
        (
            await session.scalars(
                select(DocumentBlock.block_id).where(
                    DocumentBlock.document_id == document_id
                )
            )
        ).all()
    )
    chunk_ids = set(
        (
            await session.scalars(
                select(Chunk.chunk_id).where(Chunk.document_id == document_id)
            )
        ).all()
    )
    for item in approved:
        if not item.source_refs:
            raise AppException(
                409, "approved_item_missing_evidence", f"知识项 {item.id} 缺少来源"
            )
        for ref in item.source_refs:
            block_ok = ref.get("block_id") in block_ids if ref.get("block_id") else False
            chunk_ok = ref.get("chunk_id") in chunk_ids if ref.get("chunk_id") else False
            if not (block_ok or chunk_ok) or not ref.get("quote"):
                raise AppException(
                    409,
                    "invalid_source_reference",
                    f"知识项 {item.id} 包含无效来源引用",
                )


@router.post("/documents/{document_id}/publish", response_model=dict[str, Any])
async def publish_document(
    kb_id: UUID,
    document_id: UUID,
    session: SessionDependency,
    current: ReviewerDependency,
) -> dict[str, Any]:
    await require_kb_access(session, current, kb_id)
    document = await session.scalar(
        select(Document)
        .where(
            Document.id == document_id,
            Document.organization_id == current.organization_id,
            Document.knowledge_base_id == kb_id,
            Document.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if document is None:
        raise NotFoundError("文档", "document_not_found")
    if document.status not in {
        DocumentStatus.AWAITING_REVIEW.value,
        DocumentStatus.IN_REVIEW.value,
    }:
        raise AppException(409, "document_not_publishable", "文档当前状态不可发布")
    items = (
        await session.scalars(
            select(KnowledgeItem).where(KnowledgeItem.document_id == document_id)
        )
    ).all()
    unresolved = [
        item
        for item in items
        if item.review_status
        in {ReviewStatus.PENDING.value, ReviewStatus.NEEDS_CHANGES.value}
    ]
    if unresolved:
        raise AppException(
            409,
            "review_incomplete",
            "仍有待审核或需修改的知识项",
            {"item_ids": [str(item.id) for item in unresolved]},
        )
    approved = [
        item for item in items if item.review_status == ReviewStatus.APPROVED.value
    ]
    await _validate_approved_evidence(session, document_id, approved)
    if document.vector_sync_status != "SYNCED":
        raise AppException(409, "vector_sync_incomplete", "向量同步尚未成功")
    now = datetime.now(timezone.utc)
    document.status = DocumentStatus.PUBLISHED.value
    document.vector_sync_status = "PENDING"
    document.published_at = now
    await session.execute(
        update(Chunk)
        .where(Chunk.document_id == document_id)
        .values(publication_status="PUBLISHED")
    )
    if approved:
        await session.execute(
            update(KnowledgeItem)
            .where(KnowledgeItem.id.in_([item.id for item in approved]))
            .values(publication_status="PUBLISHED")
        )
    session.add(
        OutboxEvent(
            idempotency_key=f"document.publish:{document.id}:{document.version}",
            event_type="vector.publish_document",
            aggregate_id=str(document.id),
            payload={
                "organization_id": str(current.organization_id),
                "knowledge_base_id": str(kb_id),
                "document_id": str(document.id),
                "document_version": document.version,
            },
        )
    )
    await record_audit(
        session,
        organization_id=current.organization_id,
        actor_id=current.user_id,
        action="document.publish",
        resource_type="document",
        resource_id=str(document.id),
        metadata={"approved_items": len(approved)},
    )
    await session.commit()
    from app.worker.celery_app import celery_app

    celery_app.send_task("app.worker.tasks.sync_outbox")
    waiting_job = await session.scalar(
        select(IngestionJob)
        .where(
            IngestionJob.document_id == document.id,
            IngestionJob.status == "WAITING_REVIEW",
        )
        .order_by(IngestionJob.created_at.desc())
    )
    if waiting_job is not None:
        celery_app.send_task(
            "app.worker.tasks.resume_ingestion", args=[waiting_job.job_id]
        )
    return {
        "document_id": str(document.id),
        "status": document.status,
        "published_at": document.published_at,
    }
