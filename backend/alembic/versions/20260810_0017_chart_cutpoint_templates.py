"""add versioned medical chart cut-point templates and extraction snapshots"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260810_0017"
down_revision: str | None = "20260810_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chart_cutpoint_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("template_key", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("latest_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("organization_id", "template_key", name="uq_chart_cutpoint_template_org_key"),
    )
    op.create_index("ix_chart_cutpoint_templates_org", "chart_cutpoint_templates", ["organization_id"])
    op.create_table(
        "chart_cutpoint_template_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("chart_cutpoint_templates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("items", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("template_id", "version", name="uq_chart_cutpoint_template_version"),
    )
    op.create_index("ix_chart_cutpoint_template_versions_template", "chart_cutpoint_template_versions", ["template_id"])
    op.create_table(
        "chart_extraction_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("meeting_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True),
        sa.Column("analysis_version", sa.Integer(), nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("chart_cutpoint_templates.id", ondelete="CASCADE"), nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False),
        sa.Column("observations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("covered_keys", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("meeting_id", "analysis_version", "template_id", "template_version", name="uq_chart_snapshot_meeting_template_version"),
    )
    op.create_index("ix_chart_extraction_snapshots_meeting", "chart_extraction_snapshots", ["meeting_id"])


def downgrade() -> None:
    op.drop_index("ix_chart_extraction_snapshots_meeting", table_name="chart_extraction_snapshots")
    op.drop_table("chart_extraction_snapshots")
    op.drop_index("ix_chart_cutpoint_template_versions_template", table_name="chart_cutpoint_template_versions")
    op.drop_table("chart_cutpoint_template_versions")
    op.drop_index("ix_chart_cutpoint_templates_org", table_name="chart_cutpoint_templates")
    op.drop_table("chart_cutpoint_templates")
