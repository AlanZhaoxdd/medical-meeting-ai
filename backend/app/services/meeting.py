from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.models.meeting import AnalysisStatus, Meeting, MeetingStatus
from app.repositories.meeting import MeetingRepository
from app.schemas.meeting import MeetingCreate, MeetingUpdate

_ALLOWED_TRANSITIONS: dict[MeetingStatus, set[MeetingStatus]] = {
    MeetingStatus.DRAFT: {MeetingStatus.PUBLISHED, MeetingStatus.CANCELLED},
    MeetingStatus.PUBLISHED: {MeetingStatus.IN_PROGRESS, MeetingStatus.CANCELLED},
    MeetingStatus.IN_PROGRESS: {MeetingStatus.COMPLETED, MeetingStatus.CANCELLED},
    MeetingStatus.COMPLETED: {MeetingStatus.ARCHIVED},
    MeetingStatus.CANCELLED: set(),
    MeetingStatus.ARCHIVED: set(),
}
_TERMINAL_STATUSES = {MeetingStatus.CANCELLED, MeetingStatus.ARCHIVED}


class MeetingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = MeetingRepository(session)

    async def create(
        self, payload: MeetingCreate, *, organization_id: Optional[UUID] = None
    ) -> Meeting:
        data = payload.model_dump(mode="python")
        data["online_url"] = self._url_to_string(data["online_url"])
        data["cover_url"] = self._url_to_string(data["cover_url"])
        data["organization_id"] = organization_id
        meeting = Meeting(**data)
        await self.repository.create(meeting)
        await self.session.commit()
        return meeting

    async def get(
        self, meeting_id: UUID, *, organization_id: Optional[UUID] = None
    ) -> Meeting:
        meeting = await self.repository.get_active(meeting_id, organization_id=organization_id)
        if meeting is None:
            raise NotFoundError()
        return meeting

    async def list(
        self,
        *,
        page: int,
        page_size: int,
        meeting_status: Optional[MeetingStatus],
        analysis_status: Optional[AnalysisStatus],
        keyword: Optional[str],
        starts_at_from: Optional[datetime],
        starts_at_to: Optional[datetime],
        organization_id: Optional[UUID] = None,
    ) -> tuple[Sequence[Meeting], int]:
        return await self.repository.list_active(
            page=page,
            page_size=page_size,
            meeting_status=meeting_status,
            analysis_status=analysis_status,
            keyword=keyword,
            starts_at_from=starts_at_from,
            starts_at_to=starts_at_to,
            organization_id=organization_id,
        )

    async def update(
        self,
        meeting_id: UUID,
        payload: MeetingUpdate,
        *,
        organization_id: Optional[UUID] = None,
    ) -> Meeting:
        meeting = await self.get(meeting_id, organization_id=organization_id)
        self._ensure_mutable(meeting)
        data = payload.model_dump(exclude_unset=True, mode="python")
        if "online_url" in data:
            data["online_url"] = self._url_to_string(data["online_url"])
        if "cover_url" in data:
            data["cover_url"] = self._url_to_string(data["cover_url"])
        starts_at = data.get("starts_at", meeting.starts_at)
        ends_at = data.get("ends_at", meeting.ends_at)
        if ends_at <= starts_at:
            raise ConflictError("invalid_time_range", "结束时间必须晚于开始时间")
        for field_name, value in data.items():
            setattr(meeting, field_name, value)
        await self.repository.save(meeting)
        await self.session.commit()
        return meeting

    async def change_status(
        self,
        meeting_id: UUID,
        target: MeetingStatus,
        *,
        organization_id: Optional[UUID] = None,
    ) -> Meeting:
        meeting = await self.get(meeting_id, organization_id=organization_id)
        if target not in _ALLOWED_TRANSITIONS[meeting.meeting_status]:
            raise ConflictError(
                "invalid_state_transition",
                "不允许的会议状态流转",
                {
                    "current_status": meeting.meeting_status.value,
                    "target_status": target.value,
                },
            )
        meeting.meeting_status = target
        if target is MeetingStatus.CANCELLED and meeting.analysis_status in {
            AnalysisStatus.QUEUED,
            AnalysisStatus.PROCESSING,
        }:
            meeting.analysis_status = AnalysisStatus.CANCELLED
        await self.repository.save(meeting)
        await self.session.commit()
        return meeting

    async def delete(self, meeting_id: UUID, *, organization_id: Optional[UUID] = None) -> None:
        meeting = await self.get(meeting_id, organization_id=organization_id)
        meeting.deleted_at = datetime.now(timezone.utc)
        await self.repository.save(meeting)
        await self.session.commit()

    @staticmethod
    def _ensure_mutable(meeting: Meeting) -> None:
        if meeting.meeting_status in _TERMINAL_STATUSES:
            raise ConflictError("meeting_not_editable", "终态会议不允许编辑")

    @staticmethod
    def _url_to_string(value: Optional[object]) -> Optional[str]:
        return str(value) if value is not None else None
