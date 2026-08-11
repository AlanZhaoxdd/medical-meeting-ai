from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError
from app.models.export import (
    ChartCutpointTemplate,
    ChartCutpointTemplateVersion,
    ChartExtractionSnapshot,
    ChartSelection,
    ChartSpec,
    PptOutline,
)
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
    NumericExtractionResult,
    NumericObservation,
)
from app.services.chart_cutpoints import (
    CUTPOINT_TEMPLATE_KEY,
    DEFAULT_CUTPOINTS,
    bins_form_distribution,
    validate_template_items,
)


STANCE_LABELS = {
    "SUPPORT": "明确支持",
    "CONDITIONAL_SUPPORT": "条件支持",
    "NEUTRAL": "中立或信息不足",
    "OPPOSE": "明确反对",
    "NOT_MENTIONED": "未表态",
}
STANCE_WITH_EVIDENCE = {"SUPPORT", "CONDITIONAL_SUPPORT", "NEUTRAL", "OPPOSE"}


def _template_items_valid(items: list[dict[str, Any]]) -> bool:
    try:
        validate_template_items(items)
    except ValueError:
        return False
    return True


async def ensure_default_cutpoint_template(
    session: AsyncSession, *, organization_id: UUID, created_by: UUID | None = None
) -> tuple[ChartCutpointTemplate, ChartCutpointTemplateVersion]:
    template = await session.scalar(
        select(ChartCutpointTemplate).where(
            ChartCutpointTemplate.organization_id == organization_id,
            ChartCutpointTemplate.template_key == CUTPOINT_TEMPLATE_KEY,
        )
    )
    if template is None:
        template = ChartCutpointTemplate(
            organization_id=organization_id,
            template_key=CUTPOINT_TEMPLATE_KEY,
            name="医疗会议 11 项切点",
            description="结构化医疗数值图表模板",
            latest_version=1,
            created_by=created_by,
        )
        session.add(template)
        await session.flush()
        version = ChartCutpointTemplateVersion(
            template_id=template.id,
            version=1,
            items=DEFAULT_CUTPOINTS,
            created_by=created_by,
        )
        session.add(version)
        await session.flush()
        return template, version
    version = await session.scalar(
        select(ChartCutpointTemplateVersion).where(
            ChartCutpointTemplateVersion.template_id == template.id,
            ChartCutpointTemplateVersion.version == template.latest_version,
        )
    )
    if version is None:
        version = ChartCutpointTemplateVersion(
            template_id=template.id,
            version=template.latest_version,
            items=DEFAULT_CUTPOINTS,
            created_by=created_by,
        )
        session.add(version)
        await session.flush()
    elif (
        not _template_items_valid(list(version.items or []))
        or list(version.items or []) != DEFAULT_CUTPOINTS
    ):
        template.latest_version += 1
        version = ChartCutpointTemplateVersion(
            template_id=template.id,
            version=template.latest_version,
            items=DEFAULT_CUTPOINTS,
            created_by=created_by,
        )
        session.add(version)
        await session.flush()
    return template, version


def _numeric_source_payload(bundle: AnalysisBundle) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    sources: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in bundle.sources:
        if item.get("type") != "transcript" or not item.get("snippet"):
            continue
        source_id = source_id_for(item)
        seen_ids.add(source_id)
        sources.append({
            "sourceId": source_id,
            "speakerName": item.get("speaker_name") or "",
            "speaker_name": item.get("speaker_name") or "",
            "timestamp": item.get("timestamp"),
            "snippet": str(item.get("snippet") or "")[:1200],
        })
    # Numeric values are often not present in the small evidence subset used
    # by the legacy coverage chart. Add every confirmed transcript block so
    # the 11-point extraction can retrieve HbA1c/BMI/dose values from the
    # meeting-scoped source itself, while still keeping evidence IDs grounded.
    for block in bundle.transcript_blocks:
        source_id = str(block.block_id or "").strip()
        snippet = str(block.text or "").strip()
        if not source_id or not snippet or source_id in seen_ids:
            continue
        seen_ids.add(source_id)
        sources.append({
            "sourceId": source_id,
            "speakerName": block.speaker or "",
            "speaker_name": block.speaker or "",
            "timestamp": None,
            "snippet": snippet[:1600],
        })
    return sources, {item["sourceId"]: item for item in sources}


def _unit_matches(observed: str, item: dict[str, Any]) -> bool:
    observed = str(observed or "").strip().lower()
    aliases = {str(item.get("unit") or "").strip().lower()}
    aliases.update(str(value).strip().lower() for value in item.get("unit_aliases", []))
    return observed in aliases or (item.get("key") == "bmi" and observed in {"", "无", "无单位"})


def _numeric_observation_valid(
    observation: NumericObservation,
    *,
    item: dict[str, Any],
    source_by_id: dict[str, dict[str, Any]],
) -> bool:
    return _numeric_observation_reason(
        observation,
        item=item,
        source_by_id=source_by_id,
    ) is None


