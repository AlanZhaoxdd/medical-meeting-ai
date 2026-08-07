"""Ragas-based end-to-end RAG evaluation for the meeting Q&A pipeline.

The evaluator runs the *production* meeting Q&A flow (transcript + knowledge
base retrieval, rerank and LLM generation) over a QA golden set, then scores
the collected answers with ragas metrics:

- generation: ``faithfulness``, ``answer_relevancy``
- retrieval: ``context_precision`` (with reference), ``context_recall``
- answer fidelity: ``semantic_similarity`` against the golden answer

The same runner is used by the admin "性能测试" tab (benchmark kind
``ragas_quality``) and by ``scripts/eval_ragas.py`` so a number produced in
either path is computed with exactly the same code.
"""

from __future__ import annotations

import json
import math
import random
import sys
import time
import types
import warnings
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models.meeting import Meeting
from app.schemas.analysis import MeetingChatRequest
from app.services.meeting_chat import answer_meeting_question

# Bundled QA datasets live next to the backend package (see Dockerfile COPY).
DATASET_DIR = Path(__file__).resolve().parents[2] / "eval_data"

ProgressCallback = Callable[[int, str], Awaitable[None]]

METRIC_NAMES = (
    "faithfulness",
    "answer_relevancy",
    "context_precision",
    "context_recall",
    "semantic_similarity",
)

# Ragas 0.4.x metric instances (legacy API, still supported by evaluate()).
METRIC_REGISTRY: dict[str, Any] = {}


def _patch_langchain_community_vertexai() -> None:
    """ragas 0.4.3 imports a module removed from langchain-community 0.4.x.

    ``ragas/llms/base.py`` unconditionally does
    ``from langchain_community.chat_models.vertexai import ChatVertexAI`` at
    import time, but langchain-community 0.4 moved Vertex AI to a standalone
    integration package. The binding is only used when constructing Vertex AI
    models, which this project never does, so a stub module is enough.
    """

    try:
        import langchain_community.chat_models.vertexai  # noqa: F401

        return
    except ModuleNotFoundError:
        pass
    if "langchain_community.chat_models.vertexai" in sys.modules:
        return
    stub = types.ModuleType("langchain_community.chat_models.vertexai")
    stub.ChatVertexAI = None
    sys.modules["langchain_community.chat_models.vertexai"] = stub


def _load_ragas() -> tuple[Any, Any, Any]:
    """Import ragas lazily and return ``(evaluate, EvaluationDataset, metrics)``."""

    _patch_langchain_community_vertexai()
    from ragas import EvaluationDataset, evaluate
    from ragas.metrics import (  # type: ignore[attr-defined]
        answer_relevancy,
        answer_similarity,
        context_precision,
        context_recall,
        faithfulness,
    )

    global METRIC_REGISTRY
    METRIC_REGISTRY = {
        "faithfulness": faithfulness,
        "answer_relevancy": answer_relevancy,
        "context_precision": context_precision,
        "context_recall": context_recall,
        "semantic_similarity": answer_similarity,
    }
    return evaluate, EvaluationDataset, METRIC_REGISTRY


