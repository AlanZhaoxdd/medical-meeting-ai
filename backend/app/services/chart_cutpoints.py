"""Versioned cut-point definitions used by medical chart extraction."""

from __future__ import annotations

from typing import Any


CUTPOINT_TEMPLATE_KEY = "medical-default-v1"

# The keys are deliberately stable.  Display text and aliases may be revised
# in a future template version without changing the aggregation contract.
def _bin(
    key: str,
    label: str,
    lower: float | None,
    upper: float | None,
    *,
    lower_inclusive: bool = True,
    upper_inclusive: bool = False,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "lower": lower,
        "upper": upper,
        "lower_inclusive": lower_inclusive if lower is not None else False,
        "upper_inclusive": upper_inclusive if upper is not None else False,
    }


DEFAULT_CUTPOINTS: list[dict[str, Any]] = [
    {
        "key": "hba1c",
        "label": "糖化血红蛋白（HbA1c）",
        "question": "在中国T2D合并CKD患者中，使用司美格鲁肽1.0mg治疗24周后，可评估患者的HbA1c水平如何分布？",
        "chart_title": "24周HbA1c水平分布",
        "aliases": ["HbA1c", "Hb1c", "糖化血红蛋白"],
        "indicator": "治疗24周后的HbA1c水平",
        "indicator_options": ["治疗24周后的HbA1c水平", "基线水平", "变化值"],
        "unit": "%",
        "unit_aliases": ["%"],
        "count_mode": "unique_speakers",
        "bins": [_bin("lt_7", "<7.0%", None, 7), _bin("7_to_9", "7.0%~<9.0%", 7, 9), _bin("gte_9", "≥9.0%", 9, None)],
    },
    {
        "key": "glucose",
        "label": "血糖水平",
        "question": "在中国T2D合并CKD患者中，使用司美格鲁肽1.0mg治疗24周后，可评估患者的空腹血糖水平如何分布？",
        "chart_title": "24周空腹血糖水平分布",
        "aliases": ["血糖", "空腹血糖", "血糖水平", "glucose"],
        "indicator": "治疗24周后的空腹血糖水平",
        "indicator_options": ["治疗24周后的空腹血糖水平", "基线值", "变化值"],
        "unit": "mmol/L",
        "unit_aliases": ["mmol/L", "mmol/l"],
        "count_mode": "unique_speakers",
        "bins": [_bin("lt_7", "<7.0mmol/L", None, 7), _bin("7_to_10", "7.0~<10.0mmol/L", 7, 10), _bin("gte_10", "≥10.0mmol/L", 10, None)],
    },
    {
        "key": "endpoint_attainment",
        "label": "终点达标率",
        "question": "治疗24周后，患者在血糖、UACR和血压三个预设管理目标中的个人达标率如何分布？",
        "chart_title": "心肾代谢目标个人达标率分布",
        "aliases": ["达标率", "终点达标", "管理目标"],
        "indicator": "血糖、UACR和血压三个管理目标的个人达标率",
        "indicator_options": ["心肾代谢目标个人达标率", "复合终点达标率", "单一终点达标率"],
        "unit": "%",
        "unit_aliases": ["%"],
        "count_mode": "unique_speakers",
        "bins": [_bin("lt_50", "<50%", None, 50), _bin("50_to_100", "50%~<100%", 50, 100), _bin("eq_100", "100%", 100, None)],
    },
    {
        "key": "hypoglycemia",
        "label": "低血糖",
        "question": "在中国T2D合并CKD安全性观察人群中，司美格鲁肽1.0mg治疗期间的年化临床低血糖事件率如何分布？",
        "chart_title": "年化低血糖事件率分布",
        "aliases": ["低血糖", "低血糖事件率", "hypoglycemia"],
        "indicator": "年化临床低血糖事件率",
        "indicator_options": ["年化临床低血糖事件率", "低血糖发生率", "低血糖发生次数"],
        "unit": "次/人年",
        "unit_aliases": ["次/人年", "次/人·年", "events/person-year"],
        "count_mode": "unique_speakers",
        "bins": [_bin("zero", "0次/人年", None, 0, upper_inclusive=True), _bin("gt_0_lt_1", ">0~<1次/人年", 0, 1, lower_inclusive=False), _bin("gte_1", "≥1次/人年", 1, None)],
    },
    {
        "key": "insulin_dose",
        "label": "胰岛素剂量",
        "question": "在同时接受基础胰岛素治疗的中国T2D合并CKD患者中，治疗24周时的基础胰岛素日剂量如何分布？",
        "chart_title": "基础胰岛素日剂量分布",
        "aliases": ["胰岛素剂量", "基础胰岛素", "insulin dose"],
        "indicator": "治疗24周时的基础胰岛素日剂量",
        "indicator_options": ["基础胰岛素日剂量", "胰岛素剂量", "剂量调整幅度"],
        "unit": "U/日",
        "unit_aliases": ["U", "U/日", "单位/日", "单位"],
        "count_mode": "unique_speakers",
        "bins": [_bin("lt_20", "<20U/日", None, 20), _bin("20_to_40", "20~<40U/日", 20, 40), _bin("gte_40", "≥40U/日", 40, None)],
    },
    {
        "key": "bmi",
        "label": "体重指数",
        "question": "在拟接受司美格鲁肽2.4mg长期体重管理的中国超重/肥胖合并CVD患者中，基线BMI如何分布？",
        "chart_title": "基线BMI分布",
        "aliases": ["BMI", "体重指数"],
        "indicator": "基线BMI",
        "indicator_options": ["基线BMI", "BMI", "BMI变化值"],
        "unit": "kg/m²",
        "unit_aliases": ["kg/m2", "kg/m²"],
        "count_mode": "unique_speakers",
        "bins": [_bin("27_to_30", "27~<30kg/m²", None, 30), _bin("30_to_35", "30~<35kg/m²", 30, 35), _bin("gte_35", "≥35kg/m²", 35, None)],
    },
    {
        "key": "weight_loss_goal",
        "label": "减重目标",
        "question": "在中国超重/肥胖合并CVD患者中，使用司美格鲁肽2.4mg治疗52周后，可评估患者相对基线的体重下降比例如何分布？",
        "chart_title": "52周体重下降比例分布",
        "aliases": ["减重目标", "体重下降", "体重下降比例"],
        "indicator": "治疗52周后相对基线的体重下降比例",
        "indicator_options": ["治疗52周后的体重下降比例", "达到减重目标的比例", "减重幅度"],
        "unit": "%",
        "unit_aliases": ["%"],
        "count_mode": "unique_speakers",
        "bins": [_bin("lt_5", "<5%", None, 5), _bin("5_to_10", "5%~<10%", 5, 10), _bin("gte_10", "≥10%", 10, None)],
    },
    {
        "key": "duration",
        "label": "时间",
        "question": "在中国超重/肥胖合并CVD患者的长期体重管理中，司美格鲁肽连续治疗时长如何分布？",
        "chart_title": "连续治疗时长分布",
        "aliases": ["用药时长", "治疗时长", "干预时长", "随访时间"],
        "indicator": "司美格鲁肽连续治疗时长",
        "indicator_options": ["连续治疗时长", "用药时长", "随访时间"],
        "unit": "月",
        "unit_aliases": ["月", "个月", "months"],
        "count_mode": "unique_speakers",
        "bins": [_bin("lt_6", "<6个月", None, 6), _bin("6_to_12", "6~<12个月", 6, 12), _bin("gte_12", "≥12个月", 12, None)],
    },
    {
        "key": "drug_dose",
        "label": "药物剂量",
        "question": "在纳入中国CKM综合管理路径的患者中，根据适应证、BMI、血糖和耐受性评估后的司美格鲁肽维持剂量如何分布？",
        "chart_title": "司美格鲁肽维持剂量分布",
        "aliases": ["司美格鲁肽剂量", "药物剂量", "维持剂量", "剂量"],
        "indicator": "个体化评估后的司美格鲁肽维持剂量",
        "indicator_options": ["维持剂量", "司美格鲁肽剂量", "个体化推荐剂量"],
        "unit": "mg",
        "unit_aliases": ["mg"],
        "count_mode": "unique_speakers",
        "bins": [_bin("dose_0_5", "0.5mg", None, 0.75), _bin("dose_1_0", "1.0mg", 0.75, 1.7), _bin("dose_2_4", "2.4mg", 1.7, None)],
    },
    {
        "key": "frequency",
        "label": "频次",
        "question": "在中国超重/肥胖合并CVD患者中，过去12周司美格鲁肽每周给药的漏用次数如何分布？",
        "chart_title": "近12周漏用药次数分布",
        "aliases": ["漏用药次数", "漏用次数", "给药频次", "频次"],
        "indicator": "过去12周每周给药的漏用次数",
        "indicator_options": ["漏用药次数", "给药频次", "发生次数"],
        "unit": "次",
        "unit_aliases": ["次"],
        "count_mode": "unique_speakers",
        "bins": [_bin("zero", "0次", None, 1), _bin("one", "1次", 1, 2), _bin("gte_2", "≥2次", 2, None)],
    },
    {
        "key": "proportion",
        "label": "占比",
        "question": "在中国超重/肥胖合并CVD患者中，过去12周计划给药完成率如何分布？",
        "chart_title": "近12周计划给药完成率分布",
        "aliases": ["计划给药完成率", "给药完成率", "占比", "比例"],
        "indicator": "过去12周计划给药完成率",
        "indicator_options": ["计划给药完成率", "依从性", "人群占比"],
        "unit": "%",
        "unit_aliases": ["%"],
        "count_mode": "unique_speakers",
        "bins": [_bin("lt_80", "<80%", None, 80), _bin("80_to_95", "80%~<95%", 80, 95), _bin("gte_95", "≥95%", 95, None)],
    },
]


