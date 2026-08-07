"""add meeting verification and review questions

Revision ID: 20260804_0008
Revises: 20260803_0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260804_0008"
down_revision: str | None = "20260803_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    verification_status = postgresql.ENUM(
        "pending", "in_progress", "confirmed", name="verification_status"
    )
    # 0003's Base.metadata.create_all() already creates these enum types from
    # the current model metadata, so create them idempotently here and prevent
    # op.create_table() from re-emitting CREATE TYPE.
    question_type = postgresql.ENUM(
        "cut_point", "open_ended", name="meeting_question_type", create_type=False
    )
    bind = op.get_bind()
    verification_status.create(bind, checkfirst=True)
    question_type.create(bind, checkfirst=True)

    op.add_column(
        "meetings",
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_meetings_organization_id", "meetings", ["organization_id"])
    op.execute(
        sa.text(
            """
            UPDATE meetings AS m
            SET organization_id = kb.organization_id
            FROM knowledge_bases AS kb
            WHERE m.knowledge_base_id = kb.id
              AND m.organization_id IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE meetings AS m
            SET organization_id = singleton.id
            FROM (
                SELECT id
                FROM organizations
                WHERE (SELECT count(*) FROM organizations) = 1
                LIMIT 1
            ) AS singleton
            WHERE m.organization_id IS NULL
            """
        )
    )

    op.add_column(
        "meetings",
        sa.Column(
            "verification_status",
            verification_status,
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "meetings",
        sa.Column("verification_version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("meetings", sa.Column("verification_confirmed_at", sa.DateTime(timezone=True)))
    op.add_column(
        "meetings",
        sa.Column(
            "verification_confirmed_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
    )
    op.add_column("meetings", sa.Column("analysis_requested_at", sa.DateTime(timezone=True)))

    op.create_table(
        "meeting_questions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "meeting_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("question_type", question_type, nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="manual"),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_meeting_questions_meeting_id", "meeting_questions", ["meeting_id"])
    op.create_index(
        "ix_meeting_questions_meeting_type",
        "meeting_questions",
        ["meeting_id", "question_type"],
    )
    op.create_index(
        "uq_active_meeting_question_content",
        "meeting_questions",
        ["meeting_id", "question_type", sa.text("lower(btrim(content))")],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_active_meeting_question_content", table_name="meeting_questions")
    op.drop_index("ix_meeting_questions_meeting_type", table_name="meeting_questions")
    op.drop_index("ix_meeting_questions_meeting_id", table_name="meeting_questions")
    op.drop_table("meeting_questions")
    op.drop_index("ix_meetings_organization_id", table_name="meetings")
    op.drop_column("meetings", "organization_id")
    op.drop_column("meetings", "analysis_requested_at")
    op.drop_column("meetings", "verification_confirmed_by")
    op.drop_column("meetings", "verification_confirmed_at")
    op.drop_column("meetings", "verification_version")
    op.drop_column("meetings", "verification_status")
    bind = op.get_bind()
    postgresql.ENUM(name="meeting_question_type").drop(bind, checkfirst=True)
    postgresql.ENUM(name="verification_status").drop(bind, checkfirst=True)
