"""Embedding throughput benchmark (texts/sec per batch size).

Loads real chunk texts (from a golden set, a plain-text corpus, or the
database) and measures the model-service /v1/embeddings endpoint for each
batch size. This is the metric that quantifies ingestion speedup (e.g.,
CPU batch 4 -> 8, or CPU -> GPU).

Usage:
  cd backend
  uv run python scripts/bench_embedding.py --golden eval_set.json --batch 4,8,16
  uv run python scripts/bench_embedding.py --corpus corpus.txt --batch 1,4,8
  uv run python scripts/bench_embedding.py --kb <UUID> --batch 8
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models.kb import Chunk
from app.services.benchmark import run_embedding_benchmark
from scripts._eval_utils import load_golden_set, write_json


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--golden", help="golden set JSON path (uses its queries)")
    source.add_argument("--corpus", help="plain-text corpus, one text per line")
    source.add_argument("--kb", type=UUID, help="load published chunks from a KB")
    parser.add_argument("--batch", default="1,4,8,16", help="comma-separated batch sizes")
    parser.add_argument("--iterations", type=int, default=10)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--output", default="embedding_benchmark.json")
    return parser.parse_args()


async def _load_kb_texts(kb_id: UUID) -> list[str]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            rows = await session.execute(
                select(Chunk.content)
                .where(
                    Chunk.knowledge_base_id == kb_id,
                    Chunk.publication_status == "PUBLISHED",
                    func.length(Chunk.content) >= 20,
                )
                .order_by(Chunk.document_id, Chunk.chunk_index)
            )
        return [str(content) for content in rows.scalars().all()]
    finally:
        await engine.dispose()


async def main() -> None:
    args = _parse_args()
    if args.golden:
        texts = [entry["query"] for entry in load_golden_set(args.golden)]
    elif args.corpus:
        texts = [
            line.strip()
            for line in Path(args.corpus).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        texts = await _load_kb_texts(args.kb)
    if len(texts) < max(int(value) for value in args.batch.split(",")):
        raise SystemExit("语料太少，至少需要 max(batch) 条文本")
    batch_sizes = [int(value) for value in args.batch.split(",")]
    report = await run_embedding_benchmark(
        texts,
        batch_sizes=batch_sizes,
        iterations=args.iterations,
        warmup=args.warmup,
    )
    write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"报告已写入 {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
