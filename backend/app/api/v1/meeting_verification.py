from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthContext, CurrentUserDependency, require_kb_access, require_role
from app.core.exceptions import ForbiddenError
from app.db.session import get_session
from app.models.meeting import Meeting
from app.schemas.kb import Role
from app.schemas.meeting import MeetingRead
from app.schemas.meeting_review import (
    AnalysisSubmissionRead,
    MeetingQuestionCreate,
    MeetingQuestionRead,
    MeetingQuestionUpdate,
    MeetingVerificationRead,
    VerificationMutation,
)
from app.services.meeting_review import MeetingReviewService

router = APIRouter(prefix="/meetings", tags=["会议核验"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
EditorDependency = Annotated[AuthContext, Depends(require_role(Role.EDITOR))]


@router.get("/{meeting_id}/verification", response_model=MeetingVerificationRead)
async def get_verification(
    meeting_id: UUID, session: SessionDependency, current: CurrentUserDependency
) -> MeetingVerificationRead:
    service = MeetingReviewService(session)
    meeting = await service.get_meeting(meeting_id)
    await _ensure_meeting_access(session, meeting, current)
    return await _snapshot(service, meeting)


@router.post(
    "/{meeting_id}/questions",
    response_model=MeetingQuestionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_question(
    meeting_id: UUID,
    payload: MeetingQuestionCreate,
    session: SessionDependency,
    current: EditorDependency,
) -> MeetingQuestionRead:
    service = MeetingReviewService(session)
    meeting = await service.get_meeting(meeting_id, lock=True)
    await _ensure_meeting_access(session, meeting, current)
    question = await service.create_question(meeting_id, payload, user_id=current.user_id)
    return MeetingQuestionRead.model_validate(question)


@router.patch(
    "/{meeting_id}/questions/{question_id}", response_model=MeetingQuestionRead
)
async def update_question(
    meeting_id: UUID,
    question_id: UUID,
    payload: MeetingQuestionUpdate,
    session: SessionDependency,
    current: EditorDependency,
) -> MeetingQuestionRead:
    service = MeetingReviewService(session)
    meeting = await service.get_meeting(meeting_id, lock=True)
    await _ensure_meeting_access(session, meeting, current)
    question = await service.update_question(
        meeting_id, question_id, payload, user_id=current.user_id
    )
    return MeetingQuestionRead.model_validate(question)


@router.delete(
    "/{meeting_id}/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_question(
    meeting_id: UUID,
    question_id: UUID,
    session: SessionDependency,
    current: EditorDependency,
    expected_version: int = Query(..., ge=1),
) -> Response:
    service = MeetingReviewService(session)
    meeting = await service.get_meeting(meeting_id, lock=True)
    await _ensure_meeting_access(session, meeting, current)
    await service.delete_question(
        meeting_id,
        question_id,
        expected_version=expected_version,
        user_id=current.user_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{meeting_id}/verification/confirm", response_model=MeetingVerificationRead)
async def confirm_verification(
    meeting_id: UUID,
    payload: VerificationMutation,
    session: SessionDependency,
    current: EditorDependency,
) -> MeetingVerificationRead:
    service = MeetingReviewService(session)
    meeting = await service.get_meeting(meeting_id, lock=True)
    await _ensure_meeting_access(session, meeting, current)
    meeting = await service.confirm(
        meeting_id, expected_version=payload.expected_version, user_id=current.user_id
    )
    return await _snapshot(service, meeting)


@router.post("/{meeting_id}/analysis-submissions", response_model=AnalysisSubmissionRead)
async def submit_analysis(
    meeting_id: UUID,
    payload: VerificationMutation,
    session: SessionDependency,
    current: EditorDependency,
) -> AnalysisSubmissionRead:
    service = MeetingReviewService(session)
    meeting = await service.get_meeting(meeting_id, lock=True)
    await _ensure_meeting_access(session, meeting, current)
    meeting = await service.submit_analysis(
        meeting_id, expected_version=payload.expected_version
    )
    return AnalysisSubmissionRead(
        verification=await _snapshot(service, meeting),
        message="会议核验已完成，AI 分析功能将在下一阶段接入。",
    )


async def _snapshot(
    service: MeetingReviewService, meeting: Meeting
) -> MeetingVerificationRead:
    cut_points, open_ended = await service.list_questions(meeting.id)
    return MeetingVerificationRead(
        meeting=MeetingRead.model_validate(meeting),
        cut_point_questions=[MeetingQuestionRead.model_validate(q) for q in cut_points],
        open_ended_questions=[MeetingQuestionRead.model_validate(q) for q in open_ended],
        verification_version=meeting.verification_version,
        eligibility=service.eligibility(meeting, cut_points, open_ended),
    )


async def _ensure_meeting_access(
    session: AsyncSession, meeting: Meeting, current: AuthContext
) -> None:
    if meeting.organization_id != current.organization_id:
        raise ForbiddenError("会议不存在或无权访问")
    if meeting.knowledge_base_id is not None:
        await require_kb_access(session, current, meeting.knowledge_base_id)
