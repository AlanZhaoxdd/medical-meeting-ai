"""Reusable benchmark runners for retrieval quality, latency and throughput.

These functions are shared by the CLI scripts (backend/scripts) and the
admin-triggered Celery benchmarks, so a number produced in either path is
computed with exactly the same code.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

import anyio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models.kb import Chunk
from app.services.model_client import ModelServiceClient
from app.services.vector_store import VectorStore

ProgressCallback = Callable[[int, str], Awaitable[None]]


def percentiles(
    values: list[float], points: tuple[int, ...] = (50, 95, 99)
) -> dict[str, float]:
    if not values:
        return {f"p{point}": 0.0 for point in points}
    ordered = sorted(values)
    out: dict[str, float] = {}
    for point in points:
        index = min(len(ordered) - 1, round((point / 100) * (len(ordered) - 1)))
        out[f"p{point}"] = round(ordered[index], 3)
    return out


def describe(values: list[float]) -> dict[str, float]:
    if not values:
        return {"mean_ms": 0.0, "min_ms": 0.0, "max_ms": 0.0, "qps": 0.0}
    mean = sum(values) / len(values)
    stats = {
        "mean_ms": round(mean, 3),
        "min_ms": round(min(values), 3),
        "max_ms": round(max(values), 3),
    }
    stats.update(percentiles(values))
    stats["qps"] = round(1000.0 / mean, 3) if mean > 0 else 0.0
    return stats


def _environment() -> dict[str, Any]:
    settings = get_settings()
    return {
        "device": settings.bge_device,
        "embedding_model": settings.embedding_model,
        "embedding_strategy": settings.bge_embedding_strategy,
        "reranker_model": settings.reranker_model,
        "bge_batch_size": settings.bge_batch_size,
    }


def _rank(ordered_ids: list[str], expected: str) -> int | None:
    for position, chunk_id in enumerate(ordered_ids, start=1):
        if chunk_id == expected:
            return position
    return None


def _metrics(ranks: list[int | None], top_ks: list[int]) -> dict[str, Any]:
    total = len(ranks)
    if total == 0:
        return {"n": 0}
    result: dict[str, Any] = {"n": total}
    for k in top_ks:
        hits = sum(1 for rank in ranks if rank is not None and rank <= k)
        result[f"hit@{k}"] = round(hits / total, 4)
    mrr_10 = sum(1.0 / rank for rank in ranks if rank is not None and rank <= 10)
    result["mrr@10"] = round(mrr_10 / total, 4)
    return result


async def _search_field(
    client: Any,
    collection: str,
    *,
    field: str,
    vector: Any,
    filter_expression: str,
    limit: int,
    params: dict[str, Any],
) -> list[str]:
    def run() -> list[str]:
        hits = client.search(
            collection_name=collection,
            data=[vector],
            anns_field=field,
            filter=filter_expression,
            limit=limit,
            search_params=params,
            output_fields=["record_id"],
        )[0]
        return [str(hit["entity"]["record_id"]) for hit in hits]

    return await anyio.to_thread.run_sync(run)


async def _fetch_contents(
    factory: async_sessionmaker[AsyncSession], chunk_ids: list[str]
) -> dict[str, str]:
    if not chunk_ids:
        return {}
    async with factory() as session:
        rows = await session.execute(
            select(Chunk.chunk_id, Chunk.content).where(Chunk.chunk_id.in_(chunk_ids))
        )
    return {str(chunk_id): content for chunk_id, content in rows.all()}


async def run_embedding_benchmark(
    texts: list[str],
    *,
    batch_sizes: list[int],
    iterations: int,
    warmup: int,
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    if not texts:
        raise ValueError("语料为空")
    if len(texts) < max(batch_sizes):
        raise ValueError("语料太少，至少需要 max(batch) 条文本")
    results: dict[str, dict[str, float]] = {}
    async with ModelServiceClient() as client:
        for position, batch_size in enumerate(batch_sizes, start=1):
            batch_texts = texts[:batch_size]
            for _ in range(warmup):
                await client.embeddings(batch_texts)
            timings: list[float] = []
            for _ in range(iterations):
                started = time.perf_counter()
                await client.embeddings(batch_texts)
                timings.append((time.perf_counter() - started) * 1000)
            stats = describe(timings)
            stats["texts_per_second"] = round(
                batch_size / (sum(timings) / len(timings) / 1000), 2
            )
            results[str(batch_size)] = stats
            if on_progress is not None:
                await on_progress(
                    int(position / len(batch_sizes) * 100),
                    f"batch={batch_size}",
                )
    return {
        "environment": _environment(),
        "corpus_size": len(texts),
        "iterations": iterations,
        "batch_ms": results,
    }


async def run_search_latency_benchmark(
    queries: list[str],
    *,
    iterations: int,
    warmup: int,
    dense_limit: int = 50,
    sparse_limit: int = 50,
    fusion_limit: int = 15,
    rerank_top: int = 5,
    ef: int = 128,
    rerank: bool = True,
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    if not queries:
        raise ValueError("query 列表为空")
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    vector_store = VectorStore()
    stages: dict[str, list[float]] = {
        "embed": [],
        "search": [],
        "db_fetch": [],
        "rerank": [],
        "total": [],
    }
    try:
        async with ModelServiceClient() as client:
            total = warmup + iterations
            for round_index in range(total):
                query = queries[round_index % len(queries)]
                started = time.perf_counter()
                embed_started = time.perf_counter()
                embedding = (await client.embeddings([query]))[0]
                stages["embed"].append((time.perf_counter() - embed_started) * 1000)
                search_started = time.perf_counter()
                hybrid = await vector_store.hybrid_search(
                    dense_vector=[float(value) for value in embedding["dense"]],
                    sparse_vector={
                        int(key): float(value)
                        for key, value in embedding["sparse"].items()
                    },
                    filter_expression="",
                    dense_limit=dense_limit,
                    sparse_limit=sparse_limit,
                    fusion_limit=fusion_limit,
                )
                stages["search"].append((time.perf_counter() - search_started) * 1000)
                candidate_ids = [candidate["chunk_id"] for candidate in hybrid]
                fetch_started = time.perf_counter()
                contents = await _fetch_contents(factory, candidate_ids)
                stages["db_fetch"].append((time.perf_counter() - fetch_started) * 1000)
                if not rerank or not contents:
                    stages["rerank"].append(0.0)
                else:
                    rerank_started = time.perf_counter()
                    ordered = [
                        chunk_id for chunk_id in candidate_ids if chunk_id in contents
                    ]
                    await client.rerank(
                        query,
                        [contents[chunk_id] for chunk_id in ordered],
                        min(len(ordered), rerank_top),
                    )
                    stages["rerank"].append(
                        (time.perf_counter() - rerank_started) * 1000
                    )
                stages["total"].append((time.perf_counter() - started) * 1000)
                if on_progress is not None and (round_index + 1) % 10 == 0:
                    await on_progress(
                        int((round_index + 1) / total * 100),
                        f"{round_index + 1}/{total}",
                    )
    finally:
        await engine.dispose()
    measured = {key: values[warmup:] for key, values in stages.items()}
    return {
        "environment": _environment(),
        "queries": len(queries),
        "iterations": iterations,
        "params": {
            "dense_limit": dense_limit,
            "sparse_limit": sparse_limit,
            "fusion_limit": fusion_limit,
            "ef": ef,
            "rerank_top": rerank_top,
        },
        "stages_ms": {key: describe(values) for key, values in measured.items()},
    }


async def run_retrieval_quality_eval(
    entries: list[dict[str, Any]],
    *,
    top_ks: list[int],
    dense_limit: int = 50,
    sparse_limit: int = 50,
    fusion_limit: int = 15,
    rerank_top: int = 5,
    ef: int = 128,
    rerank: bool = True,
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    if not entries:
        raise ValueError("黄金集为空")
    settings = get_settings()
    max_k = max(top_ks)
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    vector_store = VectorStore()
    collection = vector_store.collection
    dense_params = {"metric_type": "COSINE", "params": {"ef": ef}}
    sparse_params = {"metric_type": "IP", "params": {}}
    ranks: dict[str, list[int | None]] = {
        "dense_only": [],
        "sparse_only": [],
        "hybrid": [],
        "hybrid_rerank": [],
    }
    try:
        async with ModelServiceClient() as client:
            total = len(entries)
            for index, entry in enumerate(entries, start=1):
                expected = entry["expected_chunk_id"]
                kb_id = entry["kb_id"]
                if not kb_id:
                    raise ValueError(f"条目缺少 kb_id: {expected}")
                filter_expression = f'knowledge_base_id == "{kb_id}"'
                embedding = (await client.embeddings([entry["query"]]))[0]
                dense = [float(value) for value in embedding["dense"]]
                sparse = {
                    int(key): float(value)
                    for key, value in embedding["sparse"].items()
                }
                dense_ids = await _search_field(
                    vector_store.client,
                    collection,
                    field="dense_vector",
                    vector=dense,
                    filter_expression=filter_expression,
                    limit=max_k,
                    params=dense_params,
                )
                sparse_ids = await _search_field(
                    vector_store.client,
                    collection,
                    field="sparse_vector",
                    vector=sparse,
                    filter_expression=filter_expression,
                    limit=max_k,
                    params=sparse_params,
                )
                hybrid = await vector_store.hybrid_search(
                    dense_vector=dense,
                    sparse_vector=sparse,
                    filter_expression=filter_expression,
                    dense_limit=dense_limit,
                    sparse_limit=sparse_limit,
                    fusion_limit=fusion_limit,
                )
                hybrid_ids = [candidate["chunk_id"] for candidate in hybrid]
                ranks["dense_only"].append(_rank(dense_ids, expected))
                ranks["sparse_only"].append(_rank(sparse_ids, expected))
                ranks["hybrid"].append(_rank(hybrid_ids, expected))
                if not rerank:
                    ranks["hybrid_rerank"].append(None)
                else:
                    contents = await _fetch_contents(factory, hybrid_ids)
                    ordered = [
                        chunk_id for chunk_id in hybrid_ids if chunk_id in contents
                    ]
                    if not ordered:
                        ranks["hybrid_rerank"].append(None)
                    else:
                        reranked = await client.rerank(
                            entry["query"],
                            [contents[chunk_id] for chunk_id in ordered],
                            min(len(ordered), rerank_top),
                        )
                        reranked_ids = [
                            ordered[int(item["index"])] for item in reranked
                        ]
                        ranks["hybrid_rerank"].append(_rank(reranked_ids, expected))
                if on_progress is not None and index % 10 == 0:
                    await on_progress(int(index / total * 100), f"{index}/{total}")
    finally:
        await engine.dispose()
    return {
        "environment": _environment(),
        "queries": total,
        "top_k": top_ks,
        "params": {
            "dense_limit": dense_limit,
            "sparse_limit": sparse_limit,
            "fusion_limit": fusion_limit,
            "ef": ef,
            "rerank_top": rerank_top,
        },
        "results": {
            variant: _metrics(ranks[variant], top_ks)
            for variant in ("dense_only", "sparse_only", "hybrid", "hybrid_rerank")
        },
    }
