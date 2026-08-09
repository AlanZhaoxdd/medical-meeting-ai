from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.exceptions import AppException
from app.schemas.analysis import (
    MeetingChatRequest,
    MeetingChatResponse,
    MeetingChatSource,
)
from app.services.meeting_chat import (
    CHAT_PROMPT_VERSION,
    CHAT_REWRITE_PROMPT_VERSION,
    CHAT_REWRITE_SYSTEM_PROMPT,
    CHAT_SYSTEM_PROMPT,
    INSUFFICIENT_ANSWER,
    MeetingChatModelClient,
    MeetingChatRewriter,
    build_chat_materials,
    build_chat_sources,
    build_rewrite_prompt,
    generate_chat_answer,
)


def _context(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "meeting": SimpleNamespace(organization_id=uuid4()),
        "meeting_context": {
            "title": "月度病例讨论会",
            "knowledge_base_name": "临床指南库",
        },
        "kb_name": "临床指南库",
        "minutes": "确认版纪要全文",
        "blocks": [],
        "confirmed_document_id": str(uuid4()),
    }
    values.update(overrides)
    return values


def test_chat_prompt_version_is_stable() -> None:
    assert CHAT_PROMPT_VERSION == "meeting-chat-v1"


def test_rewrite_prompt_version_is_stable() -> None:
    assert CHAT_REWRITE_PROMPT_VERSION == "chat-rewrite-v1"


def test_chat_prompt_forbids_fabrication_and_requires_citations() -> None:
    assert "禁止调用外部知识或编造" in CHAT_SYSTEM_PROMPT
    assert "[n]" in CHAT_SYSTEM_PROMPT
    assert INSUFFICIENT_ANSWER in CHAT_SYSTEM_PROMPT


def test_rewrite_prompt_contains_rules_and_rendered_history() -> None:
    prompt = build_rewrite_prompt(
        [{"question": "剂量是多少？", "answer": "一天两次。"}],
        "那副作用呢？",
    )
    assert CHAT_REWRITE_SYSTEM_PROMPT in prompt
    assert "剂量是多少？" in prompt
    assert "一天两次。" in prompt
    assert "当前问题：那副作用呢？" in prompt


def test_rewrite_prompt_keeps_most_recent_turns_within_cap() -> None:
    history = [
        {"question": f"旧问题{i}", "answer": f"旧答案{i}"}
        for i in range(20)
    ]
    prompt = build_rewrite_prompt(history, "当前问题", history_cap=100)
    assert "旧问题0" not in prompt
    assert "旧问题19" in prompt


def test_chat_request_accepts_camel_case_from_frontend() -> None:
    request = MeetingChatRequest(
        **{
            "meetingId": str(uuid4()),
            "question": " 本次会议有哪些结论？ ",
            "scope": "CURRENT_MEETING",
        }
    )
    assert request.scope == "CURRENT_MEETING"
    assert request.question == "本次会议有哪些结论？"


def test_chat_request_accepts_snake_case_and_default_scope() -> None:
    request = MeetingChatRequest(meeting_id=uuid4(), question="问题")
    assert request.scope == "MEETING_AND_KB"
    assert request.conversation_id is None


def test_chat_request_rejects_extra_fields_and_empty_question() -> None:
    with pytest.raises(ValidationError):
        MeetingChatRequest(meeting_id=uuid4(), question="问题", unexpected=True)
    with pytest.raises(ValidationError):
        MeetingChatRequest(meeting_id=uuid4(), question="   ")


def test_chat_response_schema_round_trip() -> None:
    source = MeetingChatSource(
        id="kb-c1",
        index=1,
        type="knowledge_base",
        title="指南",
        snippet="摘要",
        content="正文",
        chunk_id="c1",
    )
    response = MeetingChatResponse(
        conversation_id=uuid4(),
        message_id=uuid4(),
        answer="答案 [1]",
        status="COMPLETED",
        sources=[source],
    )
    assert response.status == "COMPLETED"
    assert response.sources[0].content == "正文"


