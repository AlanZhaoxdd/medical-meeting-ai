"""Shared helpers for retrieval evaluation and benchmark scripts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_golden_set(path: str) -> list[dict[str, Any]]:
    """Load a golden set JSON file (see build_eval_set.py for the schema)."""

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = data.get("entries") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ValueError("golden set must contain an 'entries' list")
    default_kb = data.get("kb_id") if isinstance(data, dict) else None
    result: list[dict[str, Any]] = []
    for entry in entries:
        if not entry.get("query") or not entry.get("expected_chunk_id"):
            continue
        normalized = dict(entry)
        normalized.setdefault("kb_id", default_kb)
        result.append(normalized)
    return result


def write_json(path: str, payload: dict[str, Any]) -> None:
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
