from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.models.meeting import (
    AnalysisStatus,
    Meeting,
    MeetingQuestion,
    MeetingQuestionType,
    VerificationStatus,
)
from app.schemas.meeting_review import (
    MeetingQuestionCreate,
    MeetingQuestionUpdate,
    VerificationEligibility,
)


class MeetingReviewService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_meeting(self, meeting_id: UUID, *, lock: bool = False) -> Meeting:
        statement = select(Meeting).where(Meeting.id == meeting_id, Meeting.deleted_at.is_(None))
        if lock:
            statement = statement.with_for_update()
        meeting = await self.session.scalar(statement)
        if meeting is None:
            raise NotFoundError()
        return meeting

    async def list_questions(
        self, meeting_id: UUID
    ) -> tuple[Sequence[MeetingQuestion], Sequence[MeetingQuestion]]:
        statement = (
            select(MeetingQuestion)
            .where(MeetingQuestion.meeting_id == meeting_id, MeetingQuestion.deleted_at.is_(None))
            .order_by(MeetingQuestion.created_at.asc(), MeetingQuestion.id.asc())
        )
        questions = list((await self.session.scalars(statement)).all())
        return (
            [q for q in questions if q.question_type is MeetingQuestionType.CUT_POINT],
            [q for q in questions if q.question_type is MeetingQuestionType.OPEN_ENDED],
        )

    @staticmethod
    def eligibility(
        meeting: Meeting,
        cut_points: Sequence[MeetingQuestion],
        open_ended: Sequence[MeetingQuestion],
    ) -> VerificationEligibility:
        missing = MeetingReviewService._confirm_missing_conditions(meeting, cut_points, open_ended)
        can_confirm = (
            not missing
            and meeting.verification_status is not VerificationStatus.CONFIRMED
            and meeting.analysis_status is AnalysisStatus.NOT_READY
        )
        can_submit = (
            meeting.verification_status is VerificationStatus.CONFIRMED
            and meeting.analysis_status is AnalysisStatus.READY
            and not missing
        )
        if meeting.analysis_status is not AnalysisStatus.NOT_READY:
            if meeting.analysis_status not in {AnalysisStatus.READY}:
                missing.append("会议已提交分析，核验内容已锁定")
        if meeting.verification_status is not VerificationStatus.CONFIRMED:
            missing.append("请先确认核验完成")
        return VerificationEligibility(
            can_confirm=can_confirm,
            can_submit_analysis=can_submit,
            missing_conditions=missing,
        )

    async def create_question(
        self, meeting_id: UUID, payload: MeetingQuestionCreate, *, user_id: UUID
    ) -> MeetingQuestion:
        meeting = await self.get_meeting(meeting_id, lock=True)
        self._ensure_writable(meeting)
        duplicate = await self.session.scalar(
            select(MeetingQuestion.id).where(
                MeetingQuestion.meeting_id == meeting_id,
                MeetingQuestion.question_type == payload.question_type,
                MeetingQuestion.deleted_at.is_(None),
                func.lower(func.trim(MeetingQuestion.content)) == payload.content.lower(),
            )
        )
        if duplicate is not None:
            raise ConflictError("duplicate_question", "相同类型的问题已存在")
        question = MeetingQuestion(
            meeting_id=meeting_id,
            question_type=payload.question_type,
            content=payload.content,
            source="manual",
            origin="USER_CREATED",
            review_status="USER_EDITED",
            confidence=None,
            created_by=user_id,
            updated_by=user_id,
        )
        self.session.add(question)
        self._mark_changed(meeting)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError("duplicate_question", "相同类型的问题已存在") from exc
        await self.session.refresh(question)
        return question

    async def update_question(
        self,
        meeting_id: UUID,
        question_id: UUID,
        payload: MeetingQuestionUpdate,
        *,
        user_id: UUID,
    ) -> MeetingQuestion:
        meeting = await self.get_meeting(meeting_id, lock=True)
        self._ensure_writable(meeting)
        question = await self._get_question(meeting_id, question_id, lock=True)
        if question.version != payload.expected_version:
            raise self._version_conflict(question.version, payload.expected_version)
        duplicate = await self.session.scalar(
            select(MeetingQuestion.id).where(
                MeetingQuestion.id != question_id,
                MeetingQuestion.meeting_id == meeting_id,
                MeetingQuestion.question_type == question.question_type,
                MeetingQuestion.deleted_at.is_(None),
                func.lower(func.trim(MeetingQuestion.content)) == payload.content.lower(),
            )
        )
        if duplicate is not None:
            raise ConflictError("duplicate_question", "相同类型的问题已存在")
        question.content = payload.content
        if question.origin == "AI_GENERATED":
            question.review_status = "USER_EDITED"
        elif question.origin == "USER_CREATED":
            question.review_status = "USER_EDITED"
        question.version += 1
        question.updated_by = user_id
        self._mark_changed(meeting)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ConflictError("duplicate_question", "相同类型的问题已存在") from exc
        await self.session.refresh(question)
        return question

    async def delete_question(
        self,
        meeting_id: UUID,
        question_id: UUID,
        *,
        expected_version: int,
        user_id: UUID,
    ) -> None:
        meeting = await self.get_meeting(meeting_id, lock=True)
        self._ensure_writable(meeting)
        question = await self._get_question(meeting_id, question_id, lock=True)
        if question.version != expected_version:
            raise self._version_conflict(question.version, expected_version)
        question.deleted_at = datetime.now(timezone.utc)
        question.updated_by = user_id
        self._mark_changed(meeting)
        await self.session.commit()

    async def confirm(self, meeting_id: UUID, *, expected_version: int, user_id: UUID) -> Meeting:
        meeting = await self.get_meeting(meeting_id, lock=True)
        if meeting.verification_status is VerificationStatus.CONFIRMED:
            if meeting.verification_version != expected_version:
                raise self._version_conflict(meeting.verification_version, expected_version)
            return meeting
        if meeting.verification_version != expected_version:
            raise self._version_conflict(meeting.verification_version, expected_version)
        self._ensure_writable(meeting)
        cut_points, open_ended = await self.list_questions(meeting_id)
        missing = self._confirm_missing_conditions(meeting, cut_points, open_ended)
        if missing:
            raise ConflictError(
                "verification_incomplete", "会议核验尚未完成", {"missing_conditions": missing}
            )
        meeting.verification_status = VerificationStatus.CONFIRMED
        meeting.verification_confirmed_at = datetime.now(timezone.utc)
        meeting.verification_confirmed_by = user_id
        meeting.verification_version += 1
        meeting.analysis_status = AnalysisStatus.READY
        for question in [*cut_points, *open_ended]:
            question.review_status = "CONFIRMED"
        await self.session.commit()
        return meeting

    async def submit_analysis(self, meeting_id: UUID, *, expected_version: int) -> Meeting:
        meeting = await self.get_meeting(meeting_id, lock=True)
        # Submission is intentionally idempotent: retries do not require the old
        # expected version and never enqueue work or invoke a model.
        if meeting.analysis_status is AnalysisStatus.QUEUED:
            return meeting
        if meeting.analysis_status is not AnalysisStatus.READY:
            raise ConflictError("analysis_not_ready", "请先完成会议核验确认")
        if meeting.verification_version != expected_version:
            raise self._version_conflict(meeting.verification_version, expected_version)
        cut_points, open_ended = await self.list_questions(meeting_id)
        if meeting.verification_status is not VerificationStatus.CONFIRMED:
            raise ConflictError("verification_not_confirmed", "请先确认会议核验")
        missing = self._confirm_missing_conditions(meeting, cut_points, open_ended)
        if missing:
            raise ConflictError(
                "verification_incomplete", "会议核验尚未完成", {"missing_conditions": missing}
            )
        # TODO(3.1): replace this placeholder-only transition with idempotent
        # analysis-task creation and dispatch. Do not invoke a model in 3.0.
        meeting.analysis_status = AnalysisStatus.QUEUED
        meeting.analysis_requested_at = datetime.now(timezone.utc)
        await self.session.commit()
        return meeting

    async def _get_question(
        self, meeting_id: UUID, question_id: UUID, *, lock: bool
    ) -> MeetingQuestion:
        statement = select(MeetingQuestion).where(
            MeetingQuestion.id == question_id,
            MeetingQuestion.meeting_id == meeting_id,
            MeetingQuestion.deleted_at.is_(None),
        )
        if lock:
            statement = statement.with_for_update()
        question = await self.session.scalar(statement)
        if question is None:
            raise NotFoundError("问题", "question_not_found")
        return question

    @staticmethod
    def _ensure_writable(meeting: Meeting) -> None:
        if meeting.analysis_status not in {AnalysisStatus.NOT_READY, AnalysisStatus.READY}:
            raise ConflictError("verification_locked", "会议已提交分析，核验内容已锁定")

    @classmethod
    def _mark_changed(cls, meeting: Meeting) -> None:
        meeting.verification_version += 1
        meeting.verification_status = VerificationStatus.IN_PROGRESS
        meeting.verification_confirmed_at = None
        meeting.verification_confirmed_by = None
        meeting.analysis_requested_at = None
        meeting.analysis_status = AnalysisStatus.NOT_READY

    @staticmethod
    def _confirm_missing_conditions(
        meeting: Meeting,
        cut_points: Sequence[MeetingQuestion],
        open_ended: Sequence[MeetingQuestion],
    ) -> list[str]:
        missing: list[str] = []
        if not meeting.title or not meeting.title.strip():
            missing.append("会议标题不能为空")
        if meeting.starts_at is None or meeting.ends_at is None:
            missing.append("会议开始和结束时间不能为空")
        elif meeting.ends_at <= meeting.starts_at:
            missing.append("会议结束时间必须晚于开始时间")
        if not cut_points:
            missing.append("至少需要 1 条切点问题")
        if not open_ended:
            missing.append("至少需要 1 条开放性问题")
        return missing

    @staticmethod
    def _version_conflict(actual: int, expected: int) -> ConflictError:
        return ConflictError(
            "verification_version_conflict",
            "核验内容已被其他操作更新，请刷新后重试",
            {"actual_version": actual, "expected_version": expected},
        )
