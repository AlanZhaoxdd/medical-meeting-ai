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
    op.add_column(
        "documents",
        sa.Column("status", sa.String(32), nullable=True),
    )
    op.execute(
        """
        UPDATE documents
        SET status = CASE processing_status
            WHEN 'DRAFT_READY' THEN 'AWAITING_REVIEW'
            WHEN 'STORED' THEN 'UPLOADED'
            ELSE processing_status
        END
        """
    )
    op.alter_column("documents", "status", nullable=False)
    op.create_index("ix_documents_status", "documents", ["status"])
    op.drop_column("documents", "processing_status")
    op.drop_column("documents", "review_status")
    op.drop_column("documents", "publication_status")


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
