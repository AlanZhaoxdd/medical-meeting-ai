"""add chart selections for PPT export

Revision ID: 20260810_0015
Revises: 20260809_0014
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260810_0015"
down_revision: str | None = "20260809_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chart_selections",
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
            nullable=True,
        ),
        sa.Column("analysis_version", sa.Integer(), nullable=False),
        sa.Column("chart_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "meeting_id",
            "analysis_version",
            name="uq_chart_selection_meeting_version",
        ),
    )
    op.create_index(
        "ix_chart_selections_meeting_id",
        "chart_selections",
        ["meeting_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_chart_selections_meeting_id", table_name="chart_selections")
    op.drop_table("chart_selections")
