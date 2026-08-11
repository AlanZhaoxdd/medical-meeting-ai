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
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _enum_values(enum_class: type[enum.Enum]) -> list[str]:
    return [member.value for member in enum_class]


class ExportType(str, enum.Enum):
    TEXT = "text"
    PPT = "ppt"
    CHART = "chart"


class ExportFileFormat(str, enum.Enum):
    DOCX = "docx"
    PDF = "pdf"
    PPTX = "pptx"
    PNG = "png"
    SVG = "svg"


class ExportStatus(str, enum.Enum):
    PENDING = "PENDING"
    ANALYZING = "ANALYZING"
    GENERATING = "GENERATING"
    RENDERING = "RENDERING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ExportRecord(Base):
    """Persisted async export task + generated file metadata.

    Binary files are stored in MinIO (storage_key); PostgreSQL only keeps
    metadata. ``config`` snapshots the export options (including an edited PPT
    outline) so a finished task never changes after the fact.
    """

    __tablename__ = "export_records"
    __table_args__ = (
        Index("ix_export_records_meeting_created", "meeting_id", "created_at"),
        Index("ix_export_records_org_status", "organization_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    meeting_id: Mapped[UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    analysis_version: Mapped[int] = mapped_column(Integer, nullable=False)
    export_type: Mapped[ExportType] = mapped_column(
        Enum(ExportType, name="export_type", values_callable=_enum_values), nullable=False
    )
    file_format: Mapped[Optional[ExportFileFormat]] = mapped_column(
        Enum(ExportFileFormat, name="export_file_format", values_callable=_enum_values),
        nullable=True,
    )
    status: Mapped[ExportStatus] = mapped_column(
        Enum(ExportStatus, name="export_status", values_callable=_enum_values),
        nullable=False,
        default=ExportStatus.PENDING,
        server_default=ExportStatus.PENDING.value,
    )
    progress: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    current_stage: Mapped[str] = mapped_column(
        String(64), nullable=False, default="pending", server_default="pending"
    )
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    file_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    storage_key: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    content_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    file_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    attempt_token: Mapped[Optional[UUID]] = mapped_column(Uuid(as_uuid=True), nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_by: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
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
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class PptOutline(Base):
    """Editable PPT outline draft tied to one meeting/analysis version."""

    __tablename__ = "ppt_outlines"
    __table_args__ = (
        UniqueConstraint(
            "meeting_id", "analysis_version", name="uq_ppt_outline_meeting_version"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    meeting_id: Mapped[UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    analysis_version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    subtitle: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    theme: Mapped[str] = mapped_column(String(32), nullable=False, default="formal")
    slides: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    generated_by: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
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


class ChartSpec(Base):
    """Validated chart data (LLM classification + deterministic aggregation)."""

    __tablename__ = "chart_specs"
    __table_args__ = (
        UniqueConstraint(
            "meeting_id",
            "analysis_version",
            "chart_type",
            "target_id",
            name="uq_chart_spec_meeting_version_type_target",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    meeting_id: Mapped[UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    analysis_version: Mapped[int] = mapped_column(Integer, nullable=False)
    chart_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("meeting_questions.id", ondelete="CASCADE"), nullable=True
    )
    target_label: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    subtitle: Mapped[str] = mapped_column(String(1000), nullable=False, default="")
    metric: Mapped[str] = mapped_column(String(100), nullable=False)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    invalid_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ChartCutpointTemplate(Base):
    """Organization-owned, versioned definitions for medical chart cut-points."""

    __tablename__ = "chart_cutpoint_templates"
    __table_args__ = (UniqueConstraint("organization_id", "template_key", name="uq_chart_cutpoint_template_org_key"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    template_key: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    latest_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class ChartCutpointTemplateVersion(Base):
    __tablename__ = "chart_cutpoint_template_versions"
    __table_args__ = (UniqueConstraint("template_id", "version", name="uq_chart_cutpoint_template_version"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    template_id: Mapped[UUID] = mapped_column(ForeignKey("chart_cutpoint_templates.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    items: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    created_by: Mapped[Optional[UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ChartExtractionSnapshot(Base):
    """Grounded observations shared by bar and pie chart generations."""

    __tablename__ = "chart_extraction_snapshots"
    __table_args__ = (UniqueConstraint("meeting_id", "analysis_version", "template_id", "template_version", name="uq_chart_snapshot_meeting_template_version"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    meeting_id: Mapped[UUID] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_id: Mapped[UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True)
    analysis_version: Mapped[int] = mapped_column(Integer, nullable=False)
    template_id: Mapped[UUID] = mapped_column(ForeignKey("chart_cutpoint_templates.id", ondelete="CASCADE"), nullable=False)
    template_version: Mapped[int] = mapped_column(Integer, nullable=False)
    observations: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    covered_keys: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    excluded_observations: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class ChartSelection(Base):
    """User-selected chart ids used by PPT export for a meeting analysis version."""

    __tablename__ = "chart_selections"
    __table_args__ = (
        UniqueConstraint(
            "meeting_id",
            "analysis_version",
            name="uq_chart_selection_meeting_version",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    meeting_id: Mapped[UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    analysis_version: Mapped[int] = mapped_column(Integer, nullable=False)
    chart_ids: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
