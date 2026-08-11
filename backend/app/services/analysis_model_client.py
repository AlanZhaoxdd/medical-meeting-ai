from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import SecretStr

from app.core.config import get_settings
from app.core.exceptions import AppException
from app.schemas.analysis import AnalysisResult
from app.services.question_model_client import (
    _parse_json_value,
    _raw_structured_value,
    _unwrap_schema_envelope,
)

ANALYSIS_PROMPT_VERSION = "meeting-analysis-v4"

ANALYSIS_SYSTEM_PROMPT = (
    "你是医药会议智能纪要分析专家。你只能依据输入材料生成分析结果，"
    "禁止调用外部知识或编造输入中不存在的内容。\n"
    "输入材料包括：会议基本信息、参会人员、确认版会议转写 confirmed_minutes（整篇原文）、"
    "用户选中的切点问题与开放性问题（每题附证据引用）、知识库检索片段，"
    "以及一份带编号的引用清单 source_registry。\n"
    "生成规则：\n"
    "1. 你必须先通读整篇 confirmed_minutes，再生成一份完整会议纪要；只输出一个模块，"
    "id 固定为 \"minutes\"，title 固定为 \"AI 通读纪要\"，category 固定为 \"ai\"，items 为空列表。\n"
    "2. content 为 Markdown 正文，必须严格按以下六个小节的顺序与编号输出，"
    "每节以加粗标题开头（如 **一、会议概述**），正文用 3-5 句话精炼概括，"
    "全部使用连贯段落叙述（除行动计划可用“第一，…第二，…”编号外，不得使用项目符号列表），"
    "对关键药物、研究、患者人群与结论使用加粗强调（如 **司美格鲁肽 2.4mg**）：\n"
    "   **一、会议概述**：概括会议主题、参会专家领域与人数、核心聚焦问题、"
    "现场整体氛围（高度共识或存在策略分歧）与关键决策方向，可附 1-3 条关键引用 [n]\n"
    "   **二、分歧与焦虑**：聚焦尚未形成共识的管理方案，按立场描述分歧"
    "（如“半数专家…部分专家…少数专家…”），并列出焦虑的主要来源"
    "（如长期维持证据不足、停药反弹风险、医保/自费可及性等）；"
    "只能归纳输入中实际存在的观点，不得凭空补充\n"
    "   **三、循证数据解读**：解读会议引用的核心研究与数据（研究名称加粗），"
    "说明研究结论、证据价值及其相对既往证据的差异化意义；数据必须来自输入并附引用 [n]\n"
    "   **四、临床用药建议**：按患者人群分层给出用药建议"
    "（如 **合并 CVD 患者**、**单纯肥胖无 CVD 人群**），覆盖起始时机、剂量/滴定、疗程、"
    "监测与随访；建议必须能被输入中的转写、问题或知识库证据支撑\n"
    "   **五、专家共识**：汇总专家形成的关键统一共识，说明共识强度（如“高度一致”）；"
    "若与知识库历史会议结论一致，可注明“与 KB 既往会议结论一致”并附知识库引用 [n]；"
    "除非输入提供具体置信度数值，否则不得编造数值\n"
    "   **六、行动计划**：按“第一，…第二，…第三，…”的编号叙述形式列出具体行动，"
    "包括学术沟通方向、待跟进证据、内部沉淀（FAQ/幻灯片/知识库更新）等；"
    "每条行动只能来自转写原文或问题文本，未明确时写\"未明确\"，"
    "无明确行动表述时写\"暂无明确行动项\"\n"
    "3. 所有论断必须能追溯到 source_registry 中的引用编号 [n]；每条切点/开放性问题条目"
    "必须引用对应的问题来源（cutoff_question / open_question），模块级 citations 汇总全部用到的编号。\n"
    "4. 参会者观点只能摘录或改写自转写原文，不得补充转写之外的表述；无法回答的问题"
    "在 insufficient_notes 中说明原因。\n"
    "5. 输出必须为合法 JSON，严格符合给定 Schema；除 modules 与 insufficient_notes 外"
    "不输出任何字段；不要复述输入原文，要提炼改写。"
)


class AnalysisModelClient:
    """Structured-output LLM boundary for the AI meeting analysis pipeline."""

    def __init__(
        self,
        generator: Callable[[dict[str, Any]], Awaitable[AnalysisResult]] | None = None,
        *,
        model_name: str | None = None,
    ) -> None:
        self.generator = generator
        self.model_name = model_name or get_settings().llm_model or "unconfigured"

    async def generate(self, payload: dict[str, Any]) -> AnalysisResult:
        if self.generator is not None:
            return await self.generator(payload)
        return await self._invoke(AnalysisResult, payload)

    async def _invoke(
        self, schema: type[AnalysisResult], payload: dict[str, Any]
    ) -> AnalysisResult:
        settings = get_settings()
        if (
            not settings.llm_base_url
            or not settings.resolved_llm_api_key
            or not settings.llm_model
        ):
            raise AppException(503, "analysis_model_unavailable", "AI 分析模型不可用")
        from langchain_openai import ChatOpenAI

        options: dict[str, Any] = {
            "base_url": settings.llm_base_url,
            "api_key": SecretStr(settings.resolved_llm_api_key),
            "model": settings.llm_model,
            "temperature": 0.2,
            "timeout": 120,
            "max_retries": 2,
        }
        is_deepseek = "api.deepseek.com" in settings.llm_base_url
        if is_deepseek:
            options["extra_body"] = {"thinking": {"type": "disabled"}}
        model = ChatOpenAI(**options).with_structured_output(
            schema,
            method="json_mode" if is_deepseek else "function_calling",
            include_raw=True,
        )
        prompt = (
            f"{ANALYSIS_SYSTEM_PROMPT}\n请以合法 JSON 对象输出，且必须严格符合给定 Schema。\n"
            "不要复述输入字段；只输出 Schema 定义的字段，不要输出会议上下文或知识库原文。\n"
            f"Schema JSON：{json.dumps(schema.model_json_schema(), ensure_ascii=False)}\n"
            f"输入：{json.dumps(payload, ensure_ascii=False, default=str)[:160000]}"
        )
        response = await model.ainvoke(prompt)
        parsed = response.get("parsed") if isinstance(response, dict) else response
        if parsed is None and isinstance(response, dict):
            parsed = _parse_json_value(_raw_structured_value(response.get("raw")))
        parsed = _parse_json_value(parsed)
        parsed = _unwrap_schema_envelope(parsed, schema)
        if isinstance(parsed, schema):
            return parsed
        try:
            return schema.model_validate(parsed)
        except Exception as exc:
            raise AppException(
                502, "structured_output_invalid", "AI 分析结构化输出校验失败"
            ) from exc
