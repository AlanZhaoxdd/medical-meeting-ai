"""PPT / chart rendering latency benchmark (p50/p95/p99).

Measures the deterministic rendering stages of the meeting-export pipeline
that can run without PostgreSQL / Milvus / LLM:

- bar chart PNG  (960x640, 8 categories)
- pie chart PNG  (960x640, 5 categories)
- full PPTX bytes with the charts embedded (8-slide deck)

The timings reflect the same code paths used by ``export_tasks``:
``render_chart_png`` and ``render_ppt_bytes``. LLM outline generation,
retrieval and persistence are intentionally out of scope here; they are
measured separately with ``bench_search_latency.py`` / ``eval_ragas.py``.

Usage (from ``backend/`` so the ``app`` package is importable):

    uv run python scripts/bench_chart_ppt.py --iterations 30 --warmup 3

Output: JSON report (default ``report_chart_ppt.json``) with p50/p95/p99,
mean/min/max and the machine / library environment, so every number can be
traced back to the run that produced it.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from app.schemas.export import PptBulletOut, PptDeckSpec, PptSlideOut
from app.services.export_charts import render_chart_png
from app.services.export_ppt import render_ppt_bytes


def _percentiles(values: list[float], points: tuple[int, ...] = (50, 95, 99)) -> dict[str, float]:
    ordered = sorted(values)
    out: dict[str, float] = {}
    for point in points:
        index = min(len(ordered) - 1, round((point / 100) * (len(ordered) - 1)))
        out[f"p{point}"] = round(ordered[index], 3)
    return out


def _describe(values: list[float]) -> dict[str, float]:
    stats = {
        "mean_ms": round(statistics.mean(values), 3),
        "min_ms": round(min(values), 3),
        "max_ms": round(max(values), 3),
    }
    stats.update(_percentiles(values))
    stats["ops_per_sec"] = round(1000.0 / stats["mean_ms"], 3) if stats["mean_ms"] > 0 else 0.0
    return stats


def _bar_spec() -> dict[str, object]:
    labels = [
        "支持足剂量 2.4mg",
        "倾向 1.0mg 起始",
        "按 BMI 分层",
        "联合生活方式干预",
        "关注中国人群证据",
        "基因检测辅助选药",
        "需半年以上疗程",
        "暂不表态",
    ]
    values = [6, 4, 3, 3, 2, 2, 2, 1]
    return {
        "type": "bar",
        "title": "参会者对司美格鲁肽剂量调整的立场分布",
        "subtitle": "基于切点问题统计，按独立参会者去重",
        "categories": [
            {"key": f"k{index + 1}", "label": label, "value": value}
            for index, (label, value) in enumerate(zip(labels, values, strict=True))
        ],
        "denominator": {"value": 20, "label": "有效参会者"},
    }


def _pie_spec() -> dict[str, object]:
    labels = ["强烈支持", "支持但有条件", "持保留意见", "不表态", "反对"]
    values = [8, 5, 3, 2, 1]
    return {
        "type": "pie",
        "title": "会议共识方向分布",
        "subtitle": "按独立参会者立场去重统计",
        "categories": [
            {"key": f"p{index + 1}", "label": label, "value": value}
            for index, (label, value) in enumerate(zip(labels, values, strict=True))
        ],
        "denominator": {"value": 19, "label": "有效参会者"},
    }


def _deck_spec() -> PptDeckSpec:
    def bullets(items: list[str]) -> list[PptBulletOut]:
        return [PptBulletOut(text=text, sourceIds=["1"]) for text in items]

    return PptDeckSpec(
        title="医药会议汇报",
        subtitle="2026-08-01 · 市场部",
        theme="formal",
        slides=[
            PptSlideOut(pageNumber=1, type="cover", title="封面", bullets=[]),
            PptSlideOut(
                pageNumber=2,
                type="summary",
                title="会议核心摘要",
                bullets=bullets(
                    [
                        "会议围绕司美格鲁肽剂量策略达成初步共识",
                        "多数专家支持按个体化原则选择剂量",
                        "下一阶段需补充中国人群真实世界证据",
                    ]
                ),
            ),
            PptSlideOut(
                pageNumber=3,
                type="topics",
                title="主要议题",
                bullets=bullets(
                    [
                        "2.4mg 剂量在中国人群中的安全性证据",
                        "减重治疗的长期用药时长管理",
                        "生活方式干预与药物联合路径",
                    ]
                ),
            ),
            PptSlideOut(
                pageNumber=4,
                type="viewpoints",
                title="参会者观点",
                bullets=bullets(
                    [
                        "周教授：现有研究以国外数据为主，需谨慎外推",
                        "施教授：建议至少半年以上药物治疗周期",
                        "王主任：强调生活方式干预的基础地位",
                        "李教授：基因检测未来可用于精准选药",
                    ]
                ),
            ),
            PptSlideOut(
                pageNumber=5,
                type="cutoff_questions",
                title="切点问题分析",
                bullets=bullets(
                    [
                        "是否支持 2.4mg 作为首选剂量：多数支持但有条件",
                        "用药时长是否应固定：多数认为应个体化调整",
                    ]
                ),
            ),
            PptSlideOut(
                pageNumber=6,
                type="charts",
                title="数据图表",
                bullets=bullets(["立场分布（条形图）", "共识方向（饼图）"]),
                chartIds=["bar", "pie"],
            ),
            PptSlideOut(
                pageNumber=7,
                type="consensus",
                title="共识与待确认",
                bullets=bullets(
                    [
                        "共识：按个体化原则选择剂量",
                        "共识：用药时长依据治疗目标灵活调整",
                        "待确认：中国人群高剂量安全性数据",
                    ]
                ),
            ),
            PptSlideOut(
                pageNumber=8,
                type="actions",
                title="行动项",
                bullets=bullets(
                    [
                        "市场部整理中国人群证据综述（责任人：王医生）",
                        "医学部输出剂量策略建议稿（责任人：张医生）",
                        "下次会议确认基因检测落地路径",
                    ]
                ),
            ),
        ],
    )


def _bundle() -> SimpleNamespace:
    return SimpleNamespace(
        meeting=SimpleNamespace(
            id=uuid4(),
            title="医药顾问会",
            starts_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            organizer="市场部",
            meeting_info={"advisor_names": "张三, 李四", "internal_attendees": "王五"},
        ),
        run=SimpleNamespace(source_version=3, modules=[], sources=[]),
        questions=[],
        transcript_blocks=[],
        sources=[
            {"index": 1, "type": "transcript", "title": "转写片段", "snippet": "证据摘要"},
        ],
    )


def _environment() -> dict[str, str]:
    try:
        from PIL import Image
        from pptx import __version__ as pptx_version

        pil_version = Image.__version__ if hasattr(Image, "__version__") else ""
    except Exception:
        pil_version = ""
        pptx_version = ""
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or "unknown",
        "cpu_count": str(os.cpu_count() or "unknown"),
        "pil": pil_version,
        "python_pptx": pptx_version,
    }


def _run_stage(name: str, fn: object, iterations: int, warmup: int) -> dict[str, float]:
    for _ in range(warmup):
        fn()  # type: ignore[operator]
    timings: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        fn()  # type: ignore[operator]
        timings.append((time.perf_counter() - started) * 1000)
    return _describe(timings)


def main() -> None:
    parser = argparse.ArgumentParser(description="PPT / chart rendering latency benchmark")
    parser.add_argument("--iterations", type=int, default=30)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--output", default="report_chart_ppt.json")
    args = parser.parse_args()

    if args.iterations < 1 or args.warmup < 0:
        parser.error("iterations must be >= 1 and warmup >= 0")

    bar_spec = _bar_spec()
    pie_spec = _pie_spec()
    deck_spec = _deck_spec()
    bundle = _bundle()

    bar_png = render_chart_png(bar_spec)
    pie_png = render_chart_png(pie_spec)
    chart_images = {"bar": bar_png, "pie": pie_png}

    def render_bar() -> bytes:
        return render_chart_png(bar_spec)

    def render_pie() -> bytes:
        return render_chart_png(pie_spec)

    def render_ppt() -> bytes:
        return render_ppt_bytes(
            bundle,
            deck_spec,
            include_charts=True,
            include_references=True,
            anonymous_attendees=False,
            chart_images=chart_images,
        )

    def render_full_ppt_path() -> bytes:
        images = {
            "bar": render_chart_png(bar_spec),
            "pie": render_chart_png(pie_spec),
        }
        return render_ppt_bytes(
            bundle,
            deck_spec,
            include_charts=True,
            include_references=True,
            anonymous_attendees=False,
            chart_images=images,
        )

    report = {
        "benchmark": "chart_ppt_render",
        "environment": _environment(),
        "iterations": args.iterations,
        "warmup": args.warmup,
        "fixture": {
            "bar_categories": len(bar_spec["categories"]),
            "pie_categories": len(pie_spec["categories"]),
            "deck_slides": len(deck_spec.slides),
            "bar_png_bytes": len(bar_png),
            "pie_png_bytes": len(pie_png),
            "pptx_bytes": len(render_ppt()),
        },
        "stages_ms": {
            "chart_bar_png": _run_stage("bar", render_bar, args.iterations, args.warmup),
            "chart_pie_png": _run_stage("pie", render_pie, args.iterations, args.warmup),
            "pptx_render": _run_stage("ppt", render_ppt, args.iterations, args.warmup),
            "ppt_full_path": _run_stage(
                "full", render_full_ppt_path, args.iterations, args.warmup
            ),
        },
    }

    output = Path(args.output)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nreport written to {output.resolve()}")


if __name__ == "__main__":
    main()
