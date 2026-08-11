from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthContext, CurrentUserDependency, require_kb_access, require_role
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.db.session import get_session
from app.models.meeting import AiTaskStatus, Meeting, MeetingQuestion, MeetingQuestionType
from app.schemas.analysis import AnalysisModuleOut, AnalysisRunRead, SourceRegistryItem
from app.schemas.kb import Role
from app.schemas.meeting import MeetingRead
from app.schemas.meeting_review import (
    AnalysisSelectionUpdate,
    AnalysisSubmissionRead,
    AnalysisSubmissionRequest,
    AnalysisTaskRead,
    MeetingQuestionCreate,
    MeetingQuestionRead,
    MeetingQuestionUpdate,
    MeetingVerificationRead,
    QuestionCandidatePage,
    QuestionCandidateRead,
    VerificationMutation,
)
from app.services.analysis_service import (
    get_analysis_task,
    get_latest_analysis_run,
    get_or_create_analysis_task,
    list_question_candidates,
    save_analysis_selection,
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
    await _ensure_meeting_access(session, meeting, current, allow_deleted_kb=True)
    return await _snapshot(service, meeting)


@router.get(
    "/{meeting_id}/question-candidates",
    response_model=QuestionCandidatePage,
    summary="分页获取问题候选池",
)
async def list_candidates(
    meeting_id: UUID,
    session: SessionDependency,
    current: CurrentUserDependency,
    question_type: MeetingQuestionType = Query(...),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=5, ge=1, le=20),
) -> QuestionCandidatePage:
    service = MeetingReviewService(session)
    meeting = await service.get_meeting(meeting_id)
    await _ensure_meeting_access(session, meeting, current)
    items, total = await list_question_candidates(
        session,
        meeting_id=meeting_id,
        question_type=question_type,
        offset=offset,
        limit=limit,
    )
    return QuestionCandidatePage(
        items=[
            QuestionCandidateRead(
                id=question.id,
                question_type=question.question_type,
                rank=question.candidate_rank,
                content=question.content,
                topic=question.topic,
                rationale=question.rationale,
                expected_answer_type=question.expected_answer_type,
                support_score=question.support_score,
                evidence_count=question.evidence_count,
                selected=question.analysis_selected,
                source=question.source,
                version=question.version,
            )
            for question in items
        ],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.put(
    "/{meeting_id}/analysis-selection",
    response_model=MeetingVerificationRead,
    summary="保存带入 AI 分析的问题选择",
)
async def update_analysis_selection(
    meeting_id: UUID,
    payload: AnalysisSelectionUpdate,
    session: SessionDependency,
    current: EditorDependency,
) -> MeetingVerificationRead:
    service = MeetingReviewService(session)
    meeting = await service.get_meeting(meeting_id, lock=True)
    await _ensure_meeting_access(session, meeting, current)
    if meeting.verification_version != payload.expected_version:
        raise ConflictError(
            "verification_version_conflict", "会议核验版本已变化，请刷新后重试"
        )
    await save_analysis_selection(
        session,
        meeting=meeting,
        selected_question_ids=payload.selected_question_ids,
        user_id=current.user_id,
    )
    meeting = await service.get_meeting(meeting_id)
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
    payload: AnalysisSubmissionRequest,
    session: SessionDependency,
    current: EditorDependency,
) -> AnalysisSubmissionRead:
    service = MeetingReviewService(session)
    meeting = await service.get_meeting(meeting_id, lock=True)
    await _ensure_meeting_access(session, meeting, current)
    task = await get_or_create_analysis_task(
        session,
        meeting=meeting,
        organization_id=current.organization_id,
        expected_version=payload.expected_version,
        selected_question_ids=payload.selected_question_ids,
        user_id=current.user_id,
    )
    meeting = await service.get_meeting(meeting_id)
    return AnalysisSubmissionRead(
        verification=await _snapshot(service, meeting),
        message="AI 分析已提交，生成完成后可在 AI 纪要分析页查看结果。",
        task_id=task.id,
        task_status=str(
            task.status.value if isinstance(task.status, AiTaskStatus) else task.status
        ),
    )


