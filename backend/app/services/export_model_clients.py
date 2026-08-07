from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.core.config import get_settings
from app.core.exceptions import AppException
from app.schemas.export import PptDeckSpec
from app.services.export_prompts import (
    CHART_PLAN_PROMPT_VERSION,
    CHART_PLAN_SYSTEM_PROMPT,
    PPT_OUTLINE_PROMPT_VERSION,
    PPT_OUTLINE_SYSTEM_PROMPT,
)
from app.services.question_model_client import (
    _parse_json_value,
    _raw_structured_value,
    _unwrap_schema_envelope,
)


class PptMention(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=200)
    sourceIds: list[str] = Field(default_factory=list, max_length=12)


class PptSlide(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pageNumber: int = Field(ge=1, le=12)
    type: str = Field(max_length=40)
    title: str = Field(min_length=1, max_length=200)
    bullets: list[PptMention] = Field(default_factory=list, max_length=8)
    chartIds: list[str] = Field(default_factory=list, max_length=6)
    speakerNotes: str | None = Field(default=None, max_length=1000)


class ChartMentionItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speakerName: str = Field(min_length=1, max_length=200)
    sourceIds: list[str] = Field(default_factory=list, max_length=20)
    rationale: str = Field(default="", max_length=500)


class ChartMentionSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    questionId: str = Field(min_length=1, max_length=100)
    mentions: list[ChartMentionItem] = Field(default_factory=list, max_length=200)


class StanceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speakerName: str = Field(min_length=1, max_length=200)
    stance: str = Field(
        pattern="^(SUPPORT|CONDITIONAL_SUPPORT|NEUTRAL|OPPOSE|NOT_MENTIONED)$"
    )
    sourceIds: list[str] = Field(default_factory=list, max_length=20)
    rationale: str = Field(default="", max_length=500)


class ChartPlanResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mentionSets: list[ChartMentionSet] = Field(default_factory=list, max_length=50)
    stanceClassifications: list[StanceItem] = Field(default_factory=list, max_length=200)
    planNote: str = Field(default="", max_length=500)


class ChartPlanModelClient:
    """Structured-output LLM boundary for chart classification planning."""

    def __init__(
        self,
        generator: Callable[[dict[str, Any]], Awaitable[ChartPlanResult]] | None = None,
        *,
        model_name: str | None = None,
    ) -> None:
        self.generator = generator
        self.model_name = model_name or get_settings().llm_model or "unconfigured"

    async def plan(self, payload: dict[str, Any]) -> ChartPlanResult:
        if self.generator is not None:
            return await self.generator(payload)
        return await self._invoke(ChartPlanResult, payload)

    async def _invoke(
        self, schema: type[ChartPlanResult], payload: dict[str, Any]
    ) -> ChartPlanResult:
        settings = get_settings()
        if (
            not settings.llm_base_url
            or not settings.resolved_llm_api_key
            or not settings.llm_model
        ):
            raise AppException(503, "chart_model_unavailable", "图表分析模型不可用")
        from langchain_openai import ChatOpenAI

        options: dict[str, Any] = {
            "base_url": settings.llm_base_url,
            "api_key": SecretStr(settings.resolved_llm_api_key),
            "model": settings.llm_model,
            "temperature": 0.1,
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
            f"{CHART_PLAN_SYSTEM_PROMPT}\n"
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
                502, "chart_plan_invalid", "图表分类结构化输出校验失败"
            ) from exc


class PptOutlineModelClient:
    """Structured-output LLM boundary for PPT outline generation."""

    def __init__(
        self,
        generator: Callable[[dict[str, Any]], Awaitable[PptDeckSpec]] | None = None,
        *,
        model_name: str | None = None,
    ) -> None:
        self.generator = generator
        self.model_name = model_name or get_settings().llm_model or "unconfigured"

    async def generate(self, payload: dict[str, Any]) -> PptDeckSpec:
        if self.generator is not None:
            return await self.generator(payload)
        return await self._invoke(PptDeckSpec, payload)

    async def regenerate_page(
        self,
        *,
        spec: PptDeckSpec,
        page_number: int,
        instruction: str | None,
    ) -> PptDeckSpec:
        """Regenerate a single page while preserving the rest of the outline."""

        payload = {
            "existing_outline": spec.model_dump(mode="json"),
            "page_number": page_number,
            "instruction": instruction or "",
        }
        if self.generator is not None:
            return await self.generator(payload)
        settings = get_settings()
        if (
            not settings.llm_base_url
            or not settings.resolved_llm_api_key
            or not settings.llm_model
        ):
            raise AppException(503, "ppt_model_unavailable", "PPT 大纲生成模型不可用")
        from langchain_openai import ChatOpenAI

        options: dict[str, Any] = {
            "base_url": settings.llm_base_url,
            "api_key": SecretStr(settings.resolved_llm_api_key),
            "model": settings.llm_model,
            "temperature": 0.2,
            "timeout": 90,
            "max_retries": 1,
        }
        is_deepseek = "api.deepseek.com" in settings.llm_base_url
        if is_deepseek:
            options["extra_body"] = {"thinking": {"type": "disabled"}}
        model = ChatOpenAI(**options).with_structured_output(
            PptDeckSpec,
            method="json_mode" if is_deepseek else "function_calling",
            include_raw=True,
        )
        prompt = (
            f"{PPT_OUTLINE_SYSTEM_PROMPT}\n"
            "你现在只需要重新设计指定页码（page_number）这一页的内容，其余页面保持原样，"
            "输出完整的新 PptDeckSpec。\n"
            f"Schema JSON：{json.dumps(PptDeckSpec.model_json_schema(), ensure_ascii=False)}\n"
            f"输入：{json.dumps(payload, ensure_ascii=False, default=str)[:100000]}"
        )
        response = await model.ainvoke(prompt)
        parsed = response.get("parsed") if isinstance(response, dict) else response
        if parsed is None and isinstance(response, dict):
            parsed = _parse_json_value(_raw_structured_value(response.get("raw")))
        parsed = _parse_json_value(parsed)
        parsed = _unwrap_schema_envelope(parsed, PptDeckSpec)
        if isinstance(parsed, PptDeckSpec):
            return parsed
        try:
            return PptDeckSpec.model_validate(parsed)
        except Exception as exc:
            raise AppException(
                502, "ppt_outline_invalid", "PPT 大纲结构化输出校验失败"
            ) from exc

    async def _invoke(self, schema: type[PptDeckSpec], payload: dict[str, Any]) -> PptDeckSpec:
        settings = get_settings()
        if (
            not settings.llm_base_url
            or not settings.resolved_llm_api_key
            or not settings.llm_model
        ):
            raise AppException(503, "ppt_model_unavailable", "PPT 大纲生成模型不可用")
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
            f"{PPT_OUTLINE_SYSTEM_PROMPT}\n"
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
                502, "ppt_outline_invalid", "PPT 大纲结构化输出校验失败"
            ) from exc
