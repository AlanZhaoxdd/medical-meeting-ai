from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from celery import Task
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.exceptions import AppException
from app.models.kb import BenchmarkRun, Chunk, Document, IngestionJob, OutboxEvent
from app.models.meeting import AiTask, AiTaskStatus
from app.schemas.kb import DocumentStatus
from app.services.benchmark import (
    run_embedding_benchmark,
    run_retrieval_quality_eval,
    run_search_latency_benchmark,
)
from app.services.model_client import ModelServiceClient
from app.services.question_generation import claim_task
from app.services.vector_store import VectorStore
from app.worker.celery_app import celery_app
from app.worker.graph import run_graph
from app.worker.meeting_import import run_meeting_import  # noqa: F401,E402
from app.worker.progress import publish_progress
from app.worker.question_graph import QuestionGenerationState, build_question_graph


async def _mark_failed(job_id: str, exc: Exception) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            job = await session.scalar(select(IngestionJob).where(IngestionJob.job_id == job_id))
            if job is None:
                return
            document = await session.get(Document, job.document_id)
            code = exc.code if isinstance(exc, AppException) else "ingestion_unexpected_error"
            message = exc.message if isinstance(exc, AppException) else str(exc)
            if document is not None:
                document.status = DocumentStatus.FAILED.value
                document.error_code = code
                document.error_message = message[:2000]
            await publish_progress(
                session,
                job_id,
                status="FAILED",
                node=job.current_node,
                progress=job.progress,
                error_code=code,
                error_message=message[:2000],
                terminal=True,
            )
    finally:
        await engine.dispose()


def _execute(task: Task, job_id: str, resume: bool) -> dict[str, Any]:
    try:
        asyncio.run(run_graph(job_id, resume=resume))
        return {"job_id": job_id, "resumed": resume}
    except Exception as exc:
        asyncio.run(_mark_failed(job_id, exc))
        retryable = not isinstance(exc, AppException) or exc.status_code >= 500
        if retryable and task.request.retries < task.max_retries:
            raise task.retry(exc=exc, countdown=min(300, 2 ** (task.request.retries + 1))) from exc
        raise


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True, max_retries=5, name="app.worker.tasks.run_ingestion"
)
def run_ingestion(self: Task, job_id: str) -> dict[str, Any]:
    return _execute(self, job_id, False)


@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True, max_retries=5, name="app.worker.tasks.resume_ingestion"
)
def resume_ingestion(self: Task, job_id: str) -> dict[str, Any]:
    return _execute(self, job_id, True)


def _dispatch_queued_ingestion_jobs(job_ids: list[str]) -> dict[str, int]:
    """Redeliver durable QUEUED jobs; duplicate deliveries are graph-idempotent."""
    dispatched = failed = 0
    for job_id in job_ids:
        try:
            celery_app.send_task("app.worker.tasks.run_ingestion", args=[job_id])
            dispatched += 1
        except Exception:
            failed += 1
    return {"dispatched": dispatched, "failed": failed}


async def _reconcile_queued_ingestion_jobs() -> dict[str, int]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=60)
            job_ids = list(
                (
                    await session.scalars(
                        select(IngestionJob.job_id)
                        .where(
                            IngestionJob.status == "QUEUED",
                            IngestionJob.updated_at <= cutoff,
                        )
                        .order_by(IngestionJob.updated_at)
                        .limit(100)
                    )
                ).all()
            )
        return _dispatch_queued_ingestion_jobs(job_ids)
    finally:
        await engine.dispose()


@celery_app.task(name="app.worker.tasks.reconcile_ingestion_jobs")  # type: ignore[untyped-decorator]
def reconcile_ingestion_jobs() -> dict[str, int]:
    return asyncio.run(_reconcile_queued_ingestion_jobs())


async def _publish_chunk_vectors(
    document: Document,
    chunks: list[Chunk],
    *,
    batch_size: int,
    embedding_identity: str,
) -> int:
    vector_store = VectorStore()
    published = 0
    async with ModelServiceClient() as client:
        for start in range(0, len(chunks), batch_size):
            chunk_batch = chunks[start : start + batch_size]
            embeddings = await client.embeddings([chunk.content for chunk in chunk_batch])
            records = [
                {
                    "record_id": chunk.chunk_id,
                    "record_type": "chunk",
                    "organization_id": str(document.organization_id),
                    "knowledge_base_id": str(document.knowledge_base_id),
                    "meeting_id": (str(document.meeting_id) if document.meeting_id else ""),
                    "document_id": str(document.id),
                    "document_version": document.version,
                    "publication_status": "PUBLISHED",
                    "content_type": chunk.content_type,
                    "dense_vector": embedding["dense"],
                    "sparse_vector": {
                        int(key): float(value) for key, value in embedding["sparse"].items()
                    },
                    "embedding_version": embedding_identity,
                }
                for chunk, embedding in zip(chunk_batch, embeddings, strict=True)
            ]
            await vector_store.upsert(records)
            published += len(records)
    return published


