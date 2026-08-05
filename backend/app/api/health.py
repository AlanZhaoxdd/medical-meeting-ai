from __future__ import annotations

from typing import Annotated, Union

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session

router = APIRouter(tags=["系统"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]


class HealthRead(BaseModel):
    status: str
    database: str


@router.get("/health", response_model=HealthRead, summary="服务健康检查")
async def health_check(session: SessionDependency) -> Union[HealthRead, JSONResponse]:
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unhealthy", "database": "unavailable"},
        )
    return HealthRead(status="ok", database="available")
