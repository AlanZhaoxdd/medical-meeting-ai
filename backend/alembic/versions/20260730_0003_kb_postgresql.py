"""create PostgreSQL-backed KB v1 schema

Revision ID: 20260730_0003
Revises: 20260730_0002
"""

from alembic import op
from app.db.base import Base
from app.models import kb as kb_models  # noqa: F401

revision = "20260730_0003"
down_revision = "20260730_0002"
branch_labels = None
depends_on = None


KB_TABLES = [
    "node_executions",
    "retrieval_logs",
    "outbox_events",
    "review_events",
    "audit_events",
    "refresh_tokens",
    "ingestion_jobs",
    "extraction_template_versions",
    "extraction_templates",
    "knowledge_items",
    "chunks",
    "document_blocks",
    "documents",
    "knowledge_bases",
    "organization_memberships",
    "organizations",
    "users",
]


def upgrade() -> None:
    bind = op.get_bind()
    tables = [Base.metadata.tables[name] for name in reversed(KB_TABLES)]
    Base.metadata.create_all(bind=bind, tables=tables, checkfirst=True)
    op.create_foreign_key(
        "fk_meetings_knowledge_base_id",
        "meetings",
        "knowledge_bases",
        ["knowledge_base_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_constraint("fk_meetings_knowledge_base_id", "meetings", type_="foreignkey")
    for name in KB_TABLES:
        Base.metadata.tables[name].drop(bind=bind, checkfirst=True)
