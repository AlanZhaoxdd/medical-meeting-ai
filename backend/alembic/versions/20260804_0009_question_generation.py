"""add asynchronous question generation tasks and evidences

Revision ID: 20260804_0009
Revises: 20260804_0008
"""
from collections.abc import Sequence
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "20260804_0009"
down_revision: str | None = "20260804_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("meeting_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_type", sa.String(64), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("thread_id", sa.String(255), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="QUEUED"),
        sa.Column("current_stage", sa.String(64), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("message", sa.Text()),
        sa.Column("error_code", sa.String(100)),
        sa.Column("error_message", sa.Text()),
        sa.Column("cutpoint_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("open_question_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("model_name", sa.String(200)),
        sa.Column("prompt_version", sa.String(64)),
        sa.Column("attempt_token", postgresql.UUID(as_uuid=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("meeting_id", "task_type", "source_version", name="uq_ai_task_meeting_type_source"),
    )
    op.create_index("ix_ai_tasks_meeting_created", "ai_tasks", ["meeting_id", "created_at"])
    op.create_index("ix_outbox_events_status_created", "outbox_events", ["status", "created_at"])
    op.add_column("meeting_questions", sa.Column("topic", sa.String(255)))
    op.add_column("meeting_questions", sa.Column("rationale", sa.Text()))
    op.add_column("meeting_questions", sa.Column("origin", sa.String(32), nullable=False, server_default="USER_CREATED"))
    op.add_column("meeting_questions", sa.Column("review_status", sa.String(32), nullable=False, server_default="USER_EDITED"))
    op.add_column("meeting_questions", sa.Column("support_score", sa.Float()))
    op.add_column("meeting_questions", sa.Column("expected_answer_type", sa.String(32)))
    op.add_column("meeting_questions", sa.Column("evidence_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("meeting_questions", sa.Column("generated_task_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("ai_tasks.id", ondelete="SET NULL")))
    op.create_table(
        "question_evidences",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("question_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("meeting_questions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_id", sa.String(100), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE")),
        sa.Column("block_id", sa.String(100)),
        sa.Column("retrieval_query", sa.Text()),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("evidence_summary", sa.Text()),
        sa.Column("relevance_score", sa.Float()),
        sa.Column("vector_score", sa.Float()),
        sa.Column("keyword_score", sa.Float()),
        sa.Column("rerank_score", sa.Float()),
        sa.Column("source_type", sa.String(32), nullable=False, server_default="knowledge_base"),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("question_id", "chunk_id", name="uq_question_evidence_chunk"),
    )


def downgrade() -> None:
    op.drop_table("question_evidences")
    op.drop_column("meeting_questions", "generated_task_id")
    op.drop_column("meeting_questions", "evidence_count")
    op.drop_column("meeting_questions", "support_score")
    op.drop_column("meeting_questions", "expected_answer_type")
    op.drop_column("meeting_questions", "review_status")
    op.drop_column("meeting_questions", "origin")
    op.drop_column("meeting_questions", "rationale")
    op.drop_column("meeting_questions", "topic")
    op.drop_index("ix_ai_tasks_meeting_created", table_name="ai_tasks")
    op.drop_index("ix_outbox_events_status_created", table_name="outbox_events")
    op.drop_table("ai_tasks")
