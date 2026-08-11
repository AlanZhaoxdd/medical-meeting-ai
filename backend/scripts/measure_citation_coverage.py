"""Measure citation coverage of a meeting-minutes analysis result.

Input is an analysis result JSON with the same shape as the backend's
``AnalysisRunRead`` (either a bare module list or ``{"modules": [...]}``).
Each module has ``content`` / ``items`` / ``citations``; a citation is valid
only when it is an integer index that points into the ``sources`` registry
(``--sources`` is optional; without it, any non-empty integer list counts).

Two scopes are reported:

- module level: content modules with >= 1 valid citation / all content modules;
- paragraph level: content paragraphs containing at least one ``[n]`` anchor.

This is the measurable counterpart of the product guarantee that content
without citations is rejected by ``validate_and_persist`` before it can be
confirmed as meeting minutes.

Usage (from ``backend/``):

    uv run python scripts/measure_citation_coverage.py \
        --modules analysis_run.json [--sources sources.json] [--output report.json]
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ANCHOR_RE = re.compile(r"\[\s*\d+\s*\]")


def _load_list(path: str) -> list[dict[str, Any]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        if "modules" in raw:
            return list(raw["modules"])
        raise ValueError("JSON 对象需要包含 modules 数组，或直接传入模块数组")
    raise ValueError("输入必须是模块数组或 {modules: [...]} 对象")


def _valid_citations(module: dict[str, Any], source_count: int) -> list[int]:
    citations = [int(item) for item in module.get("citations", [])]
    return sorted({index for index in citations if 1 <= index <= source_count})


def _paragraphs(content: str) -> list[str]:
    return [block.strip() for block in re.split(r"\n\s*\n", content) if block.strip()]


def measure(modules: list[dict[str, Any]], source_count: int) -> dict[str, Any]:
    content_modules: list[dict[str, Any]] = []
    item_modules: list[dict[str, Any]] = []
    cited_content = 0
    cited_items = 0
    paragraphs_total = 0
    paragraphs_cited = 0
    detail: list[dict[str, Any]] = []

    for module in modules:
        content = module.get("content")
        items = module.get("items") or []
        has_content = bool(content and str(content).strip())
        has_items = bool(items)
        citations = _valid_citations(module, source_count)
        entry = {
            "id": module.get("id") or module.get("title") or "module",
            "title": module.get("title", ""),
            "has_content": has_content,
            "has_items": has_items,
            "citations": citations,
        }
        if has_content:
            content_modules.append(module)
            cited_content += 1 if citations else 0
            paragraphs = _paragraphs(str(content))
            paragraphs_total += len(paragraphs)
            paragraphs_cited += sum(
                1 for paragraph in paragraphs if ANCHOR_RE.search(paragraph)
            )
            entry["paragraphs"] = len(paragraphs)
            entry["paragraphs_cited"] = sum(
                1 for paragraph in paragraphs if ANCHOR_RE.search(paragraph)
            )
        if has_items:
            item_modules.append(module)
            cited_items += 1 if citations else 0
        detail.append(entry)

    def pct(cited: int, total: int) -> float:
        return round(100.0 * cited / total, 2) if total else 0.0

    return {
        "source_count": source_count,
        "n_modules": len(modules),
        "content_modules": len(content_modules),
        "content_cited": cited_content,
        "content_coverage_pct": pct(cited_content, len(content_modules)),
        "item_modules": len(item_modules),
        "items_cited": cited_items,
        "item_coverage_pct": pct(cited_items, len(item_modules)),
        "paragraphs_total": paragraphs_total,
        "paragraphs_cited": paragraphs_cited,
        "paragraph_coverage_pct": pct(paragraphs_cited, paragraphs_total),
        "detail": detail,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--modules", required=True, help="analysis modules JSON")
    parser.add_argument("--sources", default=None, help="optional source registry JSON")
    parser.add_argument("--output", default=None, help="optional JSON report path")
    args = parser.parse_args()

    modules = _load_list(args.modules)
    source_count = 0
    if args.sources:
        sources = json.loads(Path(args.sources).read_text(encoding="utf-8-sig"))
        source_count = len(sources if isinstance(sources, list) else sources.get("sources", []))

    report = measure(modules, source_count)
    if args.output:
        Path(args.output).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(
        f"modules={report['n_modules']} "
        f"content={report['content_cited']}/{report['content_modules']} "
        f"({report['content_coverage_pct']}%) "
        f"paragraph={report['paragraphs_cited']}/{report['paragraphs_total']} "
        f"({report['paragraph_coverage_pct']}%)"
    )
    if args.output:
        print(f"report written to {Path(args.output).resolve()}")


if __name__ == "__main__":
    main()