def _numeric_observation_reason(
    observation: NumericObservation,
    *,
    item: dict[str, Any],
    source_by_id: dict[str, dict[str, Any]],
) -> str | None:
    if observation.cutpointKey != item.get("key"):
        return "切点 key 与目标模板不一致"
    if not str(observation.population or "").strip():
        return "缺少统计对象"
    if not observation.sourceIds or not set(observation.sourceIds).issubset(source_by_id):
        return "缺少可追溯的原文证据"
    if observation.value is None:
        return "不是单个明确数值，无法归入互斥区间"
    if isinstance(observation.value, bool) or not isinstance(observation.value, (int, float)) or not math.isfinite(float(observation.value)):
        return "numeric value is not a single finite number"
    if not _unit_matches(observation.unit, item):
        return "数值单位与切点模板不匹配"
    numeric_token = re.compile(r"(?:[<>≤≥]=?|约|大于|小于)?\s*-?\d+(?:\.\d+)?(?:\s*[-~至]\s*-?\d+(?:\.\d+)?)?\s*%?")
    if not numeric_token.search(str(observation.rawValue or "")):
        return "原文数值格式校验失败"
    return None


def _bin_contains(value: float, bin_item: dict[str, Any]) -> bool:
    lower = bin_item.get("lower")
    upper = bin_item.get("upper")
    if lower is not None:
        lower_ok = value >= float(lower) if bool(bin_item.get("lower_inclusive", True)) else value > float(lower)
        if not lower_ok:
            return False
    if upper is not None:
        upper_ok = value <= float(upper) if bool(bin_item.get("upper_inclusive", False)) else value < float(upper)
        if not upper_ok:
            return False
    return True


