"""meeting import review revisions and confirmation

Revision ID: 20260802_0006
Revises: 20260802_0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260802_0006"
down_revision: str | None = "20260802_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE meeting_import_status ADD VALUE IF NOT EXISTS 'CONFIRMED'")
    op.execute(
        """DO $$ BEGIN CREATE TYPE transcript_revision_status AS ENUM
        ('DRAFT', 'CONFIRMED', 'SUPERSEDED');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$"""
    )
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS active_transcript_revision_id UUID")
    op.execute("ALTER TABLE meeting_imports ADD COLUMN IF NOT EXISTS confirmed_revision_id UUID")
    op.execute("ALTER TABLE meeting_imports ADD COLUMN IF NOT EXISTS meeting_id UUID")
    op.execute(
        "ALTER TABLE meeting_imports ADD COLUMN IF NOT EXISTS "
        "confirmation_idempotency_key VARCHAR(255)"
    )
    op.create_unique_constraint(
        "uq_meeting_import_confirmation_key", "meeting_imports", ["confirmation_idempotency_key"]
    )
    op.create_foreign_key(
        "fk_import_meeting", "meeting_imports", "meetings", ["meeting_id"], ["id"]
    )
    op.create_table(
        "transcript_revisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("import_id", postgresql.UUID(as_uuid=True)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "status",
            postgresql.ENUM(
                "DRAFT",
                "CONFIRMED",
                "SUPERSEDED",
                name="transcript_revision_status",
                create_type=False,
            ),
            nullable=False,
            server_default="DRAFT",
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("confirmed_by", postgresql.UUID(as_uuid=True)),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["import_id"], ["meeting_imports.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["confirmed_by"], ["users.id"]),
        sa.UniqueConstraint("document_id", "version", name="uq_transcript_revision_version"),
    )
    # The document FK is created after its target table exists.
    op.create_foreign_key(
        "fk_documents_active_transcript_revision",
        "documents",
        "transcript_revisions",
        ["active_transcript_revision_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_import_confirmed_revision",
        "meeting_imports",
        "transcript_revisions",
        ["confirmed_revision_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_transcript_revisions_document_id", "transcript_revisions", ["document_id"])
    op.create_index("ix_transcript_revisions_import_id", "transcript_revisions", ["import_id"])
    op.create_index(
        "ix_transcript_revisions_document_status", "transcript_revisions", ["document_id", "status"]
    )
    op.create_table(
        "transcript_revision_blocks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("block_id", sa.String(100), nullable=False),
        sa.Column("block_type", sa.String(32), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column(
            "heading_path",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("table_markdown", sa.Text()),
        sa.Column("page_number", sa.Integer()),
        sa.Column("slide_number", sa.Integer()),
        sa.Column("speaker", sa.String(255)),
        sa.Column("start_ms", sa.Integer()),
        sa.Column("end_ms", sa.Integer()),
        sa.Column("bbox", postgresql.JSONB()),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["revision_id"], ["transcript_revisions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("revision_id", "block_id", name="uq_revision_block_id"),
        sa.UniqueConstraint("revision_id", "order", name="uq_revision_block_order"),
    )
    op.create_index(
        "ix_transcript_revision_blocks_revision_id", "transcript_revision_blocks", ["revision_id"]
    )
    op.create_index(
        "ix_transcript_revision_blocks_content_hash", "transcript_revision_blocks", ["content_hash"]
    )
    op.create_table(
        "batch_replace_operations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("replacement", sa.Text(), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False, server_default="FULL"),
        sa.Column("case_sensitive", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("match_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "affected_block_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "snapshots", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["revision_id"], ["transcript_revisions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
    )
    op.create_index(
        "ix_batch_replace_operations_revision_id", "batch_replace_operations", ["revision_id"]
    )
    op.create_index(
        "ix_batch_replace_revision_created",
        "batch_replace_operations",
        ["revision_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_import_confirmed_revision", "meeting_imports", type_="foreignkey")
    op.drop_index("ix_batch_replace_revision_created", table_name="batch_replace_operations")
    op.drop_index("ix_batch_replace_operations_revision_id", table_name="batch_replace_operations")
    op.drop_table("batch_replace_operations")
    op.drop_index(
        "ix_transcript_revision_blocks_content_hash", table_name="transcript_revision_blocks"
    )
    op.drop_index(
        "ix_transcript_revision_blocks_revision_id", table_name="transcript_revision_blocks"
    )
    op.drop_table("transcript_revision_blocks")
    op.drop_index("ix_transcript_revisions_document_status", table_name="transcript_revisions")
    op.drop_index("ix_transcript_revisions_import_id", table_name="transcript_revisions")
    op.drop_index("ix_transcript_revisions_document_id", table_name="transcript_revisions")
    op.drop_constraint("fk_documents_active_transcript_revision", "documents", type_="foreignkey")
    op.drop_table("transcript_revisions")
    op.drop_constraint("fk_import_meeting", "meeting_imports", type_="foreignkey")
    op.drop_constraint("uq_meeting_import_confirmation_key", "meeting_imports", type_="unique")
    op.drop_column("meeting_imports", "confirmation_idempotency_key")
    op.drop_column("meeting_imports", "meeting_id")
    op.drop_column("meeting_imports", "confirmed_revision_id")
    op.drop_column("documents", "active_transcript_revision_id")
    op.execute("DROP TYPE IF EXISTS transcript_revision_status")