async def _sync_outbox_events() -> dict[str, int]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    processed = failed = 0
    try:
        async with factory() as session:
            events = (
                await session.scalars(
                    select(OutboxEvent)
                    .where(OutboxEvent.status.in_(["PENDING", "FAILED"]))
                    .order_by(OutboxEvent.created_at)
                    .limit(50)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for event in events:
                event.attempts += 1
                try:
                    if event.event_type == "vector.delete_document":
                        document_id = event.payload["document_id"]
                        await VectorStore().delete_document(document_id)
                    elif event.event_type == "vector.publish_document":
                        document_id = event.payload["document_id"]
                        document = await session.get(Document, UUID(document_id))
                        if document is None:
                            event.status = "PROCESSED"
                            continue
                        chunks = (
                            await session.scalars(
                                select(Chunk)
                                .where(Chunk.document_id == document.id)
                                .order_by(Chunk.chunk_index)
                            )
                        ).all()
                        await _publish_chunk_vectors(
                            document,
                            list(chunks),
                            batch_size=max(1, min(settings.bge_batch_size, 128)),
                            embedding_identity=settings.embedding_identity,
                        )
                        document.vector_sync_status = "SYNCED"
                    elif event.event_type == "question_generation.requested":
                        celery_app.send_task(
                            "app.worker.tasks.run_question_generation",
                            args=[event.payload["task_id"]],
                        )
                    event.status = "PROCESSED"
                    event.processed_at = datetime.now(timezone.utc)
                    processed += 1
                except Exception:
                    event.status = "FAILED"
                    failed += 1
            await session.commit()
    finally:
        await engine.dispose()
    return {"processed": processed, "failed": failed}


@celery_app.task(name="app.worker.tasks.sync_outbox")  # type: ignore[untyped-decorator]
def sync_outbox() -> dict[str, int]:
    return asyncio.run(_sync_outbox_events())


async def _question_lease_heartbeat(
    factory: async_sessionmaker[AsyncSession],
    task_id: UUID,
    attempt_token: UUID,
    stop: asyncio.Event,
) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=60)
            return
        except TimeoutError:
            async with factory() as session:
                result = await session.execute(
                    update(AiTask)
                    .where(
                        AiTask.id == task_id,
                        AiTask.attempt_token == attempt_token,
                        AiTask.status.in_([AiTaskStatus.RUNNING, AiTaskStatus.RETRYING]),
                    )
                    .values(
                        lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=30)
                    )
                )
                if getattr(result, "rowcount", 0) != 1:
                    raise AppException(
                        409, "stale_task_attempt", "任务执行租约已失效"
                    ) from None
                await session.commit()


async def _run_question_generation(task_id: str) -> dict[str, Any]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    claimed_token: UUID | None = None
    try:
        async with factory() as session:
            task, claimed_token = await claim_task(session, UUID(task_id))
            if task is None:
                existing = await session.get(AiTask, UUID(task_id))
                if existing is None:
                    raise AppException(404, "ai_task_not_found", "AI 任务不存在")
                return {
                    "task_id": task_id,
                    "status": getattr(existing.status, "value", existing.status),
                }
            if task.status in {
                AiTaskStatus.PENDING_REVIEW,
                AiTaskStatus.SUCCEEDED,
                AiTaskStatus.CANCELLED,
            }:
                return {
                    "task_id": task_id,
                    "status": getattr(task.status, "value", task.status),
                }
            state = QuestionGenerationState(
                task_id=task_id,
                meeting_id=str(task.meeting_id),
                org_id=str(task.organization_id),
                source_minutes_version=task.source_version,
                retry_count=task.retry_count,
                max_retries=task.max_retries,
                attempt_token=str(claimed_token),
                logs=[],
            )
        checkpoint_url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        async with AsyncPostgresSaver.from_conn_string(checkpoint_url) as checkpointer:
            await checkpointer.setup()
            graph = build_question_graph(factory, checkpointer)
            stop_heartbeat = asyncio.Event()
            heartbeat = asyncio.create_task(
                _question_lease_heartbeat(
                    factory, UUID(task_id), claimed_token, stop_heartbeat
                )
            )
            graph_run = asyncio.create_task(
                graph.ainvoke(state, {"configurable": {"thread_id": task.thread_id}})
            )
            try:
                done, _pending = await asyncio.wait(
                    {graph_run, heartbeat}, return_when=asyncio.FIRST_COMPLETED
                )
                if heartbeat in done:
                    heartbeat_exception = heartbeat.exception()
                    if heartbeat_exception is not None:
                        graph_run.cancel()
                        await asyncio.gather(graph_run, return_exceptions=True)
                        raise heartbeat_exception
                await graph_run
            finally:
                stop_heartbeat.set()
                await asyncio.gather(heartbeat, return_exceptions=True)
        async with factory() as session:
            completed = await session.get(AiTask, UUID(task_id))
            return {
                "task_id": task_id,
                "status": getattr(completed.status, "value", completed.status)
                if completed is not None
                else AiTaskStatus.FAILED.value,
            }
    except Exception as exc:
        async with factory() as session:
            task = await session.get(AiTask, UUID(task_id))
            if task is not None and (claimed_token is None or task.attempt_token == claimed_token):
                task.retry_count += 1
                task.error_code = getattr(exc, "code", "question_generation_failed")
                task.error_message = str(exc)[:2000]
                retryable = not isinstance(exc, AppException) or exc.status_code >= 500
                if task.retry_count < task.max_retries and retryable:
                    task.status = AiTaskStatus.RETRYING
                else:
                    task.status = AiTaskStatus.FAILED
                    task.completed_at = datetime.now(timezone.utc)
                task.attempt_token = None
                task.lease_expires_at = None
                await session.commit()
        raise
    finally:
        await engine.dispose()


