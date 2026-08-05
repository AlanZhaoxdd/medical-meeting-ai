"""expand node execution input version

Revision ID: 20260731_0004
Revises: 20260730_0003
"""

import sqlalchemy as sa

from alembic import op

revision = "20260731_0004"
down_revision = "20260730_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "node_executions",
        "input_version",
        existing_type=sa.String(length=100),
        type_=sa.String(length=255),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "node_executions",
        "input_version",
        existing_type=sa.String(length=255),
        type_=sa.String(length=100),
        existing_nullable=False,
    )
