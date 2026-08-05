from __future__ import annotations

import json
from typing import Any, Literal

from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field, SecretStr, ValidationError

from app.core.config import get_settings
from app.core.exceptions import AppException
from app.services.observability import observe


def _is_provider_length_error(exc: Exception) -> bool:
    text = str(exc).casefold()
    return any(
        marker in text
        for marker in (
            "context length",
            "maximum context",
            "max tokens",
            "too many tokens",
            "prompt is too long",
            "request too large",
            "length limit was reached",
            "finish_reason=length",
            "finish reason: length",
        )
    )


def _merge_extractions(results: list[KnowledgeExtraction]) -> KnowledgeExtraction:
    """Merge batches deterministically and deduplicate equivalent claims."""
    merged: dict[tuple[str, str, str], ExtractedItem] = {}
    for result in results:
        for item in result.items:
            key = (
                item.item_type,
                item.title.casefold().strip(),
                item.normalized_content.casefold().strip(),
            )
            existing = merged.get(key)
            if existing is None:
                merged[key] = item
                continue
            refs = {ref.model_dump_json(): ref for ref in existing.source_refs}
            refs.update({ref.model_dump_json(): ref for ref in item.source_refs})
            existing.source_refs = list(refs.values())
            existing.confidence = max(existing.confidence, item.confidence)
    return KnowledgeExtraction(items=list(merged.values()))


class ExtractedSource(BaseModel):
    chunk_id: str
    block_id: str | None = None
    quote: str = Field(min_length=1)
    page_number: int | None = None
    slide_number: int | None = None
    speaker: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None


class ExtractedItem(BaseModel):
    item_type: Literal[
        "meeting_metadata",
        "participant",
        "topic",
        "insight",
        "consensus",
        "disagreement",
        "evidence_claim",
        "evidence_gap",
        "action_item",
    ]
    title: str = Field(min_length=1, max_length=500)
    normalized_content: str = Field(min_length=1)
    structured_data: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[ExtractedSource] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class KnowledgeExtraction(BaseModel):
    items: list[ExtractedItem]


def _parse_knowledge_extraction(value: Any) -> KnowledgeExtraction | None:
    if isinstance(value, KnowledgeExtraction):
        return value
    try:
        if isinstance(value, dict):
            return KnowledgeExtraction.model_validate(value)
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("```"):
                text = text.removeprefix("```json").removeprefix("```").strip()
                text = text.removesuffix("```").strip()
            try:
                return KnowledgeExtraction.model_validate_json(text)
            except (ValidationError, ValueError, json.JSONDecodeError):
                # Some OpenAI-compatible providers prepend a short explanation
                # even when JSON mode is enabled. Recover the first JSON object.
                start = text.find("{")
                end = text.rfind("}")
                if start >= 0 and end > start:
                    return KnowledgeExtraction.model_validate_json(text[start : end + 1])
    except (ValidationError, ValueError, json.JSONDecodeError):
        return None
    return None


def _raw_structured_value(raw: Any) -> Any:
    if isinstance(raw, dict):
        additional_kwargs = raw.get("additional_kwargs")
        tool_calls = raw.get("tool_calls")
        if not tool_calls and isinstance(additional_kwargs, dict):
            tool_calls = additional_kwargs.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            first_call = tool_calls[0]
            if isinstance(first_call, dict):
                function = first_call.get("function")
                if isinstance(function, dict) and function.get("arguments") is not None:
                    return function["arguments"]
                if first_call.get("args") is not None:
                    return first_call["args"]
        return raw.get("content")
    tool_calls = getattr(raw, "tool_calls", None)
    if isinstance(tool_calls, list) and tool_calls:
        first_call = tool_calls[0]
        if isinstance(first_call, dict):
            function = first_call.get("function")
            if isinstance(function, dict) and function.get("arguments") is not None:
                return function["arguments"]
            return first_call.get("args")
    content = getattr(raw, "content", None)
    if isinstance(content, list):
        return "".join(
            block.get("text", "") if isinstance(block, dict) else str(block) for block in content
        )
    return content


