"""associate meetings with an optional knowledge base

Revision ID: 20260730_0002
Revises: 20260727_0001
"""

import sqlalchemy as sa

from alembic import op

revision = "20260730_0002"
down_revision = "20260727_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("meetings", sa.Column("knowledge_base_id", sa.Uuid(), nullable=True))
    op.create_index(
        "ix_meetings_knowledge_base_id", "meetings", ["knowledge_base_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_meetings_knowledge_base_id", table_name="meetings")
    op.drop_column("meetings", "knowledge_base_id")
