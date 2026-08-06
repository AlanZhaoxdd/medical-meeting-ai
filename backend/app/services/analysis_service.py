from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Sequence, cast
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import ConflictError, NotFoundError
from app.models.kb import OutboxEvent
from app.models.meeting import (
    AiTask,
    AiTaskStatus,
    AiTaskType,
    AnalysisStatus,
    Meeting,
    MeetingAnalysisRun,
    MeetingQuestion,
    MeetingQuestionType,
)
from app.schemas.analysis import AnalysisResult, SourceRegistryItem

ANALYSIS_PROMPT_VERSION = "meeting-analysis-v1"


def analysis_thread_id(meeting_id: UUID, source_version: int) -> str:
    return f"meeting:{meeting_id}:analysis:v{source_version}"


async def list_question_candidates(
    session: AsyncSession,
    *,
    meeting_id: UUID,
    question_type: MeetingQuestionType,
    offset: int,
    limit: int,
) -> tuple[Sequence[MeetingQuestion], int]:
    """Ranked AI-generated candidate pool for one question type.

    Only rows with an assigned candidate_rank participate in swapping; manual
    questions are returned by the verification snapshot instead.
    """
    base = (
        select(MeetingQuestion)
        .where(
            MeetingQuestion.meeting_id == meeting_id,
            MeetingQuestion.question_type == question_type,
            MeetingQuestion.deleted_at.is_(None),
            MeetingQuestion.source == "ai",
            MeetingQuestion.candidate_rank.is_not(None),
        )
        .order_by(MeetingQuestion.candidate_rank.asc(), MeetingQuestion.id.asc())
    )
    total = int(
        await session.scalar(
            select(func.count()).select_from(base.order_by(None).subquery())
        )
        or 0
    )
    items = list((await session.scalars(base.offset(offset).limit(limit))).all())
    return items, total


async def save_analysis_selection(
    session: AsyncSession,
    *,
    meeting: Meeting,
    selected_question_ids: list[UUID],
    user_id: UUID,
) -> tuple[int, int]:
    """Persist which questions are carried into AI analysis.

    All questions of the meeting are reset first, then the given ids are
    marked selected. The selection is only writable while analysis input is
    not locked (NOT_READY / READY / FAILED re-run states).
    """
    if meeting.analysis_status not in {
        AnalysisStatus.NOT_READY,
        AnalysisStatus.READY,
        AnalysisStatus.SUCCEEDED,
        AnalysisStatus.FAILED,
        AnalysisStatus.CANCELLED,
    }:
        raise ConflictError("analysis_locked", "会议已提交分析，问题选择已锁定")
    rows = list(
        (
            await session.scalars(
                select(MeetingQuestion).where(
                    MeetingQuestion.meeting_id == meeting.id,
                    MeetingQuestion.deleted_at.is_(None),
                )
            )
        ).all()
    )
    by_id = {row.id: row for row in rows}
    requested = set(selected_question_ids)
    missing = requested - by_id.keys()
    if missing:
        raise NotFoundError("问题", "question_not_found")
    for row in rows:
        row.analysis_selected = row.id in requested
    cutpoint_count = sum(
        1
        for row in rows
        if row.analysis_selected and row.question_type is MeetingQuestionType.CUT_POINT
    )
    open_count = sum(
        1
        for row in rows
        if row.analysis_selected and row.question_type is MeetingQuestionType.OPEN_ENDED
    )
    meeting.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return cutpoint_count, open_count


def _validate_selection_counts(
    cutpoint_count: int,
    open_count: int,
) -> None:
    missing: list[str] = []
    if cutpoint_count < 1:
        missing.append("cut_point_questions")
    if open_count < 1:
        missing.append("open_ended_questions")
    if missing:
        raise ConflictError(
            "analysis_selection_incomplete",
            "切点问题和开放性问题均须至少选中一条",
            {"missing_conditions": missing},
        )


