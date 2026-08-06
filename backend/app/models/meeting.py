from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
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


class MeetingStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"


class AnalysisStatus(str, enum.Enum):
    NOT_READY = "not_ready"
    READY = "ready"
    QUEUED = "queued"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class VerificationStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    CONFIRMED = "confirmed"


class MeetingQuestionType(str, enum.Enum):
    CUT_POINT = "cut_point"
    OPEN_ENDED = "open_ended"


class AiTaskType(str, enum.Enum):
    QUESTION_GENERATION = "QUESTION_GENERATION"
    ANALYSIS = "ANALYSIS"


class AiTaskStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RETRYING = "RETRYING"
    PENDING_REVIEW = "PENDING_REVIEW"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class Meeting(Base):
    __tablename__ = "meetings"
    __table_args__ = (
        Index("ix_meetings_status_starts_at", "meeting_status", "starts_at"),
        Index("ix_meetings_analysis_status", "analysis_status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    knowledge_base_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("knowledge_bases.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    location: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    online_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    organizer: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    topic: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    meeting_info: Mapped[dict[str, Optional[str]]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    cover_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    meeting_status: Mapped[MeetingStatus] = mapped_column(
        Enum(MeetingStatus, name="meeting_status", values_callable=_enum_values),
        nullable=False,
        default=MeetingStatus.DRAFT,
        server_default=MeetingStatus.DRAFT.value,
    )
    analysis_status: Mapped[AnalysisStatus] = mapped_column(
        Enum(AnalysisStatus, name="analysis_status", values_callable=_enum_values),
        nullable=False,
        default=AnalysisStatus.NOT_READY,
        server_default=AnalysisStatus.NOT_READY.value,
    )
    verification_status: Mapped[VerificationStatus] = mapped_column(
        Enum(
            VerificationStatus,
            name="verification_status",
            values_callable=_enum_values,
        ),
        nullable=False,
        default=VerificationStatus.PENDING,
        server_default=VerificationStatus.PENDING.value,
    )
    verification_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    verification_confirmed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    verification_confirmed_by: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    analysis_requested_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class AiTask(Base):
    __tablename__ = "ai_tasks"
    __table_args__ = (
        UniqueConstraint(
            "meeting_id", "task_type", "source_version", name="uq_ai_task_meeting_type_source"
        ),
        Index("ix_ai_tasks_meeting_created", "meeting_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    meeting_id: Mapped[UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    task_type: Mapped[AiTaskType] = mapped_column(String(64), nullable=False)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    thread_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[AiTaskStatus] = mapped_column(
        String(32),
        nullable=False,
        default=AiTaskStatus.QUEUED,
        server_default=AiTaskStatus.QUEUED.value,
    )
    current_stage: Mapped[str] = mapped_column(
        String(64), nullable=False, default="queued", server_default="queued"
    )
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cutpoint_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    open_question_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=2, server_default="2")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    model_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    prompt_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    attempt_token: Mapped[Optional[UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class QuestionEvidence(Base):
    __tablename__ = "question_evidences"
    __table_args__ = (Index("uq_question_evidence_chunk", "question_id", "chunk_id", unique=True),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    question_id: Mapped[UUID] = mapped_column(
        ForeignKey("meeting_questions.id", ondelete="CASCADE"), nullable=False
    )
    chunk_id: Mapped[str] = mapped_column(String(100), nullable=False)
    document_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=True
    )
    block_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    retrieval_query: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    relevance_score: Mapped[Optional[float]] = mapped_column(nullable=True)
    vector_score: Mapped[Optional[float]] = mapped_column(nullable=True)
    keyword_score: Mapped[Optional[float]] = mapped_column(nullable=True)
    rerank_score: Mapped[Optional[float]] = mapped_column(nullable=True)
    source_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="knowledge_base", server_default="knowledge_base"
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MeetingQuestion(Base):
    __tablename__ = "meeting_questions"
    __table_args__ = (
        Index(
            "uq_active_meeting_question_content",
            "meeting_id",
            "question_type",
            text("lower(btrim(content))"),
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_meeting_questions_meeting_type", "meeting_id", "question_type"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    meeting_id: Mapped[UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_type: Mapped[MeetingQuestionType] = mapped_column(
        Enum(MeetingQuestionType, name="meeting_question_type", values_callable=_enum_values),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="manual", server_default="manual"
    )
    confidence: Mapped[Optional[float]] = mapped_column(nullable=True)
    topic: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    origin: Mapped[str] = mapped_column(
        String(32), nullable=False, default="USER_CREATED", server_default="USER_CREATED"
    )
    review_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="USER_EDITED", server_default="USER_EDITED"
    )
    support_score: Mapped[Optional[float]] = mapped_column(nullable=True)
    expected_answer_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    evidence_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Ranked position inside the selectable candidate pool (AI-generated only).
    # Manual questions keep NULL and are directly selectable without swapping.
    candidate_rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # True when the user picked this question as input for the AI analysis.
    analysis_selected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    generated_task_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("ai_tasks.id", ondelete="SET NULL"), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_by: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_by: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class MeetingAnalysisRun(Base):
    """Persisted structured AI analysis result for one analysis task run."""

    __tablename__ = "meeting_analysis_runs"
    __table_args__ = (
        UniqueConstraint("meeting_id", "task_id", name="uq_analysis_run_meeting_task"),
        Index("ix_meeting_analysis_runs_meeting_created", "meeting_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    meeting_id: Mapped[UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    task_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_tasks.id", ondelete="CASCADE"), nullable=False
    )
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="SUCCEEDED")
    modules: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    sources: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    insufficient_notes: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
