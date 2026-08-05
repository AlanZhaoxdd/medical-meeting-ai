from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Optional, cast
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.meeting import AnalysisStatus, Meeting, MeetingStatus


class MeetingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _active_statement() -> Select[tuple[Meeting]]:
        return select(Meeting).where(Meeting.deleted_at.is_(None))

    async def get_active(
        self, meeting_id: UUID, *, organization_id: Optional[UUID] = None
    ) -> Optional[Meeting]:
        statement = self._active_statement().where(Meeting.id == meeting_id)
        if organization_id is not None:
            statement = statement.where(Meeting.organization_id == organization_id)
        return cast(Optional[Meeting], await self.session.scalar(statement))

    async def create(self, meeting: Meeting) -> Meeting:
        self.session.add(meeting)
        await self.session.flush()
        await self.session.refresh(meeting)
        return meeting

    async def list_active(
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
        statement = self._active_statement()
        if meeting_status is not None:
            statement = statement.where(Meeting.meeting_status == meeting_status)
        if organization_id is not None:
            statement = statement.where(Meeting.organization_id == organization_id)
        if analysis_status is not None:
            statement = statement.where(Meeting.analysis_status == analysis_status)
        if keyword:
            pattern = f"%{keyword.strip()}%"
            statement = statement.where(
                or_(Meeting.title.ilike(pattern), Meeting.organizer.ilike(pattern))
            )
        if starts_at_from is not None:
            statement = statement.where(Meeting.starts_at >= starts_at_from)
        if starts_at_to is not None:
            statement = statement.where(Meeting.starts_at <= starts_at_to)

        count_statement = select(func.count()).select_from(statement.order_by(None).subquery())
        total = int(await self.session.scalar(count_statement) or 0)
        statement = statement.order_by(Meeting.starts_at.asc(), Meeting.created_at.desc())
        statement = statement.offset((page - 1) * page_size).limit(page_size)
        result = await self.session.scalars(statement)
        return result.all(), total

    async def save(self, meeting: Meeting) -> Meeting:
        await self.session.flush()
        await self.session.refresh(meeting)
        return meeting
