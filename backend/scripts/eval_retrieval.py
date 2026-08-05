"""Offline retrieval-quality evaluation: Recall@k and MRR with baselines.

Loads a golden set (see build_eval_set.py), embeds each query, then runs four
retrieval variants for the same query:
- dense_only: BGE-M3 dense vector search
- sparse_only: BGE-M3 lexical (sparse) search
- hybrid: dense + sparse with reciprocal-rank fusion (production path)
- hybrid_rerank: hybrid candidates re-ranked by the reranker (production path)

Metrics are computed against the golden chunk expected for each query.

Usage:
  cd backend
  uv run python scripts/eval_retrieval.py --golden eval_set.json --output report.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.benchmark import run_retrieval_quality_eval
from scripts._eval_utils import load_golden_set, write_json


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", required=True, help="golden set JSON path")
    parser.add_argument("--top-k", default="1,3,5,10", help="comma-separated k values")
    parser.add_argument("--dense-limit", type=int, default=50)
    parser.add_argument("--sparse-limit", type=int, default=50)
    parser.add_argument("--fusion-limit", type=int, default=15)
    parser.add_argument("--rerank-top", type=int, default=5)
    parser.add_argument("--ef", type=int, default=128, help="HNSW ef for dense search")
    parser.add_argument("--no-rerank", action="store_true")
    parser.add_argument("--max-queries", type=int, default=0)
    parser.add_argument("--output", default="eval_report.json")
    return parser.parse_args()


async def main() -> None:
    args = _parse_args()
    entries = load_golden_set(args.golden)
    if args.max_queries:
        entries = entries[: args.max_queries]
    if not entries:
        raise SystemExit("golden set 没有有效条目（需要 query + expected_chunk_id）")
    top_ks = [int(value) for value in args.top_k.split(",")]
    report = await run_retrieval_quality_eval(
        entries,
        top_ks=top_ks,
        dense_limit=args.dense_limit,
        sparse_limit=args.sparse_limit,
        fusion_limit=args.fusion_limit,
        rerank_top=args.rerank_top,
        ef=args.ef,
        rerank=not args.no_rerank,
    )
    report["golden"] = args.golden
    write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"报告已写入 {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
