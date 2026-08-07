from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.models.kb import (
    MeetingImport,
    TranscriptRevision,
    TranscriptRevisionBlock,
)
from app.models.meeting import (
    AnalysisStatus,
    Meeting,
    MeetingAnalysisRun,
    MeetingQuestion,
    MeetingQuestionType,
)


class AnalysisBundle:
    """Immutable snapshot of everything an export/PPT/chart task may consume."""

    def __init__(
        self,
        *,
        meeting: Meeting,
        run: MeetingAnalysisRun,
        questions: list[MeetingQuestion],
        transcript_blocks: list[TranscriptRevisionBlock],
    ) -> None:
        self.meeting = meeting
        self.run = run
        self.questions = questions
        self.transcript_blocks = transcript_blocks

    @property
    def modules(self) -> list[dict[str, Any]]:
        return list(self.run.modules or [])

    @property
    def sources(self) -> list[dict[str, Any]]:
        return list(self.run.sources or [])

    @property
    def analysis_version(self) -> int:
        return self.run.source_version

    def transcript_source_text(self, block_id: str | None) -> str:
        """Full confirmed transcript text for a block id (empty when unknown)."""
        if not block_id:
            return ""
        for block in self.transcript_blocks:
            if block.block_id == block_id:
                return block.text or ""
        return ""

    def effective_attendees(self) -> list[str]:
        """Union of attendee metadata and transcript speakers (deduplicated)."""
        names: list[str] = []
        info = self.meeting.meeting_info or {}
        for key in ("advisor_names", "internal_attendees"):
            value = info.get(key)
            if isinstance(value, str):
                names.extend(
                    item.strip() for item in re.split(r"[;,，、\n]", value) if item.strip()
                )
            elif isinstance(value, list):
                names.extend(str(item).strip() for item in value if str(item).strip())
        names.extend(
            block.speaker.strip()
            for block in self.transcript_blocks
            if block.speaker and block.speaker.strip()
        )
        seen: set[str] = set()
        unique: list[str] = []
        for name in names:
            key = name.lower()
            if key not in seen:
                seen.add(key)
                unique.append(name)
        return unique[:200]


def normalize_speaker_name(name: str | None) -> str:
    return (name or "").strip() or "未知发言人"


def speaker_key(name: str | None) -> str:
    """Deterministic stable speaker id derived from the transcript name."""

    return normalize_speaker_name(name).lower()


async def load_analysis_bundle(
    session: AsyncSession,
    *,
    meeting_id: UUID,
    organization_id: UUID,
    require_confirmed: bool = True,
) -> AnalysisBundle:
    """Load the latest succeeded analysis run and its supporting data.

    Raises 409 when the analysis does not exist yet or is not confirmed, so
    exports can never be built from a stale/unconfirmed version.
    """

    meeting = await session.scalar(
        select(Meeting).where(Meeting.id == meeting_id, Meeting.deleted_at.is_(None))
    )
    if meeting is None or meeting.organization_id != organization_id:
        raise NotFoundError()
    run = await session.scalar(
        select(MeetingAnalysisRun)
        .where(
            MeetingAnalysisRun.meeting_id == meeting_id,
            MeetingAnalysisRun.status == "SUCCEEDED",
        )
        .order_by(MeetingAnalysisRun.created_at.desc(), MeetingAnalysisRun.id.desc())
    )
    if run is None:
        raise ConflictError("analysis_result_missing", "AI 纪要尚未生成，请先完成 AI 纪要分析")
    if require_confirmed and meeting.analysis_status is not AnalysisStatus.SUCCEEDED:
        raise ConflictError(
            "analysis_not_confirmed",
            "AI 纪要分析尚未完成或未确认，暂不能导出",
        )

    questions = list(
        (
            await session.scalars(
                select(MeetingQuestion)
                .where(
                    MeetingQuestion.meeting_id == meeting_id,
                    MeetingQuestion.deleted_at.is_(None),
                    MeetingQuestion.analysis_selected.is_(True),
                )
                .order_by(MeetingQuestion.created_at.asc(), MeetingQuestion.id.asc())
            )
        ).all()
    )
    revision = await session.scalar(
        select(TranscriptRevision)
        .join(MeetingImport, MeetingImport.confirmed_revision_id == TranscriptRevision.id)
        .where(
            MeetingImport.meeting_id == meeting_id,
            MeetingImport.organization_id == organization_id,
            MeetingImport.status == "CONFIRMED",
            TranscriptRevision.status == "CONFIRMED",
        )
        .order_by(TranscriptRevision.version.desc())
    )
    blocks: list[TranscriptRevisionBlock] = []
    if revision is not None:
        blocks = list(
            (
                await session.scalars(
                    select(TranscriptRevisionBlock)
                    .where(TranscriptRevisionBlock.revision_id == revision.id)
                    .order_by(TranscriptRevisionBlock.order)
                )
            ).all()
        )
    return AnalysisBundle(meeting=meeting, run=run, questions=questions, transcript_blocks=blocks)


def source_index(item: dict[str, Any]) -> int:
    try:
        return int(item.get("index"))
    except (TypeError, ValueError):
        return 0


def source_id_for(item: dict[str, Any]) -> str:
    chunk_id = item.get("chunk_id")
    question_id = item.get("question_id")
    if chunk_id:
        return str(chunk_id)
    if question_id:
        return str(question_id)
    return f"source-{source_index(item)}"


def is_cut_point(question: MeetingQuestion) -> bool:
    return question.question_type is MeetingQuestionType.CUT_POINT
