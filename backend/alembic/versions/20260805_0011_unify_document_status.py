"""unify document lifecycle into a single status field

Revision ID: 20260805_0011
Revises: 20260805_0010
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260805_0011"
down_revision: str | None = "20260805_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0003's Base.metadata.create_all() builds tables from the current model
    # metadata, so on a fresh database documents already has a unified
    # status column and index. Keep every step idempotent so this migration
    # works both from the legacy 3-column schema and from a fresh schema.
    op.execute("ALTER TABLE documents ADD COLUMN IF NOT EXISTS status VARCHAR(32)")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'documents' AND column_name = 'processing_status'
            ) THEN
                UPDATE documents
                SET status = CASE processing_status
                    WHEN 'DRAFT_READY' THEN 'AWAITING_REVIEW'
                    WHEN 'STORED' THEN 'UPLOADED'
                    ELSE processing_status
                END;
            END IF;
        END $$;
        """
    )
    op.execute("ALTER TABLE documents ALTER COLUMN status SET NOT NULL")
    op.execute("CREATE INDEX IF NOT EXISTS ix_documents_status ON documents (status)")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS processing_status")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS review_status")
    op.execute("ALTER TABLE documents DROP COLUMN IF EXISTS publication_status")


def downgrade() -> None:
    op.add_column(
        "documents",
        sa.Column("processing_status", sa.String(32), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("review_status", sa.String(32), nullable=True),
    )
    op.add_column(
        "documents",
        sa.Column("publication_status", sa.String(32), nullable=True),
    )
    op.execute(
        """
        UPDATE documents
        SET processing_status = CASE status
            WHEN 'AWAITING_REVIEW' THEN 'DRAFT_READY'
            ELSE status
        END,
        review_status = 'PENDING',
        publication_status = CASE status WHEN 'PUBLISHED' THEN 'PUBLISHED' ELSE 'DRAFT' END
        """
    )
    op.alter_column("documents", "processing_status", nullable=False)
    op.alter_column("documents", "review_status", nullable=False)
    op.alter_column("documents", "publication_status", nullable=False)
    op.drop_index("ix_documents_status", table_name="documents")
    op.drop_column("documents", "status")