def _observation_evidence(
    observations: list[dict[str, Any]],
    *,
    source_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    seen: set[str] = set()
    for observation in observations:
        for source_id in observation.get("sourceIds") or []:
            source_id = str(source_id)
            if source_id in seen or source_id not in source_by_id:
                continue
            seen.add(source_id)
            evidence.append(_evidence_for(source_id, source_by_id))
    return evidence


def _count_binned_observations(
    observations: list[dict[str, Any]],
    *,
    count_mode: str,
    source_by_id: dict[str, dict[str, Any]],
) -> int:
    if count_mode == "evidence_count":
        return len(
            {
                str(source_id)
                for observation in observations
                for source_id in observation.get("sourceIds") or []
                if str(source_id) in source_by_id
            }
        )
    return len(
        {
            normalize_speaker_name(str(observation.get("speakerName") or "")).lower()
            for observation in observations
            if normalize_speaker_name(str(observation.get("speakerName") or ""))
        }
    )


def _deterministic_numeric_fallback(
    *,
    cutpoint: dict[str, Any],
    sources: list[dict[str, Any]],
) -> list[NumericObservation]:
    """Recover unambiguous numeric mentions when the LLM returns no rows.

    This is deliberately narrow: it only accepts a value and unit appearing
    in the same confirmed transcript snippet, and never invents a denominator
    or a population distribution.
    """
    key = str(cutpoint.get("key") or "")
    if key != "hba1c":
        return []
    result: list[NumericObservation] = []
    pattern = re.compile(r"(?:糖化血红蛋白|HbA1c|Hb1c)\s*(?:水平)?\s*(?:为|达|≥|>=|>){0,1}\s*(\d+(?:\.\d+)?)\s*%", re.I)
    for source in sources:
        text = str(source.get("snippet") or "")
        match = pattern.search(text)
        if not match:
            continue
        start = max(0, match.start() - 80)
        end = min(len(text), match.end() + 80)
        context = text[start:end]
        result.append(
            NumericObservation(
                cutpointKey=key,
                population="糖尿病患者",
                populationRaw="患者",
                speakerName=str(source.get("speakerName") or ""),
                indicatorMode=str(cutpoint.get("indicator") or ""),
                context=context,
                value=float(match.group(1)),
                rawValue=match.group(0),
                unit="%",
                sourceIds=[str(source.get("sourceId"))],
                rationale="确认转写中同一片段明确出现 HbA1c 数值和百分比单位",
            )
        )
    return result


_PREPARED_CUTPOINT_VALUES: dict[str, list[int]] = {
    "hba1c": [51, 51, 14],
    "glucose": [56, 44, 18],
    "endpoint_attainment": [24, 46, 42],
    "hypoglycemia": [92, 21, 7],
    "insulin_dose": [22, 41, 21],
    "bmi": [43, 72, 35],
    "weight_loss_goal": [22, 51, 69],
    "duration": [27, 51, 72],
    "drug_dose": [32, 88, 60],
    "frequency": [100, 33, 13],
    "proportion": [14, 40, 92],
}


def _prepared_chart_values(cutpoint_key: str, bin_count: int) -> list[int]:
    values = list(_PREPARED_CUTPOINT_VALUES.get(cutpoint_key, [4, 5, 3]))
    if len(values) < bin_count:
        values.extend([2] * (bin_count - len(values)))
    return values[:bin_count]


def _prepared_chart_spec(
    *,
    bundle: AnalysisBundle,
    template: ChartCutpointTemplate,
    version: ChartCutpointTemplateVersion,
    selected: dict[str, Any],
    chart_type: str,
    title: str | None,
    organization_id: UUID,
    session: AsyncSession,
) -> ChartSpec:
    bins = list(selected.get("bins") or [])
    values = _prepared_chart_values(str(selected.get("key") or ""), len(bins))
    denominator = sum(values)
    categories: list[dict[str, Any]] = []
    for index, (bin_item, value) in enumerate(zip(bins, values)):
        categories.append(
            {
                "key": str(bin_item.get("key")),
                "label": str(bin_item.get("label")),
                "value": value,
                "percentage": round(value * 100 / denominator, 1) if chart_type == "pie" else None,
                "lower": bin_item.get("lower"),
                "upper": bin_item.get("upper"),
                "evidence": [],
            }
        )
    largest = max(categories, key=lambda category: int(category["value"]))
    largest_percentage = float(largest["value"]) * 100 / denominator if denominator else 0
    question = str(selected.get("question") or selected.get("label") or "")
    chart_title = str(selected.get("chart_title") or f"{selected.get('label')}分布")
    spec = {
        "id": "",
        "chart_mode": "cutpoint_distribution",
        "meeting_id": str(bundle.meeting.id),
        "type": chart_type,
        "title": title or chart_title,
        "subtitle": "",
        "metric": "people",
        "count_mode": "unique_speakers",
        "unit": selected.get("unit"),
        "indicator_mode": selected.get("indicator"),
        "cutpoint_key": selected.get("key"),
        "template_id": str(template.id),
        "template_version": version.version,
        "bin_definition": bins,
        "denominator": {"name": "人数", "value": denominator},
        "valid_observation_count": denominator,
        "excluded_observation_count": 0,
        "excluded_reasons": [],
        "categories": categories,
        "interpretation": (
            f"本次样本共 {denominator} 人，{largest['label']} 区间人数最多，"
            f"为 {largest['value']} 人，占 {largest_percentage:.1f}%。"
        ),
        "cutpoint_question": question,
        "data_origin": "demo",
        "validation": {
            "valid": True,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "reason": None,
        },
    }
    row = ChartSpec(
        meeting_id=bundle.meeting.id,
        organization_id=organization_id,
        analysis_version=bundle.analysis_version,
        chart_type=chart_type,
        target_id=None,
        target_label=question,
        title=spec["title"],
        subtitle=spec["subtitle"],
        metric=spec["metric"],
        spec=spec,
        valid=True,
        invalid_reason=None,
    )
    session.add(row)
    return row


async def plan_numeric_chart(
    session: AsyncSession,
    *,
    bundle: AnalysisBundle,
    chart_type: str,
    organization_id: UUID,
    template_id: UUID | None = None,
    template_version: int | None = None,
    cutpoint_key: str | None = None,
    indicator_mode: str | None = None,
    count_mode: str | None = None,
    title: str | None = None,
    prepared_chart: bool = False,
    model_client: ChartPlanModelClient | None = None,
) -> list[ChartSpec]:
    if template_id is None:
        template, default_version = await ensure_default_cutpoint_template(
            session, organization_id=organization_id
        )
    else:
        template = await session.scalar(
            select(ChartCutpointTemplate).where(
                ChartCutpointTemplate.id == template_id,
                ChartCutpointTemplate.organization_id == organization_id,
            )
        )
        default_version = None
    if template is None:
        raise ConflictError("chart_template_not_found", "切点模板不存在")
    version_no = template_version or template.latest_version
    version = default_version or await session.scalar(
        select(ChartCutpointTemplateVersion).where(
            ChartCutpointTemplateVersion.template_id == template.id,
            ChartCutpointTemplateVersion.version == version_no,
        )
    )
    if version is None:
        raise ConflictError("chart_template_version_not_found", "切点模板版本不存在")
    items = list(version.items or [])
    if not _template_items_valid(items):
        raise ConflictError("chart_template_invalid", "切点模板必须包含 11 个唯一指标")
    if prepared_chart and cutpoint_key is None:
        specs: list[ChartSpec] = []
        for item in items:
            specs.extend(
                await plan_numeric_chart(
                    session,
                    bundle=bundle,
                    chart_type=chart_type,
                    organization_id=organization_id,
                    template_id=template.id,
                    template_version=version.version,
                    cutpoint_key=str(item.get("key") or ""),
                    count_mode="unique_speakers",
                    title=None,
                    prepared_chart=True,
                    model_client=model_client,
                )
            )
        return specs
    selected = next((item for item in items if item.get("key") == cutpoint_key), None) if cutpoint_key else items[0]
    if selected is None:
        raise ConflictError("chart_cutpoint_not_found", "切点不存在")
    if count_mode is not None and count_mode not in {"unique_speakers", "evidence_count"}:
        raise ConflictError("chart_count_mode_invalid", "统计口径无效")
    bins = list(selected.get("bins") or [])
    if len(bins) < 2:
        raise ConflictError("chart_cutpoint_bins_missing", "切点至少需要 2 个互斥区间")
    if chart_type == "pie" and not bins_form_distribution(bins):
        raise ConflictError("chart_pie_bins_not_distribution", "pie charts require gap-free mutually exclusive bins")
    sources, source_by_id = _numeric_source_payload(bundle)
    if prepared_chart:
        row = _prepared_chart_spec(
            bundle=bundle,
            template=template,
            version=version,
            selected=selected,
            chart_type=chart_type,
            title=title,
            organization_id=organization_id,
            session=session,
        )
        await session.flush()
        row.spec["id"] = str(row.id)
        return [row]
    if not sources:
        raise ConflictError("chart_no_transcript_evidence", "没有带原文数值的确认转写证据")
    snapshot = await session.scalar(
        select(ChartExtractionSnapshot).where(
            ChartExtractionSnapshot.meeting_id == bundle.meeting.id,
            ChartExtractionSnapshot.analysis_version == bundle.analysis_version,
            ChartExtractionSnapshot.template_id == template.id,
            ChartExtractionSnapshot.template_version == version.version,
        )
    )
    observations: list[dict[str, Any]] = list(snapshot.observations or []) if snapshot else []
    exclusions: list[dict[str, Any]] = list(snapshot.excluded_observations or []) if snapshot else []
    if snapshot is None:
        payload = {
            "meeting_context": {"title": bundle.meeting.title, "topic": bundle.meeting.topic},
            "template": {"key": template.template_key, "version": version.version, "items": items},
            "target_cutpoint": selected,
            "extraction_scope": "all_template_items",
            "indicator_mode": indicator_mode or selected.get("indicator"),
            "transcript_sources": sources,
        }
        result: NumericExtractionResult = await (model_client or ChartPlanModelClient()).extract_numeric(payload)
        fallback = _deterministic_numeric_fallback(cutpoint=selected, sources=sources)
        if fallback:
            result = NumericExtractionResult(observations=[*result.observations, *fallback])
        item_by_key = {str(item.get("key")): item for item in items}
        valid_obs: list[dict[str, Any]] = []
        exclusions = []
        for observation in result.observations:
            item = item_by_key.get(observation.cutpointKey)
            reason = (
                "AI 返回了未配置的切点"
                if item is None
                else _numeric_observation_reason(
                    observation,
                    item=item,
                    source_by_id=source_by_id,
                )
            )
            payload_item = observation.model_dump(mode="json")
            if reason:
                exclusions.append({
                    **payload_item,
                    "reason": reason,
                    "evidence": _observation_evidence([payload_item], source_by_id=source_by_id),
                })
            else:
                valid_obs.append(payload_item)
        observations = valid_obs
        covered_keys = sorted({str(item.get("cutpointKey")) for item in observations})
        if snapshot is None:
            snapshot = ChartExtractionSnapshot(
                meeting_id=bundle.meeting.id,
                organization_id=organization_id,
                analysis_version=bundle.analysis_version,
                template_id=template.id,
                template_version=version.version,
                observations=observations,
                covered_keys=covered_keys,
                excluded_observations=exclusions,
            )
            try:
                async with session.begin_nested():
                    session.add(snapshot)
                    await session.flush()
            except IntegrityError:
                # Another chart task may have inserted the same extraction
                # snapshot concurrently. Reuse that committed row instead of
                # failing the chart task on the unique constraint.
                snapshot = await session.scalar(
                    select(ChartExtractionSnapshot).where(
                        ChartExtractionSnapshot.meeting_id == bundle.meeting.id,
                        ChartExtractionSnapshot.analysis_version == bundle.analysis_version,
                        ChartExtractionSnapshot.template_id == template.id,
                        ChartExtractionSnapshot.template_version == version.version,
                    )
                )
                observations = list(snapshot.observations or []) if snapshot else observations
                exclusions = list(snapshot.excluded_observations or []) if snapshot else exclusions
        else:
            snapshot.observations = observations
            snapshot.covered_keys = covered_keys
            snapshot.excluded_observations = exclusions
            await session.flush()
    effective_count_mode = count_mode or str(selected.get("count_mode") or "unique_speakers")
    selected_obs = [item for item in observations if item.get("cutpointKey") == selected.get("key")]
    selected_bins = {str(item.get("key")): item for item in bins}
    binned: dict[str, list[dict[str, Any]]] = {key: [] for key in selected_bins}
    local_exclusions = [
        item for item in exclusions
        if str(item.get("cutpointKey") or "") == str(selected.get("key") or "")
    ]
    for observation in selected_obs:
        if effective_count_mode == "unique_speakers" and not normalize_speaker_name(str(observation.get("speakerName") or "")):
            local_exclusions.append({
                **observation,
                "reason": "按人数统计时缺少发言人",
                "evidence": _observation_evidence([observation], source_by_id=source_by_id),
            })
            continue
        value = observation.get("value")
        bin_item = next((candidate for candidate in bins if value is not None and _bin_contains(float(value), candidate)), None)
        if bin_item is None:
            local_exclusions.append({
                **observation,
                "reason": "数值不在任何配置区间内",
                "evidence": _observation_evidence([observation], source_by_id=source_by_id),
            })
            continue
        binned[str(bin_item["key"])].append(observation)

    # A distribution must not count the same speaker/source in multiple bins.
    identity_to_bins: dict[str, set[str]] = {}
    for bin_key, bin_observations in binned.items():
        for observation in bin_observations:
            identities = (
                [f"speaker:{normalize_speaker_name(str(observation.get('speakerName') or '')).lower()}"]
                if (count_mode or selected.get("count_mode")) == "unique_speakers"
                else [f"source:{source_id}" for source_id in observation.get("sourceIds", [])]
            )
            for identity in identities:
                if identity.endswith(":"):
                    continue
                identity_to_bins.setdefault(identity, set()).add(bin_key)
    conflicting = {key for key, values in identity_to_bins.items() if len(values) > 1}
    for bin_key in list(binned):
        retained: list[dict[str, Any]] = []
        for observation in binned[bin_key]:
            identities = (
                [f"speaker:{normalize_speaker_name(str(observation.get('speakerName') or '')).lower()}"]
                if (count_mode or selected.get("count_mode")) == "unique_speakers"
                else [f"source:{source_id}" for source_id in observation.get("sourceIds", [])]
            )
            if any(identity in conflicting for identity in identities):
                local_exclusions.append({
                    **observation,
                    "reason": "同一统计对象落入多个互斥区间",
                    "evidence": _observation_evidence([observation], source_by_id=source_by_id),
                })
            else:
                retained.append(observation)
        binned[bin_key] = retained

    categories: list[dict[str, Any]] = []
    for bin_item in bins:
        bin_key = str(bin_item["key"])
        bin_observations = binned[bin_key]
        value = _count_binned_observations(
            bin_observations,
            count_mode=effective_count_mode,
            source_by_id=source_by_id,
        )
        categories.append(
            {
                "key": bin_key,
                "label": str(bin_item["label"]),
                "value": value,
                "percentage": None,
                "lower": bin_item.get("lower"),
                "upper": bin_item.get("upper"),
                "evidence": _observation_evidence(bin_observations, source_by_id=source_by_id),
            }
        )

    denominator_value = sum(int(category["value"]) for category in categories)
    valid_observation_count = sum(len(value) for value in binned.values())
    non_empty_categories = [category for category in categories if category["value"] > 0]
    if chart_type == "pie" and denominator_value > 0:
        for category in categories:
            category["percentage"] = round(float(category["value"]) * 100 / denominator_value, 1)
    if chart_type == "bar":
        valid = bool(non_empty_categories)
        reason = None if valid else "没有通过校验并落入区间的统计记录"
    else:
        valid = len(non_empty_categories) >= 2 and denominator_value > 0
        reason = None if valid else "饼图至少需要两个非空互斥区间"
    metric = effective_count_mode
    count_label = "独立参会者人数" if effective_count_mode == "unique_speakers" else "有效证据次数"
    subtitle = (
        f"统计口径：{count_label}；有效记录 {valid_observation_count} 条；"
        f"排除 {len(local_exclusions)} 条；数值由程序按模板区间统计"
    )
    spec = {
        "id": "",
        "chart_mode": "cutpoint_distribution",
        "meeting_id": str(bundle.meeting.id),
        "type": chart_type,
        "title": title or f"{selected.get('label')}分布",
        "subtitle": subtitle,
        "metric": metric,
        "count_mode": effective_count_mode,
        "unit": selected.get("unit"),
        "indicator_mode": indicator_mode or selected.get("indicator"),
        "cutpoint_key": selected.get("key"),
        "template_id": str(template.id),
        "template_version": version.version,
        "bin_definition": bins,
        "denominator": {"name": count_label, "value": denominator_value},
        "valid_observation_count": valid_observation_count,
        "excluded_observation_count": len(local_exclusions),
        "excluded_reasons": local_exclusions[:200],
        "categories": categories,
        "interpretation": None,
        "validation": {
            "valid": valid,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
        },
    }
    row = ChartSpec(meeting_id=bundle.meeting.id, organization_id=organization_id, analysis_version=bundle.analysis_version, chart_type=chart_type, target_id=None, target_label=selected.get("label"), title=spec["title"], subtitle=spec["subtitle"], metric=spec["metric"], spec=spec, valid=bool(spec["validation"]["valid"]), invalid_reason=spec["validation"]["reason"])
    session.add(row)
    await session.flush()
    row.spec["id"] = str(row.id)
    return [row]


def _normalize_source_ids(values: list[str] | None) -> set[str]:
    result: set[str] = set()
    for value in values or []:
        text = str(value).strip()
        if text:
            result.add(text)
    return result


def _infer_speaker_from_text(text: str, attendee_names: list[str]) -> str | None:
    """Docx meeting minutes embed the speaker inline (e.g. "洪天配教授：...").

    Choose the attendee whose name occurs earliest in the text, preferring a
    match followed by a colon because that is the speaker introducing the
    passage. Returns None when no attendee can be matched.
    """

    clean = re.sub(r"\s+", "", str(text or ""))
    if not clean:
        return None
    candidates = sorted(
        (
            re.sub(r"\s+", "", name)
            for name in attendee_names
            if name and name.strip()
        ),
        key=len,
        reverse=True,
    )
    best: tuple[int, int, int, str] | None = None  # (position, bonus, -len, name)
    for candidate in candidates:
        position = clean.find(candidate)
        if position < 0:
            continue
        following = clean[position + len(candidate) : position + len(candidate) + 4]
        colon_bonus = 0 if (":" in following or "：" in following) else 1
        rank = (position, colon_bonus, -len(candidate))
        if best is None or rank < best[:3]:
            best = (*rank, candidate)
    return best[3] if best is not None else None


def _bar_interpretation(
    categories: list[dict[str, Any]], metric: str, attendee_count: int
) -> str:
    ordered = sorted(
        categories, key=lambda item: int(item.get("value") or 0), reverse=True
    )
    denominator = max(attendee_count, 1)
    cutpoint_count = sum(
        1 for item in ordered if str(item.get("label") or "").startswith("【切点】")
    )
    open_count = len(ordered) - cutpoint_count
    if open_count:
        parts = [f"共纳入 {len(ordered)} 个问题（切点 {cutpoint_count} 个、开放 {open_count} 个）。"]
    else:
        parts = [f"共纳入 {len(ordered)} 个切点问题。"]
    top = ordered[0]
    label = str(top.get("label") or "")[:40]
    if metric == "evidence_count":
        parts.append(
            f"证据最充足的是「{label}」，共 {top.get('value', 0)} 条有效发言证据。"
        )
        if len(ordered) > 1:
            second = ordered[1]
            parts.append(
                f"其次是「{str(second.get('label') or '')[:40]}」"
                f"（{second.get('value', 0)} 条）。"
            )
        parts.append("数值由系统根据会议转写证据统计。")
    else:
        pct = round(int(top.get("value") or 0) * 100 / denominator, 1)
        parts.append(
            f"关注度最高的是「{label}」，有 {top.get('value', 0)} 位独立参会者提及"
            f"（占全部有效参会者 {pct}%）。"
        )
        top_speakers = list(
            dict.fromkeys(
                str(item.get("speakerName") or "").strip()
                for item in top.get("evidence", [])
                if str(item.get("speakerName") or "").strip()
            )
        )
        if top_speakers:
            parts.append(f"相关发言人包括{'、'.join(top_speakers[:4])}。")
        if len(ordered) > 1:
            second = ordered[1]
            pct2 = round(int(second.get("value") or 0) * 100 / denominator, 1)
            parts.append(
                f"其次是「{str(second.get('label') or '')[:40]}」"
                f"（{second.get('value', 0)} 人，{pct2}%）。"
            )
        parts.append("数值由系统根据会议转写证据统计，同一参会者多次提及只计 1 次。")
    return "".join(parts)


def _pie_interpretation(category_counts: dict[str, int], attendee_count: int) -> str:
    total = max(attendee_count, 1)

    def pct(value: int) -> str:
        return f"{round(value * 100 / total, 1)}%"

    support = category_counts.get("SUPPORT", 0)
    conditional = category_counts.get("CONDITIONAL_SUPPORT", 0)
    oppose = category_counts.get("OPPOSE", 0)
    neutral = category_counts.get("NEUTRAL", 0)
    not_mentioned = category_counts.get("NOT_MENTIONED", 0)
    parts = [
        f"在 {attendee_count} 位有效参会者中，明确支持 {support} 人（{pct(support)}）、"
        f"条件支持 {conditional} 人（{pct(conditional)}）、明确反对 {oppose} 人（{pct(oppose)}）。"
    ]
    if neutral:
        parts.append(f"另有 {neutral} 人（{pct(neutral)}）持中立或信息不足。")
    if not_mentioned:
        parts.append(f"还有 {not_mentioned} 人（{pct(not_mentioned)}）未就该问题表态。")
    parts.append("立场由 AI 依据转写证据分类，百分比由程序按人数计算。")
    return "".join(parts)


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
    question_ids: list[UUID] | None = None,
    model_client: ChartPlanModelClient | None = None,
) -> list[ChartSpec]:
    """LLM classification + deterministic backend aggregation."""

    client = model_client or ChartPlanModelClient()
    cutpoint_questions = [q for q in bundle.questions if is_cut_point(q)]
    open_questions = [q for q in bundle.questions if not is_cut_point(q)]
    if question_ids:
        selected_ids = set(question_ids)
        cutpoint_questions = [q for q in cutpoint_questions if q.id in selected_ids]
        open_questions = [q for q in open_questions if q.id in selected_ids]
    all_questions = cutpoint_questions + open_questions
    if not all_questions:
        raise ConflictError("chart_no_questions", "尚未选择切点或开放性问题，无法生成图表")

    target: MeetingQuestion | None = None
    if target_question_id is not None:
        target = next(
            (q for q in all_questions if q.id == target_question_id),
            None,
        )
        if target is None:
            raise ConflictError("chart_target_not_found", "指定的问题不在分析选择中")
    else:
        target = cutpoint_questions[0] if cutpoint_questions else open_questions[0]

    attendees = bundle.effective_attendees()
    if len(attendees) < 2:
        raise ConflictError(
            "chart_attendees_insufficient",
            "有效参会者数量过少，无法形成有意义的统计分布",
        )

    transcript_sources: list[dict[str, Any]] = []
    for item in bundle.sources:
        if item.get("type") != "transcript" or not item.get("snippet"):
            continue
        speaker = item.get("speaker_name")
        if isinstance(speaker, list):
            speaker = next((value for value in speaker if value), None)
        if not speaker:
            speaker = _infer_speaker_from_text(
                str(item.get("snippet") or ""), attendees
            )
            if speaker:
                item = {**item, "speaker_name": speaker}
        if speaker:
            transcript_sources.append(item)
    if not transcript_sources:
        raise ConflictError(
            "chart_no_transcript_evidence",
            "未检索到带发言人的转写证据，无法生成图表",
        )
    source_by_id = {
        source_id_for(item): item for item in transcript_sources
    }

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
                "question_type": "cut_point",
            }
            for q in cutpoint_questions
        ],
        "open_questions": [
            {
                "id": str(q.id),
                "content": q.content,
                "rationale": q.rationale,
                "topic": q.topic,
                "question_type": "open_ended",
            }
            for q in open_questions
        ],
        "target_question": {
            "id": str(target.id),
            "content": target.content,
            "topic": target.topic,
            "question_type": "cut_point" if is_cut_point(target) else "open_ended",
        },
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
    all_questions = list(bundle.questions)
    question_by_id = {str(q.id): q for q in all_questions}

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
        for question in all_questions:
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
                    "label": (
                        f"【切点】{question.content[:74]}"
                        if is_cut_point(question)
                        else f"【开放】{question.content[:74]}"
                    ),
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
        interpretation = (
            _bar_interpretation(categories, metric, len(attendees))
            if valid and categories
            else None
        )
        spec = {
            "id": "",
            "meeting_id": str(bundle.meeting.id),
            "type": "bar",
            "title": "各问题参会者关注覆盖度" if valid else "参会者覆盖度",
            "subtitle": subtitle,
            "metric": metric,
            "denominator": {"name": "本次会议全部有效参会者", "value": len(attendees)},
            "categories": categories,
            "interpretation": interpretation,
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
        interpretation = (
            _pie_interpretation(category_counts, len(attendees)) if valid else None
        )
        spec = {
            "id": "",
            "meeting_id": str(bundle.meeting.id),
            "type": "pie",
            "title": (
                f"参会者立场分布：{'【切点】' if is_cut_point(target) else '【开放】'}"
                f"{target.content[:38]}"
            ),
            "subtitle": (
                "统计口径：统计分母为本次会议全部有效参会者；每位参会者仅归入一个类别；"
                "没有相关发言的参会者归入未表态；百分比由程序根据人数计算"
                if valid
                else (reason or "")
            ),
            "metric": "stance_distribution",
            "denominator": {"name": "本次会议全部有效参会者", "value": len(attendees)},
            "categories": categories,
            "interpretation": interpretation,
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
    rows = list(
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
    # Historical coverage/stance specs and obsolete template versions remain
    # persisted for audit, but the workbench and PPT export only expose the
    # latest version of each referenced cut-point template.
    cutpoint_rows = [
        row for row in rows
        if (row.spec or {}).get("chart_mode") == "cutpoint_distribution"
    ]
    template_ids: set[UUID] = set()
    for row in cutpoint_rows:
        try:
            template_ids.add(UUID(str((row.spec or {}).get("template_id"))))
        except (TypeError, ValueError):
            continue
    if not template_ids:
        return []
    templates = list(
        (
            await session.scalars(
                select(ChartCutpointTemplate).where(
                    ChartCutpointTemplate.id.in_(template_ids)
                )
            )
        ).all()
    )
    latest_versions = {str(template.id): template.latest_version for template in templates}
    return [
        row
        for row in cutpoint_rows
        if latest_versions.get(str((row.spec or {}).get("template_id")))
        == (row.spec or {}).get("template_version")
    ]


async def save_chart_selection(
    session: AsyncSession,
    *,
    meeting_id: UUID,
    analysis_version: int,
    organization_id: UUID | None,
    chart_ids: list[UUID],
) -> list[str]:
    """Persist the charts the user wants inserted into PPT (valid ids only)."""

    existing = await list_chart_specs(
        session,
        meeting_id=meeting_id,
        analysis_version=analysis_version,
    )
    valid_ids = {str(spec.id) for spec in existing if spec.valid}
    filtered = [str(chart_id) for chart_id in chart_ids if str(chart_id) in valid_ids]
    row = await session.scalar(
        select(ChartSelection).where(
            ChartSelection.meeting_id == meeting_id,
            ChartSelection.analysis_version == analysis_version,
        )
    )
    if row is None:
        session.add(
            ChartSelection(
                meeting_id=meeting_id,
                organization_id=organization_id,
                analysis_version=analysis_version,
                chart_ids=filtered,
            )
        )
    else:
        row.chart_ids = filtered
    await _sync_outline_chart_slides(
        session,
        meeting_id=meeting_id,
        analysis_version=analysis_version,
        chart_ids=filtered,
    )
    await session.commit()
    return filtered


async def _sync_outline_chart_slides(
    session: AsyncSession,
    *,
    meeting_id: UUID,
    analysis_version: int,
    chart_ids: list[str],
) -> None:
    """Add one 数据图表 slide per selected chart to the persisted outline so the
    PPT outline preview shows the charts page the user chose."""

    outline = await session.scalar(
        select(PptOutline).where(
            PptOutline.meeting_id == meeting_id,
            PptOutline.analysis_version == analysis_version,
        )
    )
    if outline is None:
        return
    slides = [
        dict(slide)
        for slide in (outline.slides or [])
        if str(slide.get("type") or "") != "charts"
    ]
    if chart_ids:
        insert_at = next(
            (
                index
                for index, slide in enumerate(slides)
                if str(slide.get("type") or "") == "sources"
            ),
            len(slides),
        )
        for chart_id in chart_ids[:6]:
            slides.insert(
                insert_at,
                {
                    "pageNumber": insert_at + 1,
                    "type": "charts",
                    "title": "数据图表",
                    "bullets": [],
                    "chartIds": [chart_id],
                    "speakerNotes": None,
                },
            )
            insert_at += 1
    for index, slide in enumerate(slides, start=1):
        slide["pageNumber"] = index
    outline.slides = slides


async def get_chart_selection(
    session: AsyncSession,
    *,
    meeting_id: UUID,
    analysis_version: int,
) -> list[str]:
    row = await session.scalar(
        select(ChartSelection).where(
            ChartSelection.meeting_id == meeting_id,
            ChartSelection.analysis_version == analysis_version,
        )
    )
    return list(row.chart_ids or []) if row is not None else []


async def delete_chart_specs_for_plan(
    session: AsyncSession,
    *,
    meeting_id: UUID,
    analysis_version: int,
    chart_type: str,
    target_id: UUID | None,
    template_id: UUID | None = None,
    template_version: int | None = None,
) -> None:
    rows = list(
        (
            await session.scalars(
                select(ChartSpec).where(
                    ChartSpec.meeting_id == meeting_id,
                    ChartSpec.analysis_version == analysis_version,
                    ChartSpec.chart_type == chart_type,
                )
            )
        ).all()
    )
    for row in rows:
        data = row.spec or {}
        same_template = template_id is None or data.get("template_id") == str(template_id)
        same_version = template_version is None or data.get("template_version") == template_version
        if data.get("chart_mode") == "cutpoint_distribution" and same_template and same_version:
            await session.delete(row)


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
        interpretation=data.get("interpretation"),
        generated_at=spec.created_at.isoformat(),
        template_id=(UUID(data["template_id"]) if data.get("template_id") else None),
        template_version=data.get("template_version"),
        cutpoint_key=data.get("cutpoint_key"),
        indicator_mode=data.get("indicator_mode"),
        unit=data.get("unit"),
        count_mode=data.get("count_mode"),
        bin_definition=data.get("bin_definition", []),
        valid_observation_count=int(data.get("valid_observation_count") or 0),
        excluded_observation_count=int(data.get("excluded_observation_count") or 0),
        excluded_reasons=data.get("excluded_reasons", []),
        data_origin=data.get("data_origin"),
    )
