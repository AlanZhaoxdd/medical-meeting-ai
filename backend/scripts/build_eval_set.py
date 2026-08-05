"""Build a retrieval golden set from vectorized knowledge-base chunks.

Two modes:
- default: sample chunks evenly across documents, then use an OpenAI-compatible
  LLM (LLM_BASE_URL / LLM_API_KEY / LLM_MODEL) to generate one or two grounded
  questions per chunk. Each question's expected answer chunk is the source
  chunk, which is what Recall@k / MRR evaluate against.
- --dry-run: export the sampled chunks without questions so they can be
  reviewed and questions written by a human first.

Usage:
  cd backend
  uv run python scripts/build_eval_set.py --kb <UUID> --count 60 --output eval_set.json
  uv run python scripts/build_eval_set.py --kb <UUID> --count 30 --dry-run --output sample.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models.kb import Chunk, Document
from scripts._eval_utils import write_json

_SYSTEM_PROMPT = "你是检索评估数据标注助手。你只返回 JSON，不返回其他文字。"
_USER_PROMPT = """根据下面给出的知识库片段，提出 1 到 2 个"只有这段内容才能回答"的自然问题。
要求：
- 问题要像真实用户在检索时提出的（可以是问数据、结论、人名、药名等）
- 不要出现"根据这段文本""上述内容"等标注痕迹
- 返回 JSON 数组，例如 ["问题一", "问题二"]

片段：
{content}
"""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kb", type=UUID, required=True, help="knowledge base id")
    parser.add_argument("--count", type=int, default=60, help="number of chunks to sample")
    parser.add_argument("--seed", type=int, default=42, help="sampling seed")
    parser.add_argument("--min-chars", type=int, default=80, help="minimum chunk length")
    parser.add_argument("--max-per-document", type=int, default=6)
    parser.add_argument("--status", default="PUBLISHED", help="chunk publication status")
    parser.add_argument("--output", default="eval_set.json")
    parser.add_argument("--dry-run", action="store_true", help="export chunks without questions")
    return parser.parse_args()


def _sample_evenly(
    by_document: dict[str, list[dict[str, Any]]],
    *,
    count: int,
    max_per_document: int,
) -> list[dict[str, Any]]:
    documents = list(by_document.values())
    if not documents:
        return []
    pools: list[list[dict[str, Any]]] = []
    for chunks in documents:
        budget = min(max_per_document, len(chunks))
        pools.append(
            [chunks[(index * len(chunks)) // budget] for index in range(budget)]
        )
    sampled: list[dict[str, Any]] = []
    index = 0
    while len(sampled) < count:
        candidates = [pool for pool in pools if pool]
        if not candidates:
            break
        pool = candidates[index % len(candidates)]
        sampled.append(pool.pop(0))
        index += 1
    return sampled[:count]


async def _load_chunks(
    factory: async_sessionmaker[AsyncSession],
    kb_id: UUID,
    *,
    status: str,
    min_chars: int,
) -> dict[str, list[dict[str, Any]]]:
    async with factory() as session:
        rows = (
            await session.execute(
                select(
                    Chunk.chunk_id,
                    Chunk.document_id,
                    Chunk.content,
                    Document.safe_filename,
                )
                .join(Document, Document.id == Chunk.document_id)
                .where(
                    Chunk.knowledge_base_id == kb_id,
                    Chunk.publication_status == status,
                    func.length(Chunk.content) >= min_chars,
                )
                .order_by(Chunk.document_id, Chunk.chunk_index)
            )
        ).all()
    by_document: dict[str, list[dict[str, Any]]] = {}
    for chunk_id, document_id, content, filename in rows:
        by_document.setdefault(str(document_id), []).append(
            {
                "chunk_id": str(chunk_id),
                "document_id": str(document_id),
                "filename": filename,
                "content": content,
            }
        )
    return by_document


def _parse_questions(text: str) -> list[str]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned).strip()
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, list):
        return [str(item).strip() for item in payload if str(item).strip()]
    questions = [
        line.strip(" -·•") for line in cleaned.splitlines()
        if any(mark in line for mark in ("？", "?"))
    ]
    return [question for question in questions if question]


def _ask_questions(content: str) -> list[str]:
    settings = get_settings()
    if not settings.llm_base_url or not settings.resolved_llm_api_key:
        raise RuntimeError("LLM_BASE_URL / LLM_API_KEY 未配置，请使用 --dry-run 人工标注")
    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _USER_PROMPT.format(content=content[:4000])},
        ],
        "temperature": 0.7,
        "max_tokens": 256,
    }
    with httpx.Client(timeout=90) as client:
        response = client.post(
            f"{settings.llm_base_url.rstrip('/')}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {settings.resolved_llm_api_key}"},
        )
        response.raise_for_status()
        text = response.json()["choices"][0]["message"]["content"]
    return _parse_questions(text)


async def main() -> None:
    args = _parse_args()
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        by_document = await _load_chunks(
            factory, args.kb, status=args.status, min_chars=args.min_chars
        )
        sampled = _sample_evenly(
            by_document, count=args.count, max_per_document=args.max_per_document
        )
        print(f"已采样 {len(sampled)} 个 chunk（{len(by_document)} 个文档）")
        if not sampled:
            raise SystemExit("没有符合条件的 chunk，请检查 kb/status/min-chars")
        if args.dry_run:
            write_json(
                args.output,
                {
                    "version": 1,
                    "kb_id": str(args.kb),
                    "mode": "dry_run",
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "entries": [
                        {
                            "query": "",
                            "expected_chunk_id": item["chunk_id"],
                            "expected_document_id": item["document_id"],
                            "filename": item["filename"],
                            "content": item["content"],
                        }
                        for item in sampled
                    ],
                },
            )
            print(f"人工标注文件已写入 {args.output}（填好 query 后保留 expected_* 字段）")
            return
        entries: list[dict[str, Any]] = []
        failed = 0
        for index, item in enumerate(sampled, start=1):
            questions = _ask_questions(item["content"])
            if not questions:
                failed += 1
                print(f"[{index}/{len(sampled)}] 未能生成问题，已跳过 {item['chunk_id']}")
                continue
            for query in questions[:2]:
                entries.append(
                    {
                        "query": query,
                        "expected_chunk_id": item["chunk_id"],
                        "expected_document_id": item["document_id"],
                        "kb_id": str(args.kb),
                    }
                )
        write_json(
            args.output,
            {
                "version": 1,
                "kb_id": str(args.kb),
                "mode": "llm_generated",
                "model": settings.llm_model,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "entries": entries,
            },
        )
        print(f"黄金集已写入 {args.output}，共 {len(entries)} 条 query，{failed} 个 chunk 生成失败")
        print("建议人工抽检 10% 的 query，删掉跨 chunk 可回答的问题")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
