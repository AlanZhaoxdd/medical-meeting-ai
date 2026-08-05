from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import (
    AuthContext,
    CurrentUserDependency,
    require_kb_access,
    require_role,
)
from app.core.exceptions import ConflictError, NotFoundError
from app.db.session import get_session
from app.models.kb import (
    Document,
    ExtractionTemplate,
    ExtractionTemplateVersion,
    KnowledgeBase,
    KnowledgeItem,
)
from app.schemas.kb import (
    KnowledgeBaseCreate,
    KnowledgeBaseRead,
    KnowledgeBaseUpdate,
    Role,
    TemplateCreate,
    TemplateRead,
)
from app.services.audit import record_audit
from app.services.storage import ObjectStorage
from app.services.vector_store import VectorStore

router = APIRouter(prefix="/knowledge-bases", tags=["知识库"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
AdminDependency = Annotated[AuthContext, Depends(require_role(Role.ADMIN))]

DEFAULT_FIELDS = [
    "participants",
    "topics",
    "insights",
    "consensus",
    "disagreements",
    "evidence_claims",
    "evidence_gaps",
    "action_items",
]


def serialize_kb(
    kb: KnowledgeBase, *, document_count: int = 0, knowledge_count: int = 0
) -> KnowledgeBaseRead:
    return KnowledgeBaseRead(
        id=str(kb.id),
        organization_id=str(kb.organization_id),
        name=kb.name,
        description=kb.description,
        default_template_id=str(kb.default_template_id) if kb.default_template_id else None,
        status=kb.status,
        document_count=document_count,
        published_knowledge_count=knowledge_count,
        created_by=str(kb.created_by),
        created_at=kb.created_at,
        updated_at=kb.updated_at,
    )


async def _create_default_template(
    session: AsyncSession, kb: KnowledgeBase, current: AuthContext
) -> ExtractionTemplate:
    template = ExtractionTemplate(
        organization_id=current.organization_id,
        knowledge_base_id=kb.id,
        name="默认医药会议模板",
        description="KB v1 服务端预定义字段全集",
        latest_version=1,
        created_by=current.user_id,
    )
    session.add(template)
    await session.flush()
    session.add(
        ExtractionTemplateVersion(
            template_id=template.id,
            version=1,
            fields=DEFAULT_FIELDS,
            created_by=current.user_id,
        )
    )
    kb.default_template_id = template.id
    return template


@router.get("", response_model=list[KnowledgeBaseRead])
async def list_knowledge_bases(
    session: SessionDependency, current: CurrentUserDependency
) -> list[KnowledgeBaseRead]:
    document_count = (
        select(func.count(Document.id))
        .where(
            Document.knowledge_base_id == KnowledgeBase.id,
            Document.deleted_at.is_(None),
        )
        .correlate(KnowledgeBase)
        .scalar_subquery()
    )
    knowledge_count = (
        select(func.count(KnowledgeItem.id))
        .where(
            KnowledgeItem.knowledge_base_id == KnowledgeBase.id,
            KnowledgeItem.publication_status == "PUBLISHED",
        )
        .correlate(KnowledgeBase)
        .scalar_subquery()
    )
    rows = (
        await session.execute(
            select(KnowledgeBase, document_count, knowledge_count)
            .where(
                KnowledgeBase.organization_id == current.organization_id,
                KnowledgeBase.deleted_at.is_(None),
            )
            .order_by(KnowledgeBase.updated_at.desc())
        )
    ).all()
    return [
        serialize_kb(kb, document_count=int(docs), knowledge_count=int(items))
        for kb, docs, items in rows
    ]


@router.post("", response_model=KnowledgeBaseRead, status_code=status.HTTP_201_CREATED)
async def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    session: SessionDependency,
    current: AdminDependency,
) -> KnowledgeBaseRead:
    kb = KnowledgeBase(
        organization_id=current.organization_id,
        name=payload.name,
        description=payload.description,
        status="active",
        created_by=current.user_id,
    )
    session.add(kb)
    try:
        await session.flush()
        await _create_default_template(session, kb, current)
        await record_audit(
            session,
            organization_id=current.organization_id,
            actor_id=current.user_id,
            action="kb.create",
            resource_type="knowledge_base",
            resource_id=str(kb.id),
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("knowledge_base_name_exists", "知识库名称已存在") from exc
    await session.refresh(kb)
    return serialize_kb(kb)


@router.get("/{kb_id}", response_model=KnowledgeBaseRead)
async def get_knowledge_base(
    kb_id: UUID, session: SessionDependency, current: CurrentUserDependency
) -> KnowledgeBaseRead:
    return serialize_kb(await require_kb_access(session, current, kb_id))


@router.patch("/{kb_id}", response_model=KnowledgeBaseRead)
async def update_knowledge_base(
    kb_id: UUID,
    payload: KnowledgeBaseUpdate,
    session: SessionDependency,
    current: AdminDependency,
) -> KnowledgeBaseRead:
    kb = await require_kb_access(session, current, kb_id)
    for field_name, value in payload.model_dump(exclude_unset=True).items():
        if field_name == "default_template_id" and value is not None:
            value = UUID(value)
            template_exists = await session.scalar(
                select(ExtractionTemplate.id).where(
                    ExtractionTemplate.id == value,
                    ExtractionTemplate.knowledge_base_id == kb.id,
                    ExtractionTemplate.deleted_at.is_(None),
                )
            )
            if template_exists is None:
                raise NotFoundError("模板", "template_not_found")
        setattr(kb, field_name, value)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("knowledge_base_name_exists", "知识库名称已存在") from exc
    await session.refresh(kb)
    return serialize_kb(kb)


@router.delete("/{kb_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_base(
    kb_id: UUID,
    session: SessionDependency,
    current: AdminDependency,
    purge: bool = Query(default=False),
) -> Response:
    kb = await require_kb_access(session, current, kb_id)
    if purge:
        documents = (
            await session.scalars(
                select(Document).where(Document.knowledge_base_id == kb.id)
            )
        ).all()
        for document in documents:
            await ObjectStorage().delete(document.minio_object_key)
            await VectorStore().delete_document(str(document.id))
        await session.delete(kb)
        action = "kb.purge"
    else:
        from datetime import datetime, timezone

        kb.deleted_at = datetime.now(timezone.utc)
        action = "kb.delete"
    await record_audit(
        session,
        organization_id=current.organization_id,
        actor_id=current.user_id,
        action=action,
        resource_type="knowledge_base",
        resource_id=str(kb_id),
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _template_read(
    session: AsyncSession, template: ExtractionTemplate
) -> TemplateRead:
    version = await session.scalar(
        select(ExtractionTemplateVersion).where(
            ExtractionTemplateVersion.template_id == template.id,
            ExtractionTemplateVersion.version == template.latest_version,
        )
    )
    if version is None:
        raise NotFoundError("模板版本", "template_version_not_found")
    return TemplateRead(
        id=str(template.id),
        knowledge_base_id=str(template.knowledge_base_id),
        name=template.name,
        description=template.description,
        fields=version.fields,
        version=version.version,
        created_at=template.created_at,
    )


@router.get("/{kb_id}/templates", response_model=list[TemplateRead])
async def list_templates(
    kb_id: UUID, session: SessionDependency, current: CurrentUserDependency
) -> list[TemplateRead]:
    await require_kb_access(session, current, kb_id)
    templates = (
        await session.scalars(
            select(ExtractionTemplate)
            .where(
                ExtractionTemplate.knowledge_base_id == kb_id,
                ExtractionTemplate.organization_id == current.organization_id,
                ExtractionTemplate.deleted_at.is_(None),
            )
            .order_by(ExtractionTemplate.created_at)
        )
    ).all()
    return [await _template_read(session, template) for template in templates]


@router.post(
    "/{kb_id}/templates", response_model=TemplateRead, status_code=status.HTTP_201_CREATED
)
async def create_template(
    kb_id: UUID,
    payload: TemplateCreate,
    session: SessionDependency,
    current: AdminDependency,
) -> TemplateRead:
    await require_kb_access(session, current, kb_id)
    template = ExtractionTemplate(
        organization_id=current.organization_id,
        knowledge_base_id=kb_id,
        name=payload.name,
        description=payload.description,
        latest_version=1,
        created_by=current.user_id,
    )
    session.add(template)
    try:
        await session.flush()
        session.add(
            ExtractionTemplateVersion(
                template_id=template.id,
                version=1,
                fields=payload.fields,
                created_by=current.user_id,
            )
        )
        await record_audit(
            session,
            organization_id=current.organization_id,
            actor_id=current.user_id,
            action="template.create",
            resource_type="extraction_template",
            resource_id=str(template.id),
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("template_name_exists", "模板名称已存在") from exc
    await session.refresh(template)
    return await _template_read(session, template)


@router.get("/{kb_id}/templates/{template_id}", response_model=TemplateRead)
async def get_template(
    kb_id: UUID,
    template_id: UUID,
    session: SessionDependency,
    current: CurrentUserDependency,
) -> TemplateRead:
    await require_kb_access(session, current, kb_id)
    template = await session.scalar(
        select(ExtractionTemplate).where(
            ExtractionTemplate.id == template_id,
            ExtractionTemplate.knowledge_base_id == kb_id,
            ExtractionTemplate.organization_id == current.organization_id,
            ExtractionTemplate.deleted_at.is_(None),
        )
    )
    if template is None:
        raise NotFoundError("模板", "template_not_found")
    return await _template_read(session, template)
