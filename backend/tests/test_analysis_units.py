from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.exceptions import ConflictError
from app.models.meeting import (
    AnalysisStatus,
    Meeting,
    MeetingQuestion,
    MeetingQuestionType,
    VerificationStatus,
)
from app.schemas.analysis import AnalysisModuleOut, AnalysisResult, SourceRegistryItem
from app.services.analysis_model_client import (
    ANALYSIS_PROMPT_VERSION,
    ANALYSIS_SYSTEM_PROMPT,
    AnalysisModelClient,
)
from app.services.analysis_service import (
    analysis_thread_id,
    save_analysis_selection,
    validate_citation_indices,
)
from app.worker.analysis_graph import build_analysis_graph


def _meeting(**kwargs: object) -> Meeting:
    starts_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    values: dict[str, object] = {
        "id": uuid4(),
        "title": "会议",
        "starts_at": starts_at,
        "ends_at": starts_at + timedelta(hours=1),
        "verification_status": VerificationStatus.IN_PROGRESS,
        "verification_version": 3,
        "analysis_status": AnalysisStatus.READY,
    }
    values.update(kwargs)
    return Meeting(**values)


def _question(
    meeting_id, question_type: MeetingQuestionType
) -> MeetingQuestion:
    return MeetingQuestion(
        id=uuid4(),
        meeting_id=meeting_id,
        question_type=question_type,
        content="问题",
        version=1,
        source="ai",
        support_score=0.8,
    )


def test_analysis_prompt_version_is_stable() -> None:
    assert ANALYSIS_PROMPT_VERSION == "meeting-analysis-v4"


def test_analysis_prompt_requires_single_minutes_module() -> None:
    assert "AI 通读纪要" in ANALYSIS_SYSTEM_PROMPT
    assert '"minutes"' in ANALYSIS_SYSTEM_PROMPT
    assert '"ai"' in ANALYSIS_SYSTEM_PROMPT
    for section in (
        "**一、会议概述**",
        "**二、分歧与焦虑**",
        "**三、循证数据解读**",
        "**四、临床用药建议**",
        "**五、专家共识**",
        "**六、行动计划**",
    ):
        assert section in ANALYSIS_SYSTEM_PROMPT
    assert "第一，…第二，…第三，…" in ANALYSIS_SYSTEM_PROMPT
    assert "未明确" in ANALYSIS_SYSTEM_PROMPT
    assert "暂无明确行动项" in ANALYSIS_SYSTEM_PROMPT
    assert "不得编造数值" in ANALYSIS_SYSTEM_PROMPT


def test_analysis_thread_id_is_versioned() -> None:
    meeting_id = uuid4()
    assert analysis_thread_id(meeting_id, 4) == f"meeting:{meeting_id}:analysis:v4"


def test_analysis_module_schema_accepts_empty_module_as_insufficient() -> None:
    module = AnalysisModuleOut(
        id="actions",
        title="行动项",
        content=None,
        items=[],
        citations=[],
    )
    assert module.content is None
    result = AnalysisResult(modules=[module], insufficient_notes=["行动项无依据"])
    assert len(result.modules) == 1
    assert result.insufficient_notes == ["行动项无依据"]


def test_source_registry_requires_numbered_index() -> None:
    source = SourceRegistryItem(
        index=1,
        type="knowledge_base",
        title="指南",
        snippet="摘要",
        chunk_id="c1",
        page_number=3,
    )
    assert source.index == 1
    assert source.type == "knowledge_base"


def test_validate_citation_indices_drops_out_of_range() -> None:
    modules = [
        {
            "id": "summary",
            "title": "摘要",
            "content": "正文",
            "citations": [1, 2, 99, -1],
        }
    ]
    cleaned = validate_citation_indices(modules, source_count=2)
    assert cleaned[0]["citations"] == [1, 2]


@pytest.mark.asyncio
async def test_save_selection_rejects_locked_meeting() -> None:
    meeting = _meeting(analysis_status=AnalysisStatus.QUEUED)
    with pytest.raises(ConflictError):
        await save_analysis_selection(
            AsyncMock(),
            meeting=meeting,
            selected_question_ids=[],
            user_id=uuid4(),
        )


@pytest.mark.asyncio
async def test_save_selection_marks_only_requested_questions() -> None:
    meeting = _meeting()
    cut = _question(meeting.id, MeetingQuestionType.CUT_POINT)
    open_question = _question(meeting.id, MeetingQuestionType.OPEN_ENDED)
    unused = _question(meeting.id, MeetingQuestionType.CUT_POINT)
    session = AsyncMock()
    session.scalars = AsyncMock(
        return_value=SimpleNamespace(all=lambda: [cut, open_question, unused])
    )
    cutpoint_count, open_count = await save_analysis_selection(
        session,
        meeting=meeting,
        selected_question_ids=[cut.id, open_question.id],
        user_id=uuid4(),
    )
    assert cut.analysis_selected is True
    assert open_question.analysis_selected is True
    assert unused.analysis_selected is False
    assert cutpoint_count == 1
    assert open_count == 1
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_analysis_model_client_uses_injected_generator() -> None:
    async def fake_generator(payload: dict) -> AnalysisResult:
        assert payload["source_registry"]
        return AnalysisResult(
            modules=[
                AnalysisModuleOut(
                    id="minutes",
                    title="AI 通读纪要",
                    content="## 会议概况\n摘要正文",
                    citations=[1],
                )
            ],
            insufficient_notes=[],
        )

    client = AnalysisModelClient(generator=fake_generator)
    result = await client.generate(
        {
            "meeting_context": {},
            "source_registry": [
                {
                    "index": 1,
                    "type": "knowledge_base",
                    "title": "指南",
                    "snippet": "摘要",
                }
            ],
        }
    )
    assert result.modules[0].id == "minutes"
    assert result.modules[0].citations == [1]


def test_analysis_graph_exposes_expected_nodes() -> None:
    graph = build_analysis_graph(AsyncMock())
    nodes = set(graph.get_graph().nodes.keys())
    assert {
        "load_meeting_context",
        "retrieve_evidence",
        "rerank_evidence",
        "build_source_registry",
        "generate",
        "validate_and_persist",
    } <= nodes