def validate_template_items(items: list[dict[str, Any]]) -> None:
    """Validate the organization-owned 11-item cutpoint contract."""

    if len(items) != 11:
        raise ValueError("切点模板必须包含正好 11 个切点")
    keys = [str(item.get("key") or "").strip() for item in items]
    if any(not key for key in keys) or len(set(keys)) != len(keys):
        raise ValueError("切点 key 必须非空且不能重复")

    for item in items:
        if not str(item.get("unit") or "").strip():
            raise ValueError(f"切点 {item.get('key')} 必须配置单位")
        count_mode = str(item.get("count_mode") or "unique_speakers")
        if count_mode not in {"unique_speakers", "evidence_count"}:
            raise ValueError(f"切点 {item.get('key')} 的统计口径无效")
        bins = list(item.get("bins") or [])
        if len(bins) < 2:
            raise ValueError(f"切点 {item.get('key')} 至少需要 2 个区间")
        bin_keys = [str(value.get("key") or "").strip() for value in bins]
        if any(not key for key in bin_keys) or len(set(bin_keys)) != len(bin_keys):
            raise ValueError(f"切点 {item.get('key')} 的区间 key 必须唯一")
        for bin_item in bins:
            lower = bin_item.get("lower")
            upper = bin_item.get("upper")
            if lower is None and upper is None:
                raise ValueError(f"区间 {bin_item.get('key')} 至少需要一个边界")
            if lower is not None and upper is not None and float(lower) >= float(upper):
                raise ValueError(f"区间 {bin_item.get('key')} 下界必须小于上界")

        ordered = sorted(
            bins,
            key=lambda value: float("-inf") if value.get("lower") is None else float(value["lower"]),
        )
        for previous, current in zip(ordered, ordered[1:]):
            previous_upper = previous.get("upper")
            current_lower = current.get("lower")
            if previous_upper is None or current_lower is None:
                continue
            previous_upper = float(previous_upper)
            current_lower = float(current_lower)
            if previous_upper > current_lower:
                raise ValueError(f"切点 {item.get('key')} 的区间存在重叠")
            if previous_upper == current_lower and bool(previous.get("upper_inclusive", False)) and bool(current.get("lower_inclusive", True)):
                raise ValueError(f"切点 {item.get('key')} 的边界值存在重叠")


