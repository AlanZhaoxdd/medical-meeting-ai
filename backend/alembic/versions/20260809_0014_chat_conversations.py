"""add chat conversations and messages for meeting Q&A

Revision ID: 20260809_0014
Revises: 20260806_0013
"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260809_0014"
down_revision: str | None = "20260806_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_conversations",
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
            nullable=False,
        ),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
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
    )
    op.create_index(
        "ix_chat_conversations_meeting_created",
        "chat_conversations",
        ["meeting_id", "created_at"],
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("chat_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("rewritten_question", sa.Text(), nullable=True),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column(
            "scope",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'MEETING_AND_KB'"),
        ),
        sa.Column("model", sa.String(255), nullable=True),
        sa.Column("prompt_version", sa.String(64), nullable=True),
        sa.Column(
            "sources",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
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
    )
    op.create_index(
        "ix_chat_messages_conversation_created",
        "chat_messages",
        ["conversation_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_chat_messages_conversation_created", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index("ix_chat_conversations_meeting_created", table_name="chat_conversations")
    op.drop_table("chat_conversations")
