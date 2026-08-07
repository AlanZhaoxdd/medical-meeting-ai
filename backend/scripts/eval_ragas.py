"""Offline Ragas evaluation for the meeting Q&A RAG pipeline.

Runs the production meeting Q&A flow (retrieval + rerank + generation) over a
QA golden set and scores the results with ragas metrics (faithfulness, answer
relevancy, context precision/recall, factual correctness, semantic similarity).

Usage:
  cd backend
  uv run python scripts/eval_ragas.py \
      --meeting 7a6ed448-35a1-448f-bd6b-c4ad74e21b03 \
      --dataset eval_data/eval-datasets-1786019104793.json \
      --max-items 20 --output report_ragas.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.ragas_eval import load_qa_entries, run_ragas_eval  # noqa: E402
from scripts._eval_utils import write_json  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--meeting", required=True, help="meeting UUID to evaluate against")
    parser.add_argument(
        "--dataset",
        default="eval-datasets-1786019104793.json",
        help="QA golden set file name inside eval_data/ (or absolute path)",
    )
    parser.add_argument("--scope", default="MEETING_AND_KB", choices=["CURRENT_MEETING", "MEETING_AND_KB"])
    parser.add_argument(
        "--metrics",
        default="faithfulness,answer_relevancy,context_precision,context_recall,semantic_similarity",
        help="comma-separated ragas metric names",
    )
    parser.add_argument("--max-items", type=int, default=0, help="limit sample count (0 = all)")
    parser.add_argument("--seed", type=int, default=42, help="sampling seed")
    parser.add_argument("--output", default="eval_report_ragas.json")
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    entries = load_qa_entries(dataset_file=args.dataset)
    if args.max_items:
        entries = entries[: args.max_items]
    if not entries:
        raise SystemExit("golden set 没有有效条目（需要 question + correctAnswer）")
    metrics = [name.strip() for name in args.metrics.split(",") if name.strip()]
    report = await run_ragas_eval(
        entries=entries,
        meeting_id=args.meeting,
        scope=args.scope,
        metrics=metrics,
        max_items=args.max_items,
        seed=args.seed,
    )
    report["golden"] = args.dataset
    write_json(args.output, report)
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))
    print(
        f"报告已写入 {args.output}（{report['queries']} 条评分，"
        f"{len(report['skipped'])} 条跳过）"
    )


if __name__ == "__main__":
    asyncio.run(main())