def bins_form_distribution(bins: list[dict[str, Any]]) -> bool:
    """Return whether bins form a gap-free, mutually exclusive distribution."""

    if len(bins) < 2:
        return False
    ordered = sorted(
        bins,
        key=lambda value: float("-inf") if value.get("lower") is None else float(value["lower"]),
    )
    if ordered[0].get("lower") is not None or ordered[-1].get("upper") is not None:
        return False
    for previous, current in zip(ordered, ordered[1:]):
        if previous.get("upper") is None or current.get("lower") is None:
            return False
        if float(previous["upper"]) != float(current["lower"]):
            return False
        # A shared boundary must belong to exactly one adjacent bin. Both
        # inclusive overlaps it; both exclusive leaves a gap.
        if bool(previous.get("upper_inclusive", False)) == bool(current.get("lower_inclusive", True)):
            return False
    return True


def default_template_payload() -> dict[str, Any]:
    return {
        "key": CUTPOINT_TEMPLATE_KEY,
        "name": "医学会议切点分析（默认）",
        "description": "用于11类医学会议定量切点的检索和图表生成。",
        "version": 1,
        "items": DEFAULT_CUTPOINTS,
    }


def get_cutpoint(key: str | None) -> dict[str, Any] | None:
    if not key:
        return None
    return next((item for item in DEFAULT_CUTPOINTS if item["key"] == key), None)