async def _run_benchmark(run_id: str) -> dict[str, Any]:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            benchmark = await session.scalar(
                select(BenchmarkRun)
                .where(BenchmarkRun.id == UUID(run_id))
                .with_for_update()
            )
            if benchmark is None:
                raise AppException(404, "benchmark_run_not_found", "评测任务不存在")
            if benchmark.status in {"RUNNING", "COMPLETED"}:
                return {"run_id": run_id, "status": benchmark.status}
            benchmark.status = "RUNNING"
            benchmark.progress = 0
            benchmark.message = "启动"
            benchmark.error_message = None
            await session.commit()
            params = dict(benchmark.params)
            kind = benchmark.kind

        async def on_progress(progress: int, message: str) -> None:
            async with factory() as session:
                current = await session.get(BenchmarkRun, UUID(run_id))
                if current is None:
                    return
                current.progress = progress
                current.message = message
                await session.commit()

        if kind == "embedding_throughput":
            report = await run_embedding_benchmark(
                params["texts"],
                batch_sizes=params.get("batch_sizes", [1, 4, 8, 16]),
                iterations=params.get("iterations", 10),
                warmup=params.get("warmup", 1),
                on_progress=on_progress,
            )
        elif kind == "search_latency":
            report = await run_search_latency_benchmark(
                params["queries"],
                iterations=params.get("iterations", 50),
                warmup=params.get("warmup", 3),
                dense_limit=params.get("dense_limit", 50),
                sparse_limit=params.get("sparse_limit", 50),
                fusion_limit=params.get("fusion_limit", 15),
                rerank_top=params.get("rerank_top", 5),
                ef=params.get("ef", 128),
                rerank=params.get("rerank", True),
                on_progress=on_progress,
            )
        elif kind == "retrieval_quality":
            report = await run_retrieval_quality_eval(
                params["entries"],
                top_ks=params.get("top_ks", [1, 3, 5, 10]),
                dense_limit=params.get("dense_limit", 50),
                sparse_limit=params.get("sparse_limit", 50),
                fusion_limit=params.get("fusion_limit", 15),
                rerank_top=params.get("rerank_top", 5),
                ef=params.get("ef", 128),
                rerank=params.get("rerank", True),
                on_progress=on_progress,
            )
        else:
            raise AppException(422, "benchmark_kind_invalid", "未知评测类型")
        async with factory() as session:
            current = await session.get(BenchmarkRun, UUID(run_id))
            if current is None:
                return {"run_id": run_id, "status": "MISSING"}
            current.status = "COMPLETED"
            current.progress = 100
            current.message = "完成"
            current.metrics = report
            current.environment = report.get("environment", {})
            current.completed_at = datetime.now(timezone.utc)
            await session.commit()
        return {"run_id": run_id, "status": "COMPLETED"}
    except Exception as exc:
        async with factory() as session:
            current = await session.get(BenchmarkRun, UUID(run_id))
            if current is not None:
                current.status = "FAILED"
                current.error_message = str(exc)[:2000]
                current.completed_at = datetime.now(timezone.utc)
                await session.commit()
        raise
    finally:
        await engine.dispose()


@celery_app.task(bind=True, max_retries=2, name="app.worker.tasks.run_benchmark")  # type: ignore[untyped-decorator]
def run_benchmark(self: Task, run_id: str) -> dict[str, Any]:
    try:
        return asyncio.run(_run_benchmark(run_id))
    except Exception as exc:
        retryable = not isinstance(exc, AppException) or exc.status_code >= 500
        if retryable and self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=min(300, 2 ** (self.request.retries + 1))) from exc
        raise


@celery_app.task(bind=True, max_retries=2, name="app.worker.tasks.run_question_generation")  # type: ignore[untyped-decorator]
def run_question_generation(self: Task, task_id: str) -> dict[str, Any]:
    try:
        return asyncio.run(_run_question_generation(task_id))
    except Exception as exc:
        retryable = not isinstance(exc, AppException) or exc.status_code >= 500
        if retryable and self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=min(300, 2 ** (self.request.retries + 1))) from exc
        raise
