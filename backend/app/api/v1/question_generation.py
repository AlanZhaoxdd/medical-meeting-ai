from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthContext, CurrentUserDependency, require_kb_access, require_role
from app.core.exceptions import ConflictError, NotFoundError
from app.db.session import get_session
from app.models.kb import Chunk, Document, OutboxEvent
from app.models.meeting import AiTask, AiTaskStatus, Meeting, MeetingQuestion, QuestionEvidence
from app.schemas.kb import Role
from app.schemas.meeting_review import QuestionEvidenceRead, QuestionGenerationRead

router = APIRouter(prefix="/meetings", tags=["会议问题生成"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
EditorDependency = Annotated[AuthContext, Depends(require_role(Role.EDITOR))]


async def _meeting(
    session: AsyncSession,
    meeting_id: UUID,
    current: AuthContext,
    *,
    allow_deleted_kb: bool = False,
) -> Meeting:
    meeting = await session.scalar(
        select(Meeting).where(Meeting.id == meeting_id, Meeting.deleted_at.is_(None))
    )
    if meeting is None or meeting.organization_id != current.organization_id:
        raise NotFoundError("会议", "meeting_not_found")
    if meeting.knowledge_base_id is not None and not allow_deleted_kb:
        await require_kb_access(session, current, meeting.knowledge_base_id)
    return meeting


def _read(task: AiTask) -> QuestionGenerationRead:
    return QuestionGenerationRead(
        task_id=task.id,
        status=str(task.status.value if isinstance(task.status, AiTaskStatus) else task.status),
        current_stage=task.current_stage,
        progress=task.progress,
        message=task.message,
        cutpoint_count=task.cutpoint_count,
        open_question_count=task.open_question_count,
        error_message=task.error_message,
        retry_count=task.retry_count,
    )


@router.get("/{meeting_id}/question-generation", response_model=QuestionGenerationRead)
async def get_question_generation(
    meeting_id: UUID, session: SessionDependency, current: CurrentUserDependency
) -> QuestionGenerationRead:
    await _meeting(session, meeting_id, current)
    task = await session.scalar(
        select(AiTask)
        .where(AiTask.meeting_id == meeting_id, AiTask.task_type == "QUESTION_GENERATION")
        .order_by(AiTask.created_at.desc())
    )
    if task is None:
        raise NotFoundError("AI 任务", "question_generation_not_found")
    return _read(task)


@router.post("/{meeting_id}/question-generation/retry", response_model=QuestionGenerationRead)
async def retry_question_generation(
    meeting_id: UUID, session: SessionDependency, current: EditorDependency
) -> QuestionGenerationRead:
    await _meeting(session, meeting_id, current)
    task = await session.scalar(
        select(AiTask)
        .where(AiTask.meeting_id == meeting_id, AiTask.task_type == "QUESTION_GENERATION")
        .order_by(AiTask.created_at.desc())
        .with_for_update()
    )
    if task is None:
        raise NotFoundError("AI 任务", "question_generation_not_found")
    if task.status not in {AiTaskStatus.FAILED, AiTaskStatus.CANCELLED}:
        raise ConflictError("question_generation_not_retryable", "当前任务不可重试")
    task.status = AiTaskStatus.QUEUED
    task.current_stage = "queued"
    task.progress = 0
    task.error_code = None
    task.error_message = None
    task.retry_count += 1
    task.completed_at = None
    session.add(
        OutboxEvent(
            idempotency_key=f"question-generation-retry:{task.id}:{task.retry_count}",
            event_type="question_generation.requested",
            aggregate_id=str(meeting_id),
            payload={"task_id": str(task.id), "meeting_id": str(meeting_id)},
            status="PENDING",
        )
    )
    await session.commit()
    return _read(task)


@router.get(
    "/{meeting_id}/questions/{question_id}/evidences", response_model=list[QuestionEvidenceRead]
)
async def list_question_evidences(
    meeting_id: UUID, question_id: UUID, session: SessionDependency, current: CurrentUserDependency
) -> list[QuestionEvidenceRead]:
    meeting = await _meeting(session, meeting_id, current, allow_deleted_kb=True)
    question = await session.scalar(
        select(MeetingQuestion).where(
            MeetingQuestion.id == question_id,
            MeetingQuestion.meeting_id == meeting_id,
            MeetingQuestion.deleted_at.is_(None),
        )
    )
    if question is None:
        raise NotFoundError("问题", "question_not_found")
    rows = (
        await session.execute(
            select(QuestionEvidence, Chunk, Document)
            .outerjoin(Chunk, Chunk.chunk_id == QuestionEvidence.chunk_id)
            .outerjoin(Document, Document.id == QuestionEvidence.document_id)
            .where(
                QuestionEvidence.question_id == question_id,
                or_(
                    and_(
                        Document.organization_id == current.organization_id,
                        Document.knowledge_base_id == meeting.knowledge_base_id,
                        or_(
                            Chunk.id.is_(None),
                            and_(
                                Chunk.organization_id == current.organization_id,
                                Chunk.knowledge_base_id == meeting.knowledge_base_id,
                            ),
                        ),
                    ),
                    QuestionEvidence.document_id.is_(None),
                    Document.id.is_(None),
                ),
            )
            .order_by(QuestionEvidence.created_at, QuestionEvidence.id)
        )
    ).all()
    return [
        QuestionEvidenceRead(
            document_title=document.filename if document is not None else None,
            section_title=(chunk.heading_path or [None])[0] if chunk is not None else None,
            quote=evidence.quote,
            evidence_summary=evidence.evidence_summary or evidence.quote,
            chunk_text=chunk.content if chunk is not None else evidence.quote,
            vector_score=evidence.vector_score,
            keyword_score=evidence.keyword_score,
            rerank_score=evidence.rerank_score,
        )
        for evidence, chunk, document in rows
    ]