async def extract_knowledge(
    chunks: list[dict[str, Any]], fields: list[str]
) -> KnowledgeExtraction:
    settings = get_settings()
    api_key = settings.resolved_llm_api_key
    if not settings.llm_base_url or not api_key or not settings.llm_model:
        raise AppException(503, "llm_not_configured", "尚未配置结构化知识提取模型")
    model_options: dict[str, Any] = {
        "base_url": settings.llm_base_url,
        "api_key": SecretStr(api_key),
        "model": settings.llm_model,
        "temperature": 0,
        "timeout": 60,
        "max_retries": 2,
    }
    is_deepseek = "api.deepseek.com" in settings.llm_base_url
    if is_deepseek:
        # LangChain forces a specific tool_choice for structured output. DeepSeek
        # V4 rejects that combination in its default thinking mode.
        model_options["extra_body"] = {"thinking": {"type": "disabled"}}
    model = ChatOpenAI(**model_options).with_structured_output(
        KnowledgeExtraction,
        method="json_mode" if is_deepseek else "function_calling",
        include_raw=True,
    )
    # Keep each provider request bounded; a failed oversized completion must
    # become a stable, user-safe error rather than ingestion_unexpected_error.
    max_batch_chars = min(30000, max(4000, settings.model_service_max_input_characters // 2))
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0
    for chunk in chunks:
        size = len(str(chunk.get("content") or "")) + 40
        if current and current_chars + size > max_batch_chars:
            batches.append(current)
            current, current_chars = [], 0
        current.append(chunk)
        current_chars += size
    if current or not batches:
        batches.append(current)
    results: list[KnowledgeExtraction] = []
    schema = json.dumps(KnowledgeExtraction.model_json_schema(), ensure_ascii=False)
    for batch in batches:
        source = "\n\n".join(
            f"[chunk_id={chunk['chunk_id']}]\n{chunk['content']}" for chunk in batch
        )
        prompt = (
            "你是医药会议知识抽取器。仅抽取原文明确支持的事实，不得补充常识或推断。"
            "每个知识项必须至少引用一个真实 chunk_id，并给出该 chunk 中逐字可核对的 quote。"
            "请只输出合法 JSON 对象，不要输出 Markdown、解释文字或代码围栏。"
            f"本模板启用字段：{', '.join(fields)}。每次最多输出 40 个知识项；"
            f"输出必须符合以下 JSON Schema：{schema}\n\n"
            f"来源：\n{source}"
        )
        try:
            with observe(
                "knowledge.extract", as_type="generation", model=settings.llm_model,
                metadata={
                    "prompt_version": "kb-extraction-v1",
                    "field_count": len(fields),
                    "chunk_count": len(batch),
                    "input_characters": len(prompt),
                },
            ) as observation:
                response = await model.ainvoke(prompt)
                result = _parse_knowledge_extraction(
                    response.get("parsed") if isinstance(response, dict) else None
                )
                if result is None and isinstance(response, dict):
                    result = _parse_knowledge_extraction(_raw_structured_value(response.get("raw")))
                observation.update(
                    output=(
                        {"item_count": len(result.items)}
                        if isinstance(result, KnowledgeExtraction)
                        else {"structured_output_valid": False}
                    )
                )
        except Exception as exc:
            if _is_provider_length_error(exc):
                if len(batch) > 1:
                    midpoint = max(1, len(batch) // 2)
                    left = await extract_knowledge(batch[:midpoint], fields)
                    right = await extract_knowledge(batch[midpoint:], fields)
                    results.append(_merge_extractions([left, right]))
                    continue
                raise AppException(
                    413,
                    "llm_completion_limit",
                    "模型输出达到长度限制，请缩短会议内容后重试",
                ) from exc
            raise
        if not isinstance(result, KnowledgeExtraction):
            response_type = type(response).__name__
            parsed_type = (
                type(response.get("parsed")).__name__ if isinstance(response, dict) else None
            )
            raise AppException(
                502,
                "structured_output_invalid",
                "模型结构化输出校验失败",
                {"response_type": response_type, "parsed_type": parsed_type},
            )
        results.append(result)
    result = _merge_extractions(results)
    return result
