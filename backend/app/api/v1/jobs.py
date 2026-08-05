from __future__ import annotations

import asyncio
import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.documents import serialize_job
from app.core.auth import CurrentUserDependency
from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.core.security import decode_access_token
from app.db.session import SessionLocal, get_session
from app.models.kb import IngestionJob, OrganizationMembership, User
from app.schemas.kb import JobRead

router = APIRouter(tags=["处理任务"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]


@router.get("/jobs/{job_id}", response_model=JobRead)
async def get_job(
    job_id: str, session: SessionDependency, current: CurrentUserDependency
) -> JobRead:
    job = await session.scalar(
        select(IngestionJob).where(
            IngestionJob.job_id == job_id,
            IngestionJob.organization_id == current.organization_id,
        )
    )
    if job is None:
        raise NotFoundError("任务", "job_not_found")
    return serialize_job(job)


async def _authorize_websocket(token: str, job_id: str) -> IngestionJob | None:
    try:
        payload = decode_access_token(token)
        user_id = UUID(str(payload["sub"]))
        organization_id = UUID(str(payload["org"]))
    except Exception:
        return None
    async with SessionLocal() as session:
        valid_user = await session.scalar(
            select(User.id)
            .join(
                OrganizationMembership,
                OrganizationMembership.user_id == User.id,
            )
            .where(
                User.id == user_id,
                User.status == "active",
                User.token_version == int(payload.get("ver", -1)),
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.status == "active",
            )
        )
        if valid_user is None:
            return None
        job = await session.scalar(
            select(IngestionJob).where(
                IngestionJob.job_id == job_id,
                IngestionJob.organization_id == organization_id,
            )
        )
        return job


@router.websocket("/ws/jobs/{job_id}")
async def job_progress(
    websocket: WebSocket,
    job_id: str,
    token: str = Query(...),
) -> None:
    job = await _authorize_websocket(token, job_id)
    if job is None:
        await websocket.close(code=4403)
        return
    await websocket.accept()
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    stream = f"job:{job_id}:events"
    last_id = "0-0"
    try:
        await websocket.send_json(serialize_job(job).model_dump(mode="json"))
        while True:
            events = await redis.xread({stream: last_id}, block=15_000, count=20)
            if not events:
                await websocket.send_json({"type": "heartbeat"})
                continue
            for _, messages in events:
                for message_id, fields in messages:
                    last_id = message_id
                    payload = fields.get("payload")
                    await websocket.send_json(
                        json.loads(payload) if payload else fields
                    )
                    if fields.get("terminal") == "true":
                        return
    except (WebSocketDisconnect, asyncio.CancelledError):
        return
    finally:
        await redis.aclose()
