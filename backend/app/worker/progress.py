from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.kb import IngestionJob


async def publish_progress(
    session: AsyncSession,
    job_id: str,
    *,
    status: str,
    node: str,
    progress: int,
    summary: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    terminal: bool = False,
) -> None:
    job = await session.scalar(
        select(IngestionJob).where(IngestionJob.job_id == job_id)
    )
    if job is None:
        return
    job.status = status
    job.current_node = node
    job.progress = progress
    if summary:
        immutable = {
            key: job.result_summary.get(key)
            for key in ("revision_id", "revision_version", "mode")
            if job.result_summary.get(key) is not None
        }
        job.result_summary = {**immutable, **summary}
    job.error_code = error_code
    job.error_message = error_message
    await session.commit()
    payload = {
        "job_id": job_id,
        "document_id": str(job.document_id),
        "status": status,
        "current_node": node,
        "progress": progress,
        "result_summary": job.result_summary,
        "error_code": error_code,
        "error_message": error_message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        await redis.xadd(
            f"job:{job_id}:events",
            {"payload": json.dumps(payload), "terminal": str(terminal).lower()},
            maxlen=500,
            approximate=True,
        )
    finally:
        await redis.aclose()