def load_qa_entries(
    dataset_file: str | None = None,
    entries: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Normalize a bundled or inline QA golden set into evaluation entries.

    Accepts either ``dataset_file`` (file name inside ``eval_data/`` or an
    absolute path) or inline ``entries``. Each item may use the canonical
    keys ``question`` / ``correctAnswer`` or ragas-style aliases.
    """

    raw: Any
    if dataset_file:
        path = Path(dataset_file)
        if not path.is_absolute():
            path = DATASET_DIR / path
        if not path.is_file():
            raise FileNotFoundError(f"测试集文件不存在: {path}")
        raw = json.loads(path.read_text(encoding="utf-8"))
    elif entries is not None:
        raw = entries
    else:
        raise ValueError("需要 dataset_file 或 entries")

    items = raw if isinstance(raw, list) else raw.get("entries", [])
    if not isinstance(items, list):
        raise ValueError("测试集必须是 JSON 数组或包含 entries 数组的对象")

    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        question = (
            item.get("question")
            or item.get("user_input")
            or item.get("query")
        )
        reference = (
            item.get("correctAnswer")
            or item.get("reference")
            or item.get("ground_truth")
        )
        if not question or not reference:
            continue
        normalized.append(
            {
                "index": index,
                "question": str(question).strip(),
                "reference": str(reference).strip(),
                "question_type": str(
                    item.get("questionType") or item.get("question_type") or ""
                ),
                "options": str(item.get("options") or ""),
                "source": item,
            }
        )
    if not normalized:
        raise ValueError("测试集中没有包含 question + correctAnswer 的有效条目")
    return normalized


def _sample_entries(
    entries: list[dict[str, Any]],
    *,
    max_items: int = 0,
    seed: int = 42,
) -> list[dict[str, Any]]:
    if max_items and max_items > 0 and max_items < len(entries):
        return random.Random(seed).sample(entries, max_items)
    return entries


def ModelServiceEmbeddings(base_url: str, *, timeout: float = 300) -> Any:
    """Build modern ragas embeddings backed by the project's BGE model service.

    Ragas 0.4.x ``metrics.collections`` require embeddings that subclass its
    modern ``BaseRagasEmbedding`` interface. The model service is not
    OpenAI-compatible, so we define a small adapter lazily (after ragas is
    importable) and call ``/v1/embeddings`` with dense vectors only.
    """

    from ragas.embeddings.base import BaseRagasEmbedding

    class _ModelServiceEmbeddings(BaseRagasEmbedding):
        def __init__(self, endpoint: str, request_timeout: float) -> None:
            super().__init__()
            self.endpoint = endpoint.rstrip("/")
            self.request_timeout = request_timeout

        def embed_text(self, text: str, **kwargs: Any) -> list[float]:
            return self._dense([text])[0]

        async def aembed_text(self, text: str, **kwargs: Any) -> list[float]:
            return (await self._adense([text]))[0]

        def embed_texts(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
            return self._dense(texts)

        async def aembed_texts(
            self, texts: list[str], **kwargs: Any
        ) -> list[list[float]]:
            return await self._adense(texts)

        # Legacy ragas metric call-sites (answer_relevancy) use the
        # embed_query / embed_documents contract as well.
        def embed_query(self, text: str) -> list[float]:
            return self._dense([text])[0]

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return self._dense(texts)

        async def aembed_query(self, text: str) -> list[float]:
            return (await self._adense([text]))[0]

        async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
            return await self._adense(texts)

        def set_run_config(self, run_config: Any) -> None:
            return None

        def _dense(self, texts: list[str]) -> list[list[float]]:
            with httpx.Client(timeout=self.request_timeout) as client:
                response = client.post(
                    f"{self.endpoint}/v1/embeddings",
                    json={"texts": texts, "include_sparse": False},
                )
                response.raise_for_status()
                data = response.json()["data"]
            return [list(item["dense"]) for item in data]

        async def _adense(self, texts: list[str]) -> list[list[float]]:
            async with httpx.AsyncClient(timeout=self.request_timeout) as client:
                response = await client.post(
                    f"{self.endpoint}/v1/embeddings",
                    json={"texts": texts, "include_sparse": False},
                )
                response.raise_for_status()
                data = response.json()["data"]
            return [list(item["dense"]) for item in data]

    return _ModelServiceEmbeddings(base_url, timeout)


def _build_judge_llm(settings: Any) -> Any:
    from openai import OpenAI
    from ragas.llms import llm_factory

    if not settings.llm_base_url or not settings.resolved_llm_api_key or not settings.llm_model:
        raise RuntimeError("LLM_BASE_URL / LLM_API_KEY / LLM_MODEL 未配置，无法运行 Ragas 评测")
    client = OpenAI(
        api_key=settings.resolved_llm_api_key,
        base_url=settings.llm_base_url.rstrip("/"),
        timeout=120,
        max_retries=2,
    )
    return llm_factory(
        settings.llm_model,
        provider="openai",
        client=client,
        temperature=0.0,
        max_tokens=4096,
    )


async def _collect_samples(
    factory: async_sessionmaker[AsyncSession],
    *,
    entries: list[dict[str, Any]],
    meeting_id: UUID,
    scope: str,
    on_progress: ProgressCallback | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    async with factory() as session:
        meeting = await session.get(Meeting, meeting_id)
        if meeting is None or meeting.deleted_at is not None:
            raise ValueError(f"会议不存在: {meeting_id}")
        organization_id = UUID(str(meeting.organization_id))

        total = len(entries)
        for position, entry in enumerate(entries, start=1):
            started = time.perf_counter()
            try:
                response = await answer_meeting_question(
                    session,
                    meeting_id=meeting_id,
                    payload=MeetingChatRequest(
                        meeting_id=meeting_id,
                        question=entry["question"][:2000],
                        scope=scope,  # type: ignore[arg-type]
                    ),
                    organization_id=organization_id,
                )
            except Exception as exc:  # noqa: BLE001 - keep one bad item from killing the run
                skipped.append(
                    {
                        "index": entry["index"],
                        "question": entry["question"],
                        "error": str(exc)[:500],
                    }
                )
                await session.rollback()
                continue

            contexts = [
                source.content
                for source in response.sources
                if source.content and str(source.content).strip()
            ]
            latency_ms = int((time.perf_counter() - started) * 1000)
            if response.status == "INSUFFICIENT_CONTEXT" or not contexts:
                skipped.append(
                    {
                        "index": entry["index"],
                        "question": entry["question"],
                        "error": "INSUFFICIENT_CONTEXT: 检索未返回可用片段",
                    }
                )
                continue
            samples.append(
                {
                    "user_input": entry["question"],
                    "response": response.answer,
                    "retrieved_contexts": contexts,
                    "reference": entry["reference"],
                    "question_type": entry.get("question_type", ""),
                    "index": entry["index"],
                    "latency_ms": latency_ms,
                    "context_count": len(contexts),
                }
            )
            if on_progress is not None and position % 5 == 0:
                await on_progress(
                    int(position / total * 60),
                    f"正在问答 {position}/{total}",
                )
    return samples, skipped, {"meeting_id": str(meeting_id), "scope": scope}


async def run_ragas_eval(
    *,
    entries: list[dict[str, Any]],
    meeting_id: str | UUID,
    scope: str = "MEETING_AND_KB",
    metrics: list[str] | None = None,
    max_items: int = 0,
    seed: int = 42,
    on_progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Run the production meeting Q&A pipeline and score it with ragas."""

    selected_entries = _sample_entries(entries, max_items=max_items, seed=seed)
    if not selected_entries:
        raise ValueError("评测集合为空")
    evaluate, EvaluationDataset, registry = _load_ragas()

    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        samples, skipped, context = await _collect_samples(
            factory,
            entries=selected_entries,
            meeting_id=UUID(str(meeting_id)),
            scope=scope,
            on_progress=on_progress,
        )
        if not samples:
            raise RuntimeError(
                f"全部 {len(selected_entries)} 条测试均未得到可用上下文/答案，无法评分"
            )
        if on_progress is not None:
            await on_progress(65, f"开始计算 Ragas 指标（{len(samples)} 条）")

        metric_names = [name for name in METRIC_NAMES if name in (metrics or METRIC_NAMES)]
        judge_llm = _build_judge_llm(settings)
        embeddings = ModelServiceEmbeddings(settings.model_service_url)
        metric_instances = [registry[name] for name in metric_names]
        # Legacy metric instances expose a different score key (e.g. the
        # semantic similarity instance is named answer_similarity).
        score_key_by_name = {
            name: instance.name for name, instance in zip(metric_names, metric_instances)
        }

        warnings.filterwarnings("ignore", category=DeprecationWarning)
        result = evaluate(
            dataset=EvaluationDataset.from_list(
                [
                    {
                        key: sample[key]
                        for key in ("user_input", "response", "retrieved_contexts", "reference")
                    }
                    for sample in samples
                ]
            ),
            metrics=metric_instances,
            llm=judge_llm,
            embeddings=embeddings,
            show_progress=False,
        )
        warnings.resetwarnings()

        per_sample: list[dict[str, Any]] = []
        for sample, score_row in zip(samples, result.scores, strict=True):
            per_sample.append(
                {
                    "index": sample["index"],
                    "question_type": sample["question_type"],
                    "question": sample["user_input"][:300],
                    "answer": sample["response"][:800],
                    "reference": sample["reference"][:800],
                    "context_count": sample["context_count"],
                    "latency_ms": sample["latency_ms"],
                    **{
                        name: _safe_float(score_row.get(score_key_by_name[name]))
                        for name in metric_names
                    },
                }
            )
        aggregate = {
            name: _mean(
                [_safe_float(row.get(score_key_by_name[name])) for row in result.scores]
            )
            for name in metric_names
        }
        if on_progress is not None:
            await on_progress(100, "完成")
        return {
            "environment": {
                "device": settings.bge_device,
                "embedding_model": settings.embedding_model,
                "reranker_model": settings.reranker_model,
                "llm_model": settings.llm_model,
                "evaluator": "ragas",
            },
            "dataset": {"meeting_id": context["meeting_id"], "scope": context["scope"]},
            "queries": len(samples),
            "total_entries": len(selected_entries),
            "skipped": skipped,
            "metrics": aggregate,
            "per_sample": per_sample,
        }
    finally:
        await engine.dispose()


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _mean(values: list[float | None]) -> float | None:
    valid = [value for value in values if value is not None]
    if not valid:
        return None
    return round(sum(valid) / len(valid), 4)
