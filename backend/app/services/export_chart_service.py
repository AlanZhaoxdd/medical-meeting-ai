from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.models.export import ChartSpec
from app.models.meeting import MeetingQuestion
from app.schemas.export import ChartSpecRead
from app.services.export_bundle import (
    AnalysisBundle,
    is_cut_point,
    normalize_speaker_name,
    source_id_for,
)
from app.services.export_model_clients import (
    ChartPlanModelClient,
    ChartPlanResult,
    ChartMentionItem,
    StanceItem,
)


STANCE_LABELS = {
    "SUPPORT": "明确支持",
    "CONDITIONAL_SUPPORT": "条件支持",
    "NEUTRAL": "中立或信息不足",
    "OPPOSE": "明确反对",
    "NOT_MENTIONED": "未表态",
}
STANCE_WITH_EVIDENCE = {"SUPPORT", "CONDITIONAL_SUPPORT", "NEUTRAL", "OPPOSE"}


def _normalize_source_ids(values: list[str] | None) -> set[str]:
    result: set[str] = set()
    for value in values or []:
        text = str(value).strip()
        if text:
            result.add(text)
    return result


def _mention_valid(
    item: ChartMentionItem,
    *,
    source_by_id: dict[str, dict[str, Any]],
    speaker_keys: set[str],
) -> bool:
    """Reject hallucinated evidence: source must exist and speaker must match."""

    speaker = normalize_speaker_name(item.speakerName).lower()
    if speaker not in speaker_keys:
        return False
    valid_ids: list[str] = []
    for source_id in _normalize_source_ids(item.sourceIds):
        source = source_by_id.get(source_id)
        if source is None:
            continue
        if normalize_speaker_name(source.get("speaker_name")).lower() != speaker:
            continue
        valid_ids.append(source_id)
    return bool(valid_ids)


