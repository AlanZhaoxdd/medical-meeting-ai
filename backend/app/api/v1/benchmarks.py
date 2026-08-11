from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthContext, require_exact_role
from app.core.config import get_settings
from app.core.exceptions import AppException, NotFoundError
from app.db.session import get_session
from app.models.kb import BenchmarkRun
from app.schemas.benchmark import (
    BenchmarkCreate,
    BenchmarkRead,
    EnvironmentRead,
)
from app.schemas.kb import Role

router = APIRouter(prefix="/admin/benchmarks", tags=["性能评测"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
AdminDependency = Annotated[AuthContext, Depends(require_exact_role(Role.ADMIN))]


def _serialize(run: BenchmarkRun) -> BenchmarkRead:
    return BenchmarkRead(
        id=str(run.id),
        kind=run.kind,
        name=run.name,
        status=run.status,
        progress=run.progress,
        message=run.message,
        environment=run.environment,
        params=run.params,
        metrics=run.metrics,
        error_message=run.error_message,
        created_by=str(run.created_by) if run.created_by else None,
        created_at=run.created_at,
        updated_at=run.updated_at,
        completed_at=run.completed_at,
    )


def _validate_params(kind: str, params: dict[str, Any]) -> None:
    if kind == "retrieval_quality" and not params.get("entries"):
        raise AppException(
            422, "benchmark_params_invalid", "retrieval_quality 需要 params.entries"
        )
    if kind == "search_latency" and not params.get("queries"):
        raise AppException(
            422, "benchmark_params_invalid", "search_latency 需要 params.queries"
        )
    if kind == "embedding_throughput" and not params.get("texts"):
        raise AppException(
            422, "benchmark_params_invalid", "embedding_throughput 需要 params.texts"
        )
    if kind == "ragas_quality":
        if not params.get("meeting_id"):
            raise AppException(
                422, "benchmark_params_invalid", "ragas_quality 需要 params.meeting_id"
            )
        if not params.get("entries") and not params.get("dataset_file"):
            raise AppException(
                422,
                "benchmark_params_invalid",
                "ragas_quality 需要 params.dataset_file 或 params.entries",
            )


@router.get("/environment", response_model=EnvironmentRead)
async def benchmark_environment(_current: AdminDependency) -> EnvironmentRead:
    settings = get_settings()
    return EnvironmentRead(
        device=settings.bge_device,
        embedding_model=settings.embedding_model,
        embedding_strategy=settings.bge_embedding_strategy,
        reranker_model=settings.reranker_model,
        bge_batch_size=settings.bge_batch_size,
    )


@router.post("", response_model=BenchmarkRead, status_code=202)
async def create_benchmark(
    payload: BenchmarkCreate,
    session: SessionDependency,
    current: AdminDependency,
) -> BenchmarkRead:
    _validate_params(payload.kind, payload.params)
    benchmark = BenchmarkRun(
        kind=payload.kind,
        name=payload.name,
        params=payload.params,
        status="PENDING",
        created_by=current.user_id,
    )
    session.add(benchmark)
    await session.commit()
    await session.refresh(benchmark)
    try:
        from app.worker.celery_app import celery_app

        celery_app.send_task("app.worker.tasks.run_benchmark", args=[str(benchmark.id)])
    except Exception as exc:
        benchmark.status = "DISPATCH_FAILED"
        benchmark.error_message = str(exc)[:1000]
        await session.commit()
    return _serialize(benchmark)


@router.get("", response_model=list[BenchmarkRead])
async def list_benchmarks(
    session: SessionDependency,
    _current: AdminDependency,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[BenchmarkRead]:
    runs = (
        await session.scalars(
            select(BenchmarkRun)
            .order_by(BenchmarkRun.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [_serialize(run) for run in runs]


@router.get("/{run_id}", response_model=BenchmarkRead)
async def get_benchmark(
    run_id: UUID,
    session: SessionDependency,
    _current: AdminDependency,
) -> BenchmarkRead:
    benchmark = await session.get(BenchmarkRun, run_id)
    if benchmark is None:
        raise NotFoundError("评测任务", "benchmark_run_not_found")
    return _serialize(benchmark)