def test_build_chat_sources_maps_transcript_and_kb_chunks() -> None:
    context = _context()
    chunks = [
        {
            "source_type": "confirmed_transcript",
            "chunk_id": "t1",
            "content": "张医生建议先观察一周",
            "speaker_name": "张医生",
            "block_id": "b1",
        },
        {
            "source_type": "knowledge_base",
            "chunk_id": "k1",
            "content": "指南规定随访周期为一个月",
            "document_title": "随访指南.pdf",
        },
    ]
    sources = build_chat_sources(context, chunks)
    assert [item["type"] for item in sources] == ["transcript", "knowledge_base"]
    assert sources[0]["id"] == "transcript-t1"
    assert sources[1]["id"] == "kb-k1"
    assert sources[1]["knowledge_base_name"] == "临床指南库"
    assert sources[1]["snippet"] == "指南规定随访周期为一个月"


def test_build_chat_sources_falls_back_to_revision_blocks() -> None:
    block = SimpleNamespace(
        text="下一次会议跟进随访结果",
        block_id="block-1",
        speaker="李医生",
        start_ms=65_000,
        end_ms=100_000,
        page_number=None,
        table_markdown=None,
    )
    context = _context(blocks=[block])
    sources = build_chat_sources(context, [])
    assert len(sources) == 1
    assert sources[0]["type"] == "transcript"
    assert sources[0]["timestamp"] == "01:05 - 01:40"
    assert sources[0]["speaker_name"] == "李医生"


def test_build_chat_materials_excludes_none_values() -> None:
    materials = build_chat_materials(
        "问题",
        _context(),
        [
            {
                "index": 1,
                "type": "transcript",
                "title": "片段",
                "content": "正文",
                "speaker_name": None,
            }
        ],
    )
    assert materials["question"] == "问题"
    assert materials["confirmed_minutes"] == "确认版纪要全文"
    registry = materials["source_registry"]
    assert registry[0]["content"] == "正文"
    assert "speaker_name" not in registry[0]


async def test_generate_chat_answer_declines_without_sources() -> None:
    client = MeetingChatModelClient(generator=AsyncMock())
    response = await generate_chat_answer(
        question="问题",
        context=_context(),
        sources=[],
        conversation_id=None,
        model_client=client,
    )
    assert response.status == "INSUFFICIENT_CONTEXT"
    assert response.answer == INSUFFICIENT_ANSWER
    assert response.sources == []
    client.generator.assert_not_awaited()  # type: ignore[union-attr]


async def test_generate_chat_answer_returns_completed_with_sources() -> None:
    async def generator(payload: dict[str, object]) -> str:
        assert payload["question"] == "问题"
        assert payload["source_registry"][0]["content"] == "正文"
        return "结论：先观察一周。 [1]"

    client = MeetingChatModelClient(generator=generator)
    response = await generate_chat_answer(
        question="问题",
        context=_context(),
        sources=[{"index": 1, "type": "transcript", "title": "片段", "content": "正文"}],
        conversation_id=None,
        model_client=client,
    )
    assert response.status == "COMPLETED"
    assert "结论" in response.answer
    assert len(response.sources) == 1
    assert response.sources[0].index == 1


async def test_generate_chat_answer_maps_model_decline_to_insufficient() -> None:
    client = MeetingChatModelClient(generator=AsyncMock(return_value=INSUFFICIENT_ANSWER))
    response = await generate_chat_answer(
        question="问题",
        context=_context(),
        sources=[{"index": 1, "type": "transcript", "title": "片段", "content": "正文"}],
        conversation_id=None,
        model_client=client,
    )
    assert response.status == "INSUFFICIENT_CONTEXT"
    assert response.answer == INSUFFICIENT_ANSWER


async def test_generate_chat_answer_wraps_model_failures() -> None:
    async def broken(_payload: dict[str, object]) -> str:
        raise RuntimeError("boom")

    client = MeetingChatModelClient(generator=broken)
    with pytest.raises(AppException) as excinfo:
        await generate_chat_answer(
            question="问题",
            context=_context(),
            sources=[{"index": 1, "type": "transcript", "title": "片段", "content": "正文"}],
            conversation_id=None,
            model_client=client,
        )
    assert excinfo.value.status_code == 502
    assert excinfo.value.code == "chat_generation_failed"


