from __future__ import annotations

from datetime import datetime
from typing import Annotated, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import OptionalCurrentUserDependency, require_kb_access
from app.core.exceptions import AppException
from app.models.kb import MeetingImport
from app.db.session import get_session
from app.models.meeting import AnalysisStatus, MeetingStatus
from app.schemas.meeting import (
    MeetingCreate,
    MeetingListRead,
    MeetingRead,
    MeetingStatusUpdate,
    MeetingUpdate,
)
from app.services.meeting import MeetingService

router = APIRouter(prefix="/meetings", tags=["会议管理"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]


async def _import_ids_by_meeting(
    session: AsyncSession, meeting_ids: list[UUID]
) -> dict[UUID, UUID]:
    if not meeting_ids:
        return {}
    rows = (
        await session.execute(
            select(MeetingImport.meeting_id, MeetingImport.id)
            .where(MeetingImport.meeting_id.in_(meeting_ids))
            .order_by(MeetingImport.created_at.desc())
        )
    ).all()
    result: dict[UUID, UUID] = {}
    for meeting_id, import_id in rows:
        if meeting_id is not None:
            result.setdefault(meeting_id, import_id)
    return result


@router.post(
    "",
    response_model=MeetingRead,
    status_code=status.HTTP_201_CREATED,
    summary="创建会议",
)
async def create_meeting(
    payload: MeetingCreate,
    session: SessionDependency,
    current: OptionalCurrentUserDependency,
) -> MeetingRead:
    if current and payload.knowledge_base_id:
        await require_kb_access(session, current, payload.knowledge_base_id)
    meeting = await MeetingService(session).create(
        payload, organization_id=current.organization_id if current else None
    )
    return MeetingRead.model_validate(meeting)


@router.get("", response_model=MeetingListRead, summary="查询会议列表")
async def list_meetings(
    session: SessionDependency,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    meeting_status: Optional[MeetingStatus] = Query(default=None),
    analysis_status: Optional[AnalysisStatus] = Query(default=None),
    keyword: Optional[str] = Query(default=None, min_length=1, max_length=255),
    starts_at_from: Optional[datetime] = Query(default=None),
    starts_at_to: Optional[datetime] = Query(default=None),
    current: OptionalCurrentUserDependency = None,
) -> MeetingListRead:
    _validate_time_filter(starts_at_from, "starts_at_from")
    _validate_time_filter(starts_at_to, "starts_at_to")
    if starts_at_from and starts_at_to and starts_at_from > starts_at_to:
        raise AppException(422, "validation_error", "开始时间范围无效")
    items, total = await MeetingService(session).list(
        page=page,
        page_size=page_size,
        meeting_status=meeting_status,
        analysis_status=analysis_status,
        keyword=keyword,
        starts_at_from=starts_at_from,
        starts_at_to=starts_at_to,
        organization_id=current.organization_id if current else None,
    )
    import_ids = await _import_ids_by_meeting(session, [item.id for item in items])
    return MeetingListRead.create(
        items=[
            MeetingRead.model_validate(item).model_copy(
                update={"import_id": import_ids.get(item.id)}
            )
            for item in items
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/{meeting_id}", response_model=MeetingRead, summary="获取会议详情")
async def get_meeting(
    meeting_id: UUID, session: SessionDependency, current: OptionalCurrentUserDependency
) -> MeetingRead:
    meeting = await MeetingService(session).get(
        meeting_id, organization_id=current.organization_id if current else None
    )
    import_ids = await _import_ids_by_meeting(session, [meeting.id])
    return MeetingRead.model_validate(meeting).model_copy(
        update={"import_id": import_ids.get(meeting.id)}
    )


@router.patch("/{meeting_id}", response_model=MeetingRead, summary="更新会议资料")
async def update_meeting(
    meeting_id: UUID,
    payload: MeetingUpdate,
    session: SessionDependency,
    current: OptionalCurrentUserDependency,
) -> MeetingRead:
    if current and payload.knowledge_base_id:
        await require_kb_access(session, current, payload.knowledge_base_id)
    meeting = await MeetingService(session).update(
        meeting_id, payload, organization_id=current.organization_id if current else None
    )
    return MeetingRead.model_validate(meeting)


@router.patch(
    "/{meeting_id}/status",
    response_model=MeetingRead,
    summary="更新会议业务状态",
)
async def update_meeting_status(
    meeting_id: UUID,
    payload: MeetingStatusUpdate,
    session: SessionDependency,
    current: OptionalCurrentUserDependency,
) -> MeetingRead:
    meeting = await MeetingService(session).change_status(
        meeting_id,
        payload.meeting_status,
        organization_id=current.organization_id if current else None,
    )
    return MeetingRead.model_validate(meeting)


@router.delete("/{meeting_id}", status_code=status.HTTP_204_NO_CONTENT, summary="软删除会议")
async def delete_meeting(
    meeting_id: UUID, session: SessionDependency, current: OptionalCurrentUserDependency
) -> Response:
    await MeetingService(session).delete(
        meeting_id, organization_id=current.organization_id if current else None
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _validate_time_filter(value: Optional[datetime], name: str) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise AppException(422, "validation_error", f"{name} 必须携带时区信息")
