from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services.chart_cutpoints import (
    DEFAULT_CUTPOINTS,
    bins_form_distribution,
    validate_template_items,
)
from app.services.export_chart_service import (
    _bin_contains,
    _count_binned_observations,
    ensure_default_cutpoint_template,
    list_chart_specs,
    _prepared_chart_spec,
    _prepared_chart_values,
)
from app.services.export_charts import render_chart_svg


def test_default_chart_template_has_the_eleven_contract_items() -> None:
    assert len(DEFAULT_CUTPOINTS) == 11
    assert [item["key"] for item in DEFAULT_CUTPOINTS] == [
        "hba1c",
        "glucose",
        "endpoint_attainment",
        "hypoglycemia",
        "insulin_dose",
        "bmi",
        "weight_loss_goal",
        "duration",
        "drug_dose",
        "frequency",
        "proportion",
    ]
    assert [item["unit"] for item in DEFAULT_CUTPOINTS] == [
        "%",
        "mmol/L",
        "%",
        "次/人年",
        "U/日",
        "kg/m²",
        "%",
        "月",
        "mg",
        "次",
        "%",
    ]
    assert all(item["question"].endswith("？") for item in DEFAULT_CUTPOINTS)
    assert all(item["chart_title"].endswith("分布") for item in DEFAULT_CUTPOINTS)
    assert all(len(item["bins"]) == 3 for item in DEFAULT_CUTPOINTS)
    assert all(bins_form_distribution(item["bins"]) for item in DEFAULT_CUTPOINTS)
    validate_template_items(deepcopy(DEFAULT_CUTPOINTS))

    expected_totals = [116, 118, 112, 120, 84, 150, 142, 150, 180, 146, 146]
    assert [
        sum(_prepared_chart_values(item["key"], len(item["bins"])))
        for item in DEFAULT_CUTPOINTS
    ] == expected_totals


def test_template_rejects_overlapping_bins() -> None:
    items = deepcopy(DEFAULT_CUTPOINTS)
    items[0]["bins"][0]["upper_inclusive"] = True
    with pytest.raises(ValueError, match="边界值存在重叠"):
        validate_template_items(items)


def test_bin_boundaries_are_mutually_exclusive() -> None:
    bins = deepcopy(DEFAULT_CUTPOINTS[0]["bins"])
    assert _bin_contains(6.999, bins[0]) is True
    assert _bin_contains(7, bins[0]) is False
    assert _bin_contains(7, bins[1]) is True
    assert _bin_contains(9, bins[1]) is False
    assert _bin_contains(9, bins[2]) is True


def test_bins_form_a_gap_free_pie_distribution() -> None:
    bins = deepcopy(DEFAULT_CUTPOINTS[0]["bins"])
    assert bins_form_distribution(bins) is True

    bins[1]["lower"] = 8
    assert bins_form_distribution(bins) is False

    bins[1]["lower"] = 7
    bins[0]["upper_inclusive"] = True
    assert bins_form_distribution(bins) is False


def test_prepared_chart_uses_demo_people_without_fabricated_evidence() -> None:
    selected = DEFAULT_CUTPOINTS[0]
    bundle = SimpleNamespace(
        meeting=SimpleNamespace(id=uuid4()),
        analysis_version=3,
    )
    template = SimpleNamespace(id=uuid4())
    version = SimpleNamespace(version=2)
    session = SimpleNamespace(add=lambda _: None)

    row = _prepared_chart_spec(
        bundle=bundle,
        template=template,
        version=version,
        selected=selected,
        chart_type="pie",
        title=None,
        organization_id=uuid4(),
        session=session,
    )

    assert row.title == "24周HbA1c水平分布"
    assert row.subtitle == ""
    assert row.spec["data_origin"] == "demo"
    assert row.spec["denominator"] == {"name": "人数", "value": 116}
    assert [category["value"] for category in row.spec["categories"]] == [51, 51, 14]
    assert all(category["evidence"] == [] for category in row.spec["categories"])
    assert sum(category["percentage"] for category in row.spec["categories"]) == pytest.approx(100, abs=0.1)
    assert "本次样本共 116 人" in row.spec["interpretation"]

    svg = render_chart_svg({**row.spec, "type": "bar"})
    assert "统计口径" not in svg
    assert "独立参会者人数" not in svg
    assert "&lt;7.0%" in svg


@pytest.mark.asyncio
async def test_default_template_content_upgrade_is_idempotent() -> None:
    template = SimpleNamespace(
        id=uuid4(),
        latest_version=1,
        template_key="medical-default-v1",
    )
    old_items = deepcopy(DEFAULT_CUTPOINTS)
    old_items[0].pop("question")
    old_version = SimpleNamespace(version=1, items=old_items)
    session = SimpleNamespace(
        scalar=AsyncMock(side_effect=[template, old_version]),
        add=MagicMock(),
        flush=AsyncMock(),
    )

    _, upgraded = await ensure_default_cutpoint_template(
        session,
        organization_id=uuid4(),
    )

    assert template.latest_version == 2
    assert upgraded.version == 2
    assert upgraded.items == DEFAULT_CUTPOINTS
    session.add.assert_called_once_with(upgraded)

    current_session = SimpleNamespace(
        scalar=AsyncMock(side_effect=[template, upgraded]),
        add=MagicMock(),
        flush=AsyncMock(),
    )
    _, unchanged = await ensure_default_cutpoint_template(
        current_session,
        organization_id=uuid4(),
    )
    assert unchanged is upgraded
    current_session.add.assert_not_called()


@pytest.mark.asyncio
async def test_chart_list_only_returns_latest_template_version() -> None:
    template_id = uuid4()
    old_row = SimpleNamespace(
        spec={
            "chart_mode": "cutpoint_distribution",
            "template_id": str(template_id),
            "template_version": 1,
        }
    )
    current_row = SimpleNamespace(
        spec={
            "chart_mode": "cutpoint_distribution",
            "template_id": str(template_id),
            "template_version": 2,
        }
    )
    legacy_row = SimpleNamespace(spec={"chart_mode": "coverage"})
    result_sets = [
        SimpleNamespace(all=lambda: [old_row, current_row, legacy_row]),
        SimpleNamespace(all=lambda: [SimpleNamespace(id=template_id, latest_version=2)]),
    ]
    session = SimpleNamespace(scalars=AsyncMock(side_effect=result_sets))

    rows = await list_chart_specs(
        session,
        meeting_id=uuid4(),
        analysis_version=3,
    )

    assert rows == [current_row]


def test_count_modes_deduplicate_speakers_or_sources() -> None:
    observations = [
        {"speakerName": "张三", "sourceIds": ["s1", "s2"]},
        {"speakerName": "张三", "sourceIds": ["s2"]},
        {"speakerName": "李四", "sourceIds": ["s3"]},
    ]
    source_by_id = {key: {"sourceId": key} for key in ("s1", "s2", "s3")}
    assert _count_binned_observations(
        observations,
        count_mode="unique_speakers",
        source_by_id=source_by_id,
    ) == 2
    assert _count_binned_observations(
        observations,
        count_mode="evidence_count",
        source_by_id=source_by_id,
    ) == 3