@router.get("/{meeting_id}/analysis-task", response_model=AnalysisTaskRead)
async def get_analysis_task_status(
    meeting_id: UUID,
    session: SessionDependency,
    current: CurrentUserDependency,
) -> AnalysisTaskRead:
    service = MeetingReviewService(session)
    meeting = await service.get_meeting(meeting_id)
    await _ensure_meeting_access(session, meeting, current)
    task = await get_analysis_task(session, meeting_id)
    if task is None:
        raise NotFoundError("AI 分析任务", "analysis_task_not_found")
    return AnalysisTaskRead(
        task_id=task.id,
        status=str(task.status.value if isinstance(task.status, AiTaskStatus) else task.status),
        current_stage=task.current_stage,
        progress=task.progress,
        message=task.message,
        error_message=task.error_message,
        retry_count=task.retry_count,
    )


@router.post("/{meeting_id}/analysis/reanalyze", response_model=AnalysisTaskRead)
async def reanalyze_meeting(
    meeting_id: UUID,
    payload: AnalysisSubmissionRequest,
    session: SessionDependency,
    current: EditorDependency,
) -> AnalysisTaskRead:
    service = MeetingReviewService(session)
    meeting = await service.get_meeting(meeting_id, lock=True)
    await _ensure_meeting_access(session, meeting, current)
    existing = await get_analysis_task(session, meeting_id)
    if existing is None:
        raise NotFoundError("AI 分析任务", "analysis_task_not_found")
    selected_ids = list(
        (
            await session.scalars(
                select(MeetingQuestion.id).where(
                    MeetingQuestion.meeting_id == meeting_id,
                    MeetingQuestion.deleted_at.is_(None),
                    MeetingQuestion.analysis_selected.is_(True),
                )
            )
        ).all()
    )
    task = await get_or_create_analysis_task(
        session,
        meeting=meeting,
        organization_id=current.organization_id,
        expected_version=payload.expected_version,
        selected_question_ids=selected_ids,
        user_id=current.user_id,
        force_reanalyze=True,
    )
    return AnalysisTaskRead(
        task_id=task.id,
        status=str(task.status.value if isinstance(task.status, AiTaskStatus) else task.status),
        current_stage=task.current_stage,
        progress=task.progress,
        message=task.message,
        error_message=task.error_message,
        retry_count=task.retry_count,
    )


@router.get("/{meeting_id}/analysis", response_model=AnalysisRunRead)
async def get_analysis_result(
    meeting_id: UUID,
    session: SessionDependency,
    current: CurrentUserDependency,
) -> AnalysisRunRead:
    service = MeetingReviewService(session)
    meeting = await service.get_meeting(meeting_id)
    await _ensure_meeting_access(session, meeting, current)
    run = await get_latest_analysis_run(session, meeting_id)
    if run is None:
        raise NotFoundError("AI 分析结果", "analysis_result_not_found")
    return AnalysisRunRead(
        task_id=run.task_id,
        source_version=run.source_version,
        modules=[AnalysisModuleOut.model_validate(module) for module in run.modules],
        sources=[SourceRegistryItem.model_validate(source) for source in run.sources],
        insufficient_notes=[str(note) for note in run.insufficient_notes],
        created_at=run.created_at.isoformat(),
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
    session: AsyncSession,
    meeting: Meeting,
    current: AuthContext,
    *,
    allow_deleted_kb: bool = False,
) -> None:
    if meeting.organization_id != current.organization_id:
        raise ForbiddenError("会议不存在或无权访问")
    if meeting.knowledge_base_id is not None and not allow_deleted_kb:
        await require_kb_access(session, current, meeting.knowledge_base_id)
