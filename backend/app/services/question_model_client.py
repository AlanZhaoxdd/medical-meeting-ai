from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from pydantic import BaseModel, SecretStr

from app.core.config import get_settings
from app.core.exceptions import AppException
from app.schemas.question_generation import QualityReview, QuestionBatch, RetrievalPlan

PROMPT_VERSION = "question-generation-v2"
SchemaT = TypeVar("SchemaT", bound=BaseModel)


def _raw_structured_value(raw: Any) -> Any:
    """Extract provider JSON content when LangChain leaves parsed unset."""
    if isinstance(raw, dict):
        content = raw.get("content")
        if content is not None:
            return content
        tool_calls = raw.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            first = tool_calls[0]
            if isinstance(first, dict):
                function = first.get("function")
                if isinstance(function, dict) and function.get("arguments") is not None:
                    return function["arguments"]
                return first.get("args")
        return raw
    content = getattr(raw, "content", None)
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        )
    return content if content is not None else raw


def _parse_json_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text.startswith("```"):
        text = text.removeprefix("```").removeprefix("json").strip()
        text = text.removesuffix("```").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def _unwrap_schema_envelope(value: Any, schema: type[BaseModel]) -> Any:
    """DeepSeek JSON mode may wrap the object under the schema name."""
    if not isinstance(value, dict):
        return value
    name = re.sub(r"(?<!^)(?=[A-Z])", "_", schema.__name__).lower()
    nested = value.get(name)
    return nested if isinstance(nested, dict) else value


def _legacy_queries(
    values: Any, *, question_type: str, grounding: str = ""
) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    normalized: list[dict[str, Any]] = []
    for value in values:
        if isinstance(value, str):
            query = value.strip()
            if not query:
                continue
            value = {"query": query, "topic": query}
        if not isinstance(value, dict) or not str(value.get("query") or "").strip():
            continue
        query = str(value["query"]).strip()
        anchors = [str(value.get("entity") or ""), str(value.get("topic") or "")]
        if grounding:
            tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9][A-Za-z0-9./+-]{1,}", query)
            anchors.extend(tokens)
            if not any(anchor and anchor in grounding for anchor in anchors):
                continue
        topic = value.get("topic") or value.get("entity") or query[:200]
        if grounding and topic not in grounding:
            topic = next(
                (anchor for anchor in anchors if anchor and anchor in grounding),
                topic,
            )
        normalized.append(
            {
                "query": query,
                "purpose": value.get("purpose") or "基于确认版纪要检索相关证据",
                "topic": topic,
                "keywords": value.get("keywords") or [],
                "question_type": value.get("question_type") or question_type,
                "top_k": value.get("top_k") or 12,
            }
        )
    return normalized


def _normalize_schema_payload(
    value: Any, schema: type[BaseModel], payload: dict[str, Any] | None = None
) -> Any:
    if schema is not RetrievalPlan or not isinstance(value, dict):
        return value
    normalized = dict(value)
    meeting_context = payload.get("meeting_context", {}) if payload else {}
    grounding = " ".join(
        str(value or "")
        for value in (
            payload.get("confirmed_minutes") if payload else "",
            *(meeting_context.values() if isinstance(meeting_context, dict) else []),
        )
    )
    if "cutpoint_queries" not in normalized:
        normalized["cutpoint_queries"] = _legacy_queries(
            normalized.get("fact_check_queries"), question_type="cut_point", grounding=grounding
        )[:10]
    if "open_question_queries" not in normalized:
        normalized["open_question_queries"] = _legacy_queries(
            normalized.get("clinical_discussion_queries"),
            question_type="open_ended",
            grounding=grounding,
        )[:10]
    normalized.pop("fact_check_queries", None)
    normalized.pop("clinical_discussion_queries", None)
    return normalized


