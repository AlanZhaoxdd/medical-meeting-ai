"""add indicator_options to seeded cut-point template items

Revision ID: 20260810_0018
Revises: 20260810_0017
"""
from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.services.chart_cutpoints import DEFAULT_CUTPOINTS

revision: str = "20260810_0018"
down_revision: str | None = "20260810_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    options_by_key = {
        str(item["key"]): item.get("indicator_options") or []
        for item in DEFAULT_CUTPOINTS
    }
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            """
            SELECT ver.id, ver.items
            FROM chart_cutpoint_template_versions AS ver
            JOIN chart_cutpoint_templates AS tpl ON tpl.id = ver.template_id
            WHERE tpl.template_key = 'medical-default-v1'
            """
        )
    ).fetchall()
    for row in rows:
        items = list(row.items or [])
        changed = False
        for item in items:
            key = str(item.get("key") or "")
            options = options_by_key.get(key)
            if options and not item.get("indicator_options"):
                item["indicator_options"] = options
                changed = True
        if changed:
            conn.execute(
                sa.text(
                    "UPDATE chart_cutpoint_template_versions SET items = :items WHERE id = :id"
                ),
                {"items": json.dumps(items, ensure_ascii=False), "id": row.id},
            )


def downgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            """
            SELECT ver.id, ver.items
            FROM chart_cutpoint_template_versions AS ver
            JOIN chart_cutpoint_templates AS tpl ON tpl.id = ver.template_id
            WHERE tpl.template_key = 'medical-default-v1'
            """
        )
    ).fetchall()
    for row in rows:
        items = list(row.items or [])
        changed = False
        for item in items:
            if "indicator_options" in item:
                item.pop("indicator_options", None)
                changed = True
        if changed:
            conn.execute(
                sa.text(
                    "UPDATE chart_cutpoint_template_versions SET items = :items WHERE id = :id"
                ),
                {"items": json.dumps(items, ensure_ascii=False), "id": row.id},
            )
