"""End-to-end search latency benchmark (p50/p95/p99 + QPS).

Mirrors the production search path: query embedding -> Milvus hybrid search ->
PostgreSQL content fetch -> rerank. Stage timings are recorded separately so
the slowest component is visible (usually embedding or rerank on CPU).

Usage:
  cd backend
  uv run python scripts/bench_search_latency.py --golden eval_set.json
  uv run python scripts/bench_search_latency.py --queries queries.txt --iterations 100
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.benchmark import run_search_latency_benchmark
from scripts._eval_utils import load_golden_set, write_json


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--golden", help="golden set JSON path (uses its queries)")
    source.add_argument("--queries", help="plain-text file, one query per line")
    parser.add_argument("--iterations", type=int, default=50)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--dense-limit", type=int, default=50)
    parser.add_argument("--sparse-limit", type=int, default=50)
    parser.add_argument("--fusion-limit", type=int, default=15)
    parser.add_argument("--rerank-top", type=int, default=5)
    parser.add_argument("--ef", type=int, default=128)
    parser.add_argument("--no-rerank", action="store_true")
    parser.add_argument("--output", default="search_latency_report.json")
    return parser.parse_args()


def _load_queries(args: argparse.Namespace) -> list[str]:
    if args.golden:
        return [entry["query"] for entry in load_golden_set(args.golden)]
    lines = [
        line.strip()
        for line in Path(args.queries).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return lines


async def main() -> None:
    args = _parse_args()
    queries = _load_queries(args)
    if not queries:
        raise SystemExit("没有可用的 query")
    report = await run_search_latency_benchmark(
        queries,
        iterations=args.iterations,
        warmup=args.warmup,
        dense_limit=args.dense_limit,
        sparse_limit=args.sparse_limit,
        fusion_limit=args.fusion_limit,
        rerank_top=args.rerank_top,
        ef=args.ef,
        rerank=not args.no_rerank,
    )
    write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"报告已写入 {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
