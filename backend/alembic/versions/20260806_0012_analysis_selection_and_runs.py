"""add analysis candidate selection and analysis result runs

Revision ID: 20260806_0012
Revises: 20260805_0011
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260806_0012"
down_revision: str | None = "20260805_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "meeting_questions",
        sa.Column("candidate_rank", sa.Integer(), nullable=True),
    )
    op.add_column(
        "meeting_questions",
        sa.Column(
            "analysis_selected",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.create_table(
        "meeting_analysis_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ai_tasks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="SUCCEEDED"),
        sa.Column("modules", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("sources", postgresql.JSONB(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column(
            "insufficient_notes",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column("error_message", sa.Text()),
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
            "meeting_id", "task_id", name="uq_analysis_run_meeting_task"
        ),
    )
    op.create_index(
        "ix_meeting_analysis_runs_meeting_created",
        "meeting_analysis_runs",
        ["meeting_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_meeting_analysis_runs_meeting_created",
        table_name="meeting_analysis_runs",
    )
    op.drop_table("meeting_analysis_runs")
    op.drop_column("meeting_questions", "analysis_selected")
    op.drop_column("meeting_questions", "candidate_rank")
