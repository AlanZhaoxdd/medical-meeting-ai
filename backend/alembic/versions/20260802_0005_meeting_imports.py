"""single file meeting imports

Revision ID: 20260802_0005
Revises: 20260731_0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260802_0005"
down_revision: str | None = "20260731_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    status_enum = postgresql.ENUM(
        "UPLOADED",
        "PARSING",
        "EXTRACTING_METADATA",
        "READY_FOR_REVIEW",
        "FAILED",
        "CANCELLED",
        name="meeting_import_status",
        create_type=False,
    )
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE meeting_import_status AS ENUM (
                'UPLOADED', 'PARSING', 'EXTRACTING_METADATA',
                'READY_FOR_REVIEW', 'FAILED', 'CANCELLED'
            );
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$
        """
    )
    op.create_table(
        "meeting_imports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("safe_filename", sa.String(length=200), nullable=False),
        sa.Column("mime_type", sa.String(length=200), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", status_enum, nullable=False, server_default="UPLOADED"),
        sa.Column("current_step", sa.String(length=64), nullable=False, server_default="upload"),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("failure_code", sa.String(length=100), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("can_retry", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "cancel_requested", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("attempt_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["knowledge_base_id"], ["knowledge_bases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
    )
    op.create_index(
        "ix_meeting_imports_org_kb_created",
        "meeting_imports",
        ["organization_id", "knowledge_base_id", "created_at"],
    )
    op.create_index(
        "ix_meeting_imports_org_status", "meeting_imports", ["organization_id", "status"]
    )
    op.create_index("ix_meeting_imports_sha256", "meeting_imports", ["sha256"])
    op.create_index(
        "uq_active_meeting_import_document",
        "meeting_imports",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('UPLOADED', 'PARSING', 'EXTRACTING_METADATA')"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_active_meeting_import_document", table_name="meeting_imports")
    op.drop_index("ix_meeting_imports_sha256", table_name="meeting_imports")
    op.drop_index("ix_meeting_imports_org_status", table_name="meeting_imports")
    op.drop_index("ix_meeting_imports_org_kb_created", table_name="meeting_imports")
    op.drop_table("meeting_imports")
    op.execute("DROP TYPE IF EXISTS meeting_import_status")
