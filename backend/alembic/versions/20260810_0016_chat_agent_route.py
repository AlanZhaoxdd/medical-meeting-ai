"""add agent route column to chat messages

Revision ID: 20260810_0016
Revises: 20260810_0015
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260810_0016"
down_revision: str | None = "20260810_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column("route", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_messages", "route")
