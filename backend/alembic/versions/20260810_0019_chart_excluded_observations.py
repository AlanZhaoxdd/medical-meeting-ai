"""store excluded chart observations with reasons

Revision ID: 20260810_0019
Revises: 20260810_0018
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260810_0019"
down_revision: str | None = "20260810_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "chart_extraction_snapshots",
        sa.Column(
            "excluded_observations",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("chart_extraction_snapshots", "excluded_observations")
