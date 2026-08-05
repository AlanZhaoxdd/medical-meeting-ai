"""create meetings table

Revision ID: 20260727_0001
Revises:
Create Date: 2026-07-27 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Optional

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260727_0001"
down_revision: Optional[str] = None
branch_labels: Optional[Sequence[str]] = None
depends_on: Optional[Sequence[str]] = None


meeting_status = sa.Enum(
    "draft",
    "published",
    "in_progress",
    "completed",
    "cancelled",
    "archived",
    name="meeting_status",
)
analysis_status = sa.Enum(
    "not_ready",
    "ready",
    "queued",
    "processing",
    "succeeded",
    "failed",
    "cancelled",
    name="analysis_status",
)


def upgrade() -> None:
    op.create_table(
        "meetings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("location", sa.String(length=500), nullable=True),
        sa.Column("online_url", sa.String(length=2048), nullable=True),
        sa.Column("organizer", sa.String(length=255), nullable=True),
        sa.Column("topic", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("cover_url", sa.String(length=2048), nullable=True),
        sa.Column(
            "meeting_status", meeting_status, nullable=False, server_default=sa.text("'draft'")
        ),
        sa.Column(
            "analysis_status",
            analysis_status,
            nullable=False,
            server_default=sa.text("'not_ready'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_meetings_status_starts_at", "meetings", ["meeting_status", "starts_at"], unique=False
    )
    op.create_index("ix_meetings_analysis_status", "meetings", ["analysis_status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_meetings_analysis_status", table_name="meetings")
    op.drop_index("ix_meetings_status_starts_at", table_name="meetings")
    op.drop_table("meetings")
    bind = op.get_bind()
    analysis_status.drop(bind, checkfirst=True)
    meeting_status.drop(bind, checkfirst=True)