class QuestionGenerationModelClient:
    """One structured-output LLM boundary shared by every graph node."""

    def __init__(
        self,
        plan_builder: Callable[[dict[str, Any]], Awaitable[RetrievalPlan]] | None = None,
        generator: Callable[[dict[str, Any]], Awaitable[QuestionBatch]] | None = None,
        reviewer: Callable[[dict[str, Any]], Awaitable[QualityReview]] | None = None,
        *,
        model_name: str | None = None,
    ) -> None:
        self.plan_builder = plan_builder
        self.generator = generator
        self.reviewer = reviewer
        self.model_name = model_name or get_settings().llm_model or "unconfigured"

    async def build_plan(self, payload: dict[str, Any]) -> RetrievalPlan:
        if self.plan_builder is not None:
            return await self.plan_builder(payload)
        return await self._invoke(
            RetrievalPlan,
            "你是医药行业知识检索规划专家。根据会议基本信息和用户最终确认的会议纪要，"
            "识别真实出现的疾病、药品、研究、指南、政策和术语，分别生成事实核验型切点查询"
            "与临床讨论型开放查询。不得编造输入中没有的实体，内容不足时减少查询。",
            payload,
            temperature=0.1,
        )

    async def generate(self, payload: dict[str, Any]) -> QuestionBatch:
        if self.generator is not None:
            return await self.generator(payload)
        return await self._invoke(
            QuestionBatch,
            "你是医药会议问题生成专家。只使用确认版会议纪要和给定知识库证据生成问题；"
            "每题必须引用输入中真实存在的 chunk_id/document_id 和原文 quote，不输出答案；"
            "切点问题和开放性问题每类最多生成 10 条。"
            "切点问题要有明确事实边界；开放性问题必须适合专家讨论。证据不足时少生成或返回空数组。",
            payload,
            temperature=0.2,
        )

    async def review(self, payload: dict[str, Any]) -> QualityReview:
        if self.reviewer is not None:
            return await self.reviewer(payload)
        return await self._invoke(
            QualityReview,
            "你是严格的医药会议问题质量评审专家。逐题输出 pass/reject/revise；检查会议相关性、"
            "题型、清晰度、专业价值、证据支持和过度推断。不要修改或伪造证据 ID，权限由程序判断。",
            payload,
            temperature=0,
        )

    async def _invoke(
        self,
        schema: type[SchemaT],
        instruction: str,
        payload: dict[str, Any],
        *,
        temperature: float,
    ) -> SchemaT:
        settings = get_settings()
        if not settings.llm_base_url or not settings.resolved_llm_api_key or not settings.llm_model:
            raise AppException(503, "question_model_unavailable", "问题生成模型不可用")
        from langchain_openai import ChatOpenAI

        options: dict[str, Any] = {
            "base_url": settings.llm_base_url,
            "api_key": SecretStr(settings.resolved_llm_api_key),
            "model": settings.llm_model,
            "temperature": temperature,
            "timeout": 60,
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
            f"{instruction}\n请以合法 JSON 对象输出，且必须严格符合给定 Schema。\n"
            "不要复述输入字段；只输出 Schema 定义的字段，不要输出答案、会议上下文或知识库原文。\n"
            f"Schema JSON：{json.dumps(schema.model_json_schema(), ensure_ascii=False)}\n"
            f"输入：{json.dumps(payload, ensure_ascii=False, default=str)[:120000]}"
        )
        response = await model.ainvoke(prompt)
        parsed = response.get("parsed") if isinstance(response, dict) else response
        if parsed is None and isinstance(response, dict):
            parsed = _parse_json_value(_raw_structured_value(response.get("raw")))
        parsed = _parse_json_value(parsed)
        parsed = _unwrap_schema_envelope(parsed, schema)
        parsed = _normalize_schema_payload(parsed, schema, payload)
        if isinstance(parsed, schema):
            return parsed
        try:
            return schema.model_validate(parsed)
        except Exception as exc:
            raise AppException(502, "structured_output_invalid", "模型结构化输出校验失败") from exc