def _evidence_for(
    source_id: str,
    source_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    source = source_by_id.get(source_id, {})
    return {
        "speakerId": normalize_speaker_name(source.get("speaker_name")).lower(),
        "speakerName": source.get("speaker_name"),
        "sourceId": source_id,
        "timestamp": source.get("timestamp"),
        "snippet": str(source.get("snippet") or "")[:400],
    }


async def plan_charts(
    session: AsyncSession,
    *,
    bundle: AnalysisBundle,
    chart_type: str,
    target_question_id: UUID | None,
    metric: str,
    model_client: ChartPlanModelClient | None = None,
) -> list[ChartSpec]:
    """LLM classification + deterministic backend aggregation."""

    client = model_client or ChartPlanModelClient()
    cutpoint_questions = [q for q in bundle.questions if is_cut_point(q)]
    if not cutpoint_questions:
        raise ConflictError("chart_no_cutpoint", "尚未选择切点问题，无法生成图表")

    target: MeetingQuestion | None = None
    if target_question_id is not None:
        target = next(
            (q for q in cutpoint_questions if q.id == target_question_id),
            None,
        )
        if target is None:
            raise ConflictError("chart_target_not_found", "指定的切点问题不在分析选择中")
    else:
        target = cutpoint_questions[0]

    transcript_sources = [
        item
        for item in bundle.sources
        if item.get("type") == "transcript"
        and item.get("speaker_name")
        and item.get("snippet")
    ]
    if not transcript_sources:
        raise ConflictError(
            "chart_no_transcript_evidence",
            "未检索到带发言人的转写证据，无法生成图表",
        )
    source_by_id = {
        source_id_for(item): item for item in transcript_sources
    }
    attendees = bundle.effective_attendees()
    if len(attendees) < 2:
        raise ConflictError(
            "chart_attendees_insufficient",
            "有效参会者数量过少，无法形成有意义的统计分布",
        )

    payload = {
        "meeting_context": {
            "title": bundle.meeting.title,
            "date": bundle.meeting.starts_at.isoformat() if bundle.meeting.starts_at else None,
            "organizer": bundle.meeting.organizer,
            "topic": bundle.meeting.topic,
        },
        "cutpoint_questions": [
            {
                "id": str(q.id),
                "content": q.content,
                "rationale": q.rationale,
                "topic": q.topic,
            }
            for q in cutpoint_questions
        ],
        "transcript_sources": [
            {
                "sourceId": source_id_for(item),
                "speakerName": item.get("speaker_name"),
                "timestamp": item.get("timestamp"),
                "snippet": item.get("snippet"),
            }
            for item in transcript_sources
        ],
        "effective_attendees": attendees[:200],
    }
    plan: ChartPlanResult = await client.plan(payload)
    return await _aggregate_and_persist(
        session,
        bundle=bundle,
        plan=plan,
        chart_type=chart_type,
        target=target,
        metric=metric,
        source_by_id=source_by_id,
        attendees=attendees,
    )


async def _aggregate_and_persist(
    session: AsyncSession,
    *,
    bundle: AnalysisBundle,
    plan: ChartPlanResult,
    chart_type: str,
    target: MeetingQuestion,
    metric: str,
    source_by_id: dict[str, dict[str, Any]],
    attendees: list[str],
) -> list[ChartSpec]:
    speaker_keys = {
        normalize_speaker_name(source.get("speaker_name")).lower()
        for source in source_by_id.values()
    }
    speaker_keys.update(name.lower() for name in attendees)
    cutpoint_questions = [q for q in bundle.questions if is_cut_point(q)]
    question_by_id = {str(q.id): q for q in cutpoint_questions}

    mention_by_question: dict[str, list[tuple[str, list[str]]]] = {}
    for mention_set in plan.mentionSets:
        question_id = str(mention_set.questionId)
        if question_id not in question_by_id:
            continue
        seen: set[str] = set()
        entries: list[tuple[str, list[str]]] = []
        for item in mention_set.mentions:
            if not _mention_valid(
                item,
                source_by_id=source_by_id,
                speaker_keys=speaker_keys,
            ):
                continue
            speaker = normalize_speaker_name(item.speakerName).lower()
            if speaker in seen:
                continue
            seen.add(speaker)
            valid_ids = sorted(
                _normalize_source_ids(item.sourceIds).intersection(source_by_id.keys())
            )
            entries.append((item.speakerName, valid_ids))
        mention_by_question[question_id] = entries

    specs: list[ChartSpec] = []
    if chart_type == "bar":
        categories: list[dict[str, Any]] = []
        for question in cutpoint_questions:
            question_id = str(question.id)
            entries = mention_by_question.get(question_id, [])
            if not entries:
                continue
            evidence: list[dict[str, Any]] = []
            seen_evidence: set[str] = set()
            for speaker_name, source_ids in entries:
                for source_id in source_ids[:4]:
                    if source_id in seen_evidence:
                        continue
                    seen_evidence.add(source_id)
                    evidence.append(_evidence_for(source_id, source_by_id))
            if metric == "evidence_count":
                value = len(evidence)
            else:
                value = len(entries)
            categories.append(
                {
                    "key": question_id,
                    "label": question.content[:80],
                    "value": value,
                    "percentage": None,
                    "evidence": evidence,
                }
            )
        valid = len(categories) >= 1
        subtitle = (
            "统计口径：独立参会者覆盖数（按 speaker 去重，同一参会者多次提及只计 1 次）；"
            "数值由系统根据会议证据统计"
            if metric == "independent_speakers"
            else "统计口径：有效证据片段数量（发言证据数，非参会者人数）；数值由系统统计"
        )
        spec = {
            "id": "",
            "meeting_id": str(bundle.meeting.id),
            "type": "bar",
            "title": "各切点问题参会者关注覆盖度" if valid else "参会者覆盖度",
            "subtitle": subtitle,
            "metric": metric,
            "denominator": {"name": "本次会议全部有效参会者", "value": len(attendees)},
            "categories": categories,
            "validation": {
                "valid": valid,
                "generatedAt": datetime.now(timezone.utc).isoformat(),
                "reason": None if valid else "没有足够的可追溯证据生成条形图",
            },
        }
        if not valid:
            spec["subtitle"] = "当前数据不适合生成条形图：缺少可追溯的发言证据。"
    else:
        stance_by_speaker: dict[str, StanceItem] = {}
        for item in plan.stanceClassifications:
            speaker = normalize_speaker_name(item.speakerName).lower()
            if speaker not in speaker_keys:
                continue
            valid_ids = sorted(
                _normalize_source_ids(item.sourceIds).intersection(source_by_id.keys())
            )
            if item.stance in STANCE_WITH_EVIDENCE and not valid_ids:
                continue
            stance_by_speaker[speaker] = item

        category_counts: dict[str, int] = {key: 0 for key in STANCE_LABELS}
        evidence_by_stance: dict[str, list[dict[str, Any]]] = {
            key: [] for key in STANCE_LABELS
        }
        for name in attendees:
            key = name.lower()
            item = stance_by_speaker.get(key)
            stance = item.stance if item is not None else "NOT_MENTIONED"
            category_counts[stance] += 1
            for source_id in (
                sorted(_normalize_source_ids(item.sourceIds).intersection(source_by_id.keys()))
                if item is not None
                else []
            )[:3]:
                evidence_by_stance[stance].append(_evidence_for(source_id, source_by_id))

        categories = [
            {
                "key": key,
                "label": label,
                "value": category_counts[key],
                "percentage": (
                    round(category_counts[key] * 100 / max(len(attendees), 1), 1)
                ),
                "evidence": evidence_by_stance[key],
            }
            for key, label in STANCE_LABELS.items()
            if category_counts[key] > 0
        ]
        denominator_total = sum(category_counts.values())
        valid = (
            denominator_total == len(attendees)
            and len(attendees) >= 3
            and any(
                category_counts[key] > 0
                for key in ("SUPPORT", "CONDITIONAL_SUPPORT", "OPPOSE")
            )
        )
        if not valid:
            reason = (
                "当前数据不适合使用饼图展示，因为分类无法构成互斥的整体分布。"
                "建议改用条形图展示不同议题的参会者覆盖度。"
            )
        else:
            reason = None
        spec = {
            "id": "",
            "meeting_id": str(bundle.meeting.id),
            "type": "pie",
            "title": f"参会者立场分布：{target.content[:40]}",
            "subtitle": (
                "统计口径：统计分母为本次会议全部有效参会者；每位参会者仅归入一个类别；"
                "没有相关发言的参会者归入未表态；百分比由程序根据人数计算"
                if valid
                else (reason or "")
            ),
            "metric": "stance_distribution",
            "denominator": {"name": "本次会议全部有效参会者", "value": len(attendees)},
            "categories": categories,
            "validation": {
                "valid": valid,
                "generatedAt": datetime.now(timezone.utc).isoformat(),
                "reason": reason,
            },
        }

    validation = dict(spec["validation"])
    row = ChartSpec(
        meeting_id=bundle.meeting.id,
        organization_id=bundle.meeting.organization_id,
        analysis_version=bundle.analysis_version,
        chart_type=chart_type,
        target_id=target.id,
        target_label=target.content,
        title=spec["title"],
        subtitle=spec["subtitle"],
        metric=metric,
        spec=spec,
        valid=bool(validation.get("valid")),
        invalid_reason=validation.get("reason"),
    )
    session.add(row)
    await session.flush()
    row.spec["id"] = str(row.id)
    specs.append(row)
    return specs


async def get_latest_chart_spec(
    session: AsyncSession,
    *,
    meeting_id: UUID,
    analysis_version: int,
    chart_type: str,
    target_id: UUID | None,
) -> ChartSpec | None:
    return await session.scalar(
        select(ChartSpec)
        .where(
            ChartSpec.meeting_id == meeting_id,
            ChartSpec.analysis_version == analysis_version,
            ChartSpec.chart_type == chart_type,
            ChartSpec.target_id == target_id,
        )
        .order_by(ChartSpec.created_at.desc(), ChartSpec.id.desc())
    )


async def list_chart_specs(
    session: AsyncSession,
    *,
    meeting_id: UUID,
    analysis_version: int,
) -> list[ChartSpec]:
    return list(
        (
            await session.scalars(
                select(ChartSpec)
                .where(
                    ChartSpec.meeting_id == meeting_id,
                    ChartSpec.analysis_version == analysis_version,
                )
                .order_by(ChartSpec.created_at.desc(), ChartSpec.id.desc())
            )
        ).all()
    )


async def delete_chart_specs_for_plan(
    session: AsyncSession,
    *,
    meeting_id: UUID,
    analysis_version: int,
    chart_type: str,
    target_id: UUID | None,
) -> None:
    await session.execute(
        delete(ChartSpec).where(
            ChartSpec.meeting_id == meeting_id,
            ChartSpec.analysis_version == analysis_version,
            ChartSpec.chart_type == chart_type,
            ChartSpec.target_id == target_id,
        )
    )


def chart_spec_to_read(spec: ChartSpec) -> ChartSpecRead:
    data = dict(spec.spec or {})
    return ChartSpecRead(
        id=spec.id,
        meeting_id=spec.meeting_id,
        analysis_version=spec.analysis_version,
        type=data.get("type", spec.chart_type),
        title=data.get("title", spec.title),
        subtitle=data.get("subtitle", spec.subtitle),
        metric=data.get("metric", spec.metric),
        target_id=spec.target_id,
        target_label=spec.target_label,
        denominator=data.get("denominator"),
        categories=data.get("categories", []),
        validation=data.get("validation", {"valid": spec.valid}),
        generated_at=spec.created_at.isoformat(),
    )