async def test_chat_model_client_uses_injected_generator(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    async def generator(payload: dict[str, object]) -> str:
        calls.append(payload)
        return "答案"

    client = MeetingChatModelClient(generator=generator)
    assert await client.answer({"question": "问题"}) == "答案"
    assert calls[0]["question"] == "问题"


async def test_chat_model_client_requires_llm_configuration(monkeypatch) -> None:
    settings = SimpleNamespace(
        llm_base_url="",
        resolved_llm_api_key="",
        llm_model="",
    )
    monkeypatch.setattr(
        "app.services.meeting_chat.get_settings",
        lambda: settings,
    )
    client = MeetingChatModelClient()
    with pytest.raises(AppException) as excinfo:
        await client.answer({"question": "问题", "materials": {}})
    assert excinfo.value.status_code == 503
    assert excinfo.value.code == "chat_model_unavailable"


async def test_chat_model_client_serializes_materials_without_nested_key(monkeypatch) -> None:
    """Regression: _invoke must serialize the materials payload directly.

    The chat flow calls ``client.answer(materials)`` where materials already
    contains ``question`` / ``meeting_context`` / ``confirmed_minutes`` /
    ``source_registry``; the previous ``payload['materials']`` lookup raised
    KeyError and surfaced as chat_generation_failed.
    """

    class FakeResponse:
        content = "答案 [1]"

    captured: dict[str, object] = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs: object) -> None:
            captured["kwargs"] = kwargs

        async def ainvoke(self, prompt: str) -> FakeResponse:
            captured["prompt"] = prompt
            return FakeResponse()

    settings = SimpleNamespace(
        llm_base_url="https://api.deepseek.com/v1",
        resolved_llm_api_key="sk-test",
        llm_model="deepseek-test",
    )
    monkeypatch.setattr("app.services.meeting_chat.get_settings", lambda: settings)
    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeChatOpenAI)

    materials = {
        "question": "本次会议有哪些结论？",
        "meeting_context": {"title": "月度病例讨论会"},
        "confirmed_minutes": "确认版纪要全文",
        "source_registry": [
            {
                "index": 1,
                "type": "transcript",
                "title": "片段",
                "content": "张医生建议先观察一周",
            }
        ],
    }
    client = MeetingChatModelClient()
    answer = await client.answer(materials)
    assert answer == "答案 [1]"
    prompt = str(captured["prompt"])
    assert "本次会议有哪些结论？" in prompt
    assert "source_registry" in prompt
    assert "张医生建议先观察一周" in prompt


async def test_rewriter_skips_when_no_history() -> None:
    rewriter = MeetingChatRewriter(generator=AsyncMock())
    result = await rewriter.rewrite(history=[], question="独立问题")
    assert result == "独立问题"
    rewriter.generator.assert_not_awaited()  # type: ignore[union-attr]


async def test_rewriter_returns_rewritten_question() -> None:
    async def generator(prompt: str) -> str:
        assert "对话历史" in prompt
        return "该药物的副作用有哪些？"

    rewriter = MeetingChatRewriter(generator=generator)
    result = await rewriter.rewrite(
        history=[{"question": "剂量是多少？", "answer": "一天两次。"}],
        question="那副作用呢？",
    )
    assert result == "该药物的副作用有哪些？"


async def test_rewriter_falls_back_to_original_question() -> None:
    history = [{"question": "剂量是多少？", "answer": "一天两次。"}]

    async def broken(_prompt: str) -> str:
        raise RuntimeError("boom")

    rewriter = MeetingChatRewriter(generator=broken)
    assert await rewriter.rewrite(history=history, question="那副作用呢？") == "那副作用呢？"

    async def empty(_prompt: str) -> str:
        return "   "

    rewriter = MeetingChatRewriter(generator=empty)
    assert await rewriter.rewrite(history=history, question="那副作用呢？") == "那副作用呢？"

    async def same(_prompt: str) -> str:
        return "那副作用呢？"

    rewriter = MeetingChatRewriter(generator=same)
    assert await rewriter.rewrite(history=history, question="那副作用呢？") == "那副作用呢？"


async def test_rewriter_falls_back_on_overlong_result() -> None:
    async def overlong(_prompt: str) -> str:
        return "很长的改写问题" * 500

    rewriter = MeetingChatRewriter(generator=overlong)
    result = await rewriter.rewrite(
        history=[{"question": "q", "answer": "a"}],
        question="那呢？",
        max_result_chars=100,
    )
    assert result == "那呢？"
