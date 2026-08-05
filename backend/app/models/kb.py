from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _enum_values(enum_class: type[enum.Enum]) -> list[str]:
    return [member.value for member in enum_class]


class MeetingImportStatus(str, enum.Enum):
    """Lifecycle for the lightweight meeting import flow.

    This is intentionally separate from the historical ingestion state machine:
    meeting import stops after deterministic metadata extraction and never builds
    chunks, embeddings or knowledge items.
    """

    UPLOADED = "UPLOADED"
    PARSING = "PARSING"
    EXTRACTING_METADATA = "EXTRACTING_METADATA"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    CONFIRMED = "CONFIRMED"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Organization(Base, TimestampMixin):
    __tablename__ = "organizations"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class OrganizationMembership(Base, TimestampMixin):
    __tablename__ = "organization_memberships"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_membership_org_user"),
        Index("ix_memberships_user_status", "user_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")


class KnowledgeBase(Base, TimestampMixin):
    __tablename__ = "knowledge_bases"
    __table_args__ = (
        Index(
            "uq_active_kb_org_name",
            "organization_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    default_template_id: Mapped[Optional[UUID]] = mapped_column(Uuid(as_uuid=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class Document(Base, TimestampMixin):
    __tablename__ = "documents"
    __table_args__ = (
        Index(
            "ix_documents_kb_hash_active",
            "organization_id",
            "knowledge_base_id",
            "sha256",
            "deleted_at",
        ),
        Index("ix_documents_kb_created", "knowledge_base_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    knowledge_base_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False
    )
    meeting_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("meetings.id"))
    active_transcript_revision_id: Mapped[Optional[UUID]] = mapped_column(
        Uuid(as_uuid=True), nullable=True, index=True
    )
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    safe_filename: Mapped[str] = mapped_column(String(200), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(200), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    minio_bucket: Mapped[str] = mapped_column(String(128), nullable=False)
    minio_object_key: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    previous_version_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("documents.id"))
    parser_name: Mapped[str] = mapped_column(String(100), nullable=False, default="docling")
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    template_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    template_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="UPLOADED", index=True
    )
    # Internal technical flag used for publish/confirmation gates. It is not a
    # user-facing lifecycle state and must never be shown as one.
    vector_sync_status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    error_code: Mapped[Optional[str]] = mapped_column(String(100))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class MeetingImport(Base, TimestampMixin):
    """A single-file import request tied to an existing document or new document."""

    __tablename__ = "meeting_imports"
    __table_args__ = (
        Index(
            "ix_meeting_imports_org_kb_created",
            "organization_id",
            "knowledge_base_id",
            "created_at",
        ),
        Index("ix_meeting_imports_org_status", "organization_id", "status"),
        Index(
            "uq_active_meeting_import_document",
            "document_id",
            unique=True,
            postgresql_where=text("status IN ('UPLOADED', 'PARSING', 'EXTRACTING_METADATA')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    knowledge_base_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    safe_filename: Mapped[str] = mapped_column(String(200), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(200), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[MeetingImportStatus] = mapped_column(
        Enum(
            MeetingImportStatus,
            name="meeting_import_status",
            values_callable=_enum_values,
        ),
        nullable=False,
        default=MeetingImportStatus.UPLOADED,
        server_default=MeetingImportStatus.UPLOADED.value,
    )
    current_step: Mapped[str] = mapped_column(String(64), nullable=False, default="upload")
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    failure_code: Mapped[Optional[str]] = mapped_column(String(100))
    failure_message: Mapped[Optional[str]] = mapped_column(Text)
    can_retry: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    attempt_token: Mapped[Optional[UUID]] = mapped_column(Uuid(as_uuid=True))
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    confirmed_revision_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey(
            "transcript_revisions.id",
            name="fk_import_confirmed_revision",
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
    )
    meeting_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("meetings.id", ondelete="SET NULL"), nullable=True
    )
    confirmation_idempotency_key: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, unique=True
    )


class DocumentBlock(Base):
    __tablename__ = "document_blocks"
    __table_args__ = (
        UniqueConstraint("document_id", "block_id", name="uq_document_block_id"),
        UniqueConstraint("document_id", "order", name="uq_document_block_order"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    block_id: Mapped[str] = mapped_column(String(100), nullable=False)
    block_type: Mapped[str] = mapped_column(String(32), nullable=False)
    order: Mapped[int] = mapped_column(Integer, nullable=False)
    heading_path: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    table_markdown: Mapped[Optional[str]] = mapped_column(Text)
    page_number: Mapped[Optional[int]] = mapped_column(Integer)
    slide_number: Mapped[Optional[int]] = mapped_column(Integer)
    speaker: Mapped[Optional[str]] = mapped_column(String(255))
    start_ms: Mapped[Optional[int]] = mapped_column(Integer)
    end_ms: Mapped[Optional[int]] = mapped_column(Integer)
    bbox: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


class TranscriptRevisionStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    CONFIRMED = "CONFIRMED"
    SUPERSEDED = "SUPERSEDED"


class TranscriptRevision(Base, TimestampMixin):
    __tablename__ = "transcript_revisions"
    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_transcript_revision_version"),
        Index("ix_transcript_revisions_document_status", "document_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    import_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("meeting_imports.id", ondelete="SET NULL"), nullable=True, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[TranscriptRevisionStatus] = mapped_column(
        Enum(
            TranscriptRevisionStatus,
            name="transcript_revision_status",
            values_callable=_enum_values,
        ),
        nullable=False,
        default=TranscriptRevisionStatus.DRAFT,
        server_default=TranscriptRevisionStatus.DRAFT.value,
    )
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    confirmed_by: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id"))
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class TranscriptRevisionBlock(Base):
    __tablename__ = "transcript_revision_blocks"
    __table_args__ = (
        UniqueConstraint("revision_id", "block_id", name="uq_revision_block_id"),
        UniqueConstraint("revision_id", "order", name="uq_revision_block_order"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("transcript_revisions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    block_id: Mapped[str] = mapped_column(String(100), nullable=False)
    block_type: Mapped[str] = mapped_column(String(32), nullable=False)
    order: Mapped[int] = mapped_column(Integer, nullable=False)
    heading_path: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    table_markdown: Mapped[Optional[str]] = mapped_column(Text)
    page_number: Mapped[Optional[int]] = mapped_column(Integer)
    slide_number: Mapped[Optional[int]] = mapped_column(Integer)
    speaker: Mapped[Optional[str]] = mapped_column(String(255))
    start_ms: Mapped[Optional[int]] = mapped_column(Integer)
    end_ms: Mapped[Optional[int]] = mapped_column(Integer)
    bbox: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


class BatchReplaceOperation(Base):
    __tablename__ = "batch_replace_operations"
    __table_args__ = (Index("ix_batch_replace_revision_created", "revision_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("transcript_revisions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    replacement: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="FULL")
    case_sensitive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    match_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    affected_block_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    snapshots: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_document_chunk_index"),
        Index(
            "ix_chunks_scope_publication",
            "organization_id",
            "knowledge_base_id",
            "publication_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    chunk_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    knowledge_base_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    meeting_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("meetings.id"))
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    heading_path: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    content_type: Mapped[str] = mapped_column(String(32), nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source_block_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    source_locator: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_version: Mapped[str] = mapped_column(String(100), nullable=False)
    chunker_version: Mapped[str] = mapped_column(String(64), nullable=False)
    publication_status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class KnowledgeItem(Base, TimestampMixin):
    __tablename__ = "knowledge_items"
    __table_args__ = (
        Index(
            "ix_knowledge_items_scope_review",
            "organization_id",
            "knowledge_base_id",
            "document_id",
            "review_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    knowledge_base_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    meeting_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("meetings.id"))
    item_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    normalized_content: Mapped[str] = mapped_column(Text, nullable=False)
    structured_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    source_refs: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    extraction_template_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    extraction_template_version: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    langfuse_trace_id: Mapped[Optional[str]] = mapped_column(String(200))
    review_status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    reviewer_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id"))
    review_comment: Mapped[Optional[str]] = mapped_column(Text)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    publication_status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ExtractionTemplate(Base, TimestampMixin):
    __tablename__ = "extraction_templates"
    __table_args__ = (
        UniqueConstraint("knowledge_base_id", "name", name="uq_extraction_template_kb_name"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    knowledge_base_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    latest_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class ExtractionTemplateVersion(Base):
    __tablename__ = "extraction_template_versions"
    __table_args__ = (UniqueConstraint("template_id", "version", name="uq_template_version"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    template_id: Mapped[UUID] = mapped_column(
        ForeignKey("extraction_templates.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    fields: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IngestionJob(Base, TimestampMixin):
    __tablename__ = "ingestion_jobs"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    job_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    knowledge_base_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    current_node: Mapped[str] = mapped_column(String(64), nullable=False)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[Optional[str]] = mapped_column(String(100))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    result_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class EventBase:
    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    actor_id: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id"))
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(100), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AuditEvent(Base, EventBase):
    __tablename__ = "audit_events"
    action: Mapped[str] = mapped_column(String(100), nullable=False)


class ReviewEvent(Base, EventBase):
    __tablename__ = "review_events"
    action: Mapped[str] = mapped_column(String(100), nullable=False)


class OutboxEvent(Base):
    __tablename__ = "outbox_events"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class RetrievalLog(Base):
    __tablename__ = "retrieval_logs"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    knowledge_base_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    filters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    candidates: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    included_drafts: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class NodeExecution(Base):
    __tablename__ = "node_executions"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    job_id: Mapped[str] = mapped_column(String(100), nullable=False)
    node_name: Mapped[str] = mapped_column(String(64), nullable=False)
    input_version: Mapped[str] = mapped_column(String(255), nullable=False)
    result_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BenchmarkRun(Base, TimestampMixin):
    """Admin-triggered retrieval/ingestion benchmark result."""

    __tablename__ = "benchmark_runs"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    environment: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
