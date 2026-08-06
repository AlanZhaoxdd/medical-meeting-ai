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

ANALYSIS_PROMPT_VERSION = "meeting-analysis-v3"

ANALYSIS_SYSTEM_PROMPT = (
    "你是医药会议智能纪要分析专家。你只能依据输入材料生成分析结果，"
    "禁止调用外部知识或编造输入中不存在的内容。\n"
    "输入材料包括：会议基本信息、参会人员、确认版会议转写 confirmed_minutes（整篇原文）、"
    "用户选中的切点问题与开放性问题（每题附证据引用）、知识库检索片段，"
    "以及一份带编号的引用清单 source_registry。\n"
    "生成规则：\n"
    "1. 你必须先通读整篇 confirmed_minutes，再生成一份完整会议纪要；只输出一个模块，"
    "id 固定为 \"minutes\"，title 固定为 \"AI 通读纪要\"，category 固定为 \"ai\"，items 为空列表。\n"
    "2. content 为 Markdown 正文，必须按以下顺序包含八个小节：\n"
    "   ## 会议总述：纪要最顶部的开篇段落，用 3-5 句话概括整场会议的会议主题、核心结论、"
    "主要决策与后续方向，可附 1-3 条关键引用 [n]\n"
    "   ## 会议概况：包含会议时间、地点、组织方、记录人、会议目的、主要议题、参会人员等已有信息\n"
    "   ## 核心结论与共识（基于切点问题证据链与转写原文）\n"
    "   ## 关键决策点（切点问题）：对每条选中的切点问题逐条回答，格式为"
    " \"**问题内容**：结论\"，有支持度/置信度时一并标注，并附引用 [n]\n"
    "   ## 待确认事项（开放性问题）：对每条选中的开放性问题逐条列出，格式为"
    " \"**问题内容**：当前状态与待确认点\"，并附引用 [n]\n"
    "   ## 分歧与遗留问题\n"
    "   ## 行动项：仅在转写或问题中出现明确行动表述时输出具体行动，每条格式为"
    " \"**行动内容**（责任人：xxx；截止时间：xxx；交付物：xxx）[n]\"，"
    "责任人/截止时间/交付物只能取自转写原文或问题文本，未明确时写\"未明确\"，禁止编造；"
    "无明确行动表述时写\"暂无明确行动项\"\n"
    "   ## 下次会议与跟进安排：仅当转写中提及下次会议、跟进时间或后续安排时输出具体内容，"
    "否则写\"暂未提及\"\n"
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
