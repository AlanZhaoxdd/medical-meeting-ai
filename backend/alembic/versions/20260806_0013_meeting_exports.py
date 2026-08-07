"""add meeting outcome export records, ppt outlines and chart specs

Revision ID: 20260806_0013
Revises: 20260806_0012
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260806_0013"
down_revision: str | None = "20260806_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(name: str, *values: str) -> postgresql.ENUM:
    # create_type=False prevents op.create_table() from re-emitting CREATE TYPE
    # after the explicit .create(bind, checkfirst=True) below.
    return postgresql.ENUM(*values, name=name, create_type=False)


def upgrade() -> None:
    export_type = _enum("export_type", "text", "ppt", "chart")
    export_format = _enum("export_file_format", "docx", "pdf", "pptx", "png", "svg")
    export_status = _enum(
        "export_status",
        "PENDING",
        "ANALYZING",
        "GENERATING",
        "RENDERING",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
    )
    export_type.create(op.get_bind(), checkfirst=True)
    export_format.create(op.get_bind(), checkfirst=True)
    export_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "export_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("analysis_version", sa.Integer(), nullable=False),
        sa.Column("export_type", export_type, nullable=False),
        sa.Column("file_format", export_format, nullable=True),
        sa.Column(
            "status",
            export_status,
            nullable=False,
            server_default=sa.text("'PENDING'"),
        ),
        sa.Column("progress", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "current_stage",
            sa.String(64),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("file_name", sa.String(255), nullable=True),
        sa.Column("storage_key", sa.String(1024), nullable=True),
        sa.Column("content_type", sa.String(128), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("config", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("attempt_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_export_records_meeting_created",
        "export_records",
        ["meeting_id", "created_at"],
    )
    op.create_index(
        "ix_export_records_org_status",
        "export_records",
        ["organization_id", "status"],
    )

    op.create_table(
        "ppt_outlines",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("analysis_version", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("subtitle", sa.String(500), nullable=True),
        sa.Column("theme", sa.String(32), nullable=False, server_default=sa.text("'formal'")),
        sa.Column("slides", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column(
            "generated_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "meeting_id", "analysis_version", name="uq_ppt_outline_meeting_version"
        ),
    )

    op.create_table(
        "chart_specs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("analysis_version", sa.Integer(), nullable=False),
        sa.Column("chart_type", sa.String(16), nullable=False),
        sa.Column(
            "target_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meeting_questions.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("target_label", sa.String(500), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("subtitle", sa.String(1000), nullable=False, server_default=sa.text("''")),
        sa.Column("metric", sa.String(100), nullable=False),
        sa.Column("spec", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("valid", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("invalid_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "meeting_id",
            "analysis_version",
            "chart_type",
            "target_id",
            name="uq_chart_spec_meeting_version_type_target",
        ),
    )


def downgrade() -> None:
    op.drop_table("chart_specs")
    op.drop_table("ppt_outlines")
    op.drop_table("export_records")
    sa.Enum(
        "PENDING",
        "ANALYZING",
        "GENERATING",
        "RENDERING",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
        name="export_status",
    ).drop(op.get_bind(), checkfirst=True)
    sa.Enum("docx", "pdf", "pptx", "png", "svg", name="export_file_format").drop(
        op.get_bind(), checkfirst=True
    )
    sa.Enum("text", "ppt", "chart", name="export_type").drop(op.get_bind(), checkfirst=True)