async def get_or_create_analysis_task(
    session: AsyncSession,
    *,
    meeting: Meeting,
    organization_id: UUID,
    expected_version: int,
    selected_question_ids: list[UUID],
    user_id: UUID,
    force_reanalyze: bool = False,
) -> AiTask:
    """Validate selection and idempotently create/reset an ANALYSIS task."""
    if meeting.verification_version != expected_version:
        raise ConflictError(
            "verification_version_conflict",
            "会议核验版本已变化，请刷新后重试",
        )
    source_version = meeting.verification_version
    existing = await session.scalar(
        select(AiTask).where(
            AiTask.meeting_id == meeting.id,
            AiTask.task_type == AiTaskType.ANALYSIS,
            AiTask.source_version == source_version,
        )
    )
    if existing is not None:
        if existing.status in {
            AiTaskStatus.QUEUED,
            AiTaskStatus.RUNNING,
            AiTaskStatus.RETRYING,
        }:
            return existing
        if existing.status is AiTaskStatus.SUCCEEDED and not force_reanalyze:
            raise ConflictError(
                "analysis_already_succeeded", "该版本已生成分析结果，请使用重新分析"
            )
    if meeting.analysis_status in {
        AnalysisStatus.QUEUED,
        AnalysisStatus.PROCESSING,
    }:
        raise ConflictError("analysis_locked", "会议分析任务正在运行，请等待完成")
    cutpoint_count, open_count = await save_analysis_selection(
        session,
        meeting=meeting,
        selected_question_ids=selected_question_ids,
        user_id=user_id,
    )
    _validate_selection_counts(cutpoint_count, open_count)
    if existing is not None:
        existing.status = AiTaskStatus.QUEUED
        existing.current_stage = "queued"
        existing.progress = 0
        existing.error_code = None
        existing.error_message = None
        existing.completed_at = None
        existing.lease_expires_at = None
        existing.attempt_token = None
        existing.retry_count += 1
        task = existing
        idempotency_key = f"analysis-retry:{task.id}:{task.retry_count}"
    else:
        task = AiTask(
            meeting_id=meeting.id,
            organization_id=organization_id,
            task_type=AiTaskType.ANALYSIS,
            source_version=source_version,
            thread_id=analysis_thread_id(meeting.id, source_version),
            status=AiTaskStatus.QUEUED,
            current_stage="queued",
            progress=0,
            model_name=get_settings().llm_model or "unconfigured",
            prompt_version=ANALYSIS_PROMPT_VERSION,
        )
        session.add(task)
        idempotency_key = f"analysis:{meeting.id}:{source_version}"
    await session.flush()
    session.add(
        OutboxEvent(
            idempotency_key=idempotency_key,
            event_type="analysis.requested",
            aggregate_id=str(meeting.id),
            payload={"task_id": str(task.id), "meeting_id": str(meeting.id)},
            status="PENDING",
        )
    )
    meeting.analysis_status = AnalysisStatus.QUEUED
    meeting.analysis_requested_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(task)
    return task


async def get_analysis_task(
    session: AsyncSession, meeting_id: UUID
) -> AiTask | None:
    return cast(
        AiTask | None,
        await session.scalar(
            select(AiTask)
            .where(
                AiTask.meeting_id == meeting_id,
                AiTask.task_type == AiTaskType.ANALYSIS,
            )
            .order_by(AiTask.created_at.desc(), AiTask.id.desc())
        ),
    )


async def get_latest_analysis_run(
    session: AsyncSession, meeting_id: UUID
) -> MeetingAnalysisRun | None:
    return cast(
        MeetingAnalysisRun | None,
        await session.scalar(
            select(MeetingAnalysisRun)
            .where(
                MeetingAnalysisRun.meeting_id == meeting_id,
                MeetingAnalysisRun.status == "SUCCEEDED",
            )
            .order_by(MeetingAnalysisRun.created_at.desc(), MeetingAnalysisRun.id.desc())
        ),
    )


async def persist_analysis_run(
    session: AsyncSession,
    *,
    task: AiTask,
    result: AnalysisResult,
    sources: list[SourceRegistryItem],
) -> None:
    await session.execute(
        delete(MeetingAnalysisRun).where(
            MeetingAnalysisRun.meeting_id == task.meeting_id,
            MeetingAnalysisRun.task_id == task.id,
        )
    )
    run = MeetingAnalysisRun(
        meeting_id=task.meeting_id,
        task_id=task.id,
        source_version=task.source_version,
        status="SUCCEEDED",
        modules=[module.model_dump(mode="json") for module in result.modules],
        sources=[source.model_dump(mode="json") for source in sources],
        insufficient_notes=result.insufficient_notes,
        error_message=None,
    )
    session.add(run)


def validate_citation_indices(
    modules: list[dict[str, Any]], source_count: int
) -> list[dict[str, Any]]:
    """Drop citations outside the source registry; empty modules stay empty."""
    cleaned: list[dict[str, Any]] = []
    for module in modules:
        citations = [
            int(index)
            for index in module.get("citations", [])
            if isinstance(index, int) and 1 <= index <= source_count
        ]
        cleaned.append({**module, "citations": sorted(set(citations))})
    return cleaned
