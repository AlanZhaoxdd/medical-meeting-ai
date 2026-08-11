from __future__ import annotations

from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

SourceType = Literal[
    "transcript",
    "meeting_summary",
    "historical_meeting",
    "knowledge_base",
    "cutoff_question",
    "open_question",
]


class SourceRegistryItem(BaseModel):
    """One numbered retrievable source passed to the LLM and returned to the UI."""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=1)
    type: SourceType
    title: str = Field(min_length=1, max_length=300)
    snippet: str = Field(default="", max_length=2000)
    speaker_name: Optional[str] = None
    timestamp: Optional[str] = None
    page_number: Optional[int] = None
    chunk_id: Optional[str] = None
    document_id: Optional[UUID] = None
    document_title: Optional[str] = None
    knowledge_base_name: Optional[str] = None
    question_id: Optional[UUID] = None
    block_id: Optional[str] = None


class AnalysisModuleOut(BaseModel):
    """One analysis module; citations must reference source_registry indices."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=500)
    content: Optional[str] = Field(default=None, max_length=20000)
    items: list[str] = Field(default_factory=list, max_length=50)
    citations: list[int] = Field(default_factory=list, max_length=30)
    category: Literal["meeting", "transcript", "questions", "knowledge", "ai"] = "meeting"


class AnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    modules: list[AnalysisModuleOut] = Field(default_factory=list, max_length=20)
    insufficient_notes: list[str] = Field(default_factory=list, max_length=20)


class AnalysisRunRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: UUID
    source_version: int
    modules: list[AnalysisModuleOut]
    sources: list[SourceRegistryItem]
    insufficient_notes: list[str]
    created_at: str


ChatScope = Literal["CURRENT_MEETING", "MEETING_AND_KB"]
ChatStatus = Literal["COMPLETED", "INSUFFICIENT_CONTEXT", "FAILED"]
ChatRoute = Literal["MEETING_GROUNDED", "GENERAL_LLM", "REFUSED"]


def _to_camel(value: str) -> str:
    """Convert snake_case field names to camelCase request keys."""

    head, *rest = value.split("_")
    return head + "".join(part.capitalize() for part in rest)


class MeetingChatRequest(BaseModel):
    """Body of POST /meetings/{meeting_id}/ai-chat.

    The frontend sends camelCase keys (meetingId / conversationId), so both
    spellings are accepted via aliases while the canonical names stay snake_case.
    """

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        alias_generator=_to_camel,
        str_strip_whitespace=True,
    )

    meeting_id: UUID
    conversation_id: Optional[UUID] = None
    question: str = Field(min_length=1, max_length=2000)
    scope: ChatScope = "MEETING_AND_KB"


class MeetingChatSource(SourceRegistryItem):
    """A retrievable source returned with a chat answer, including full text."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=200)
    content: Optional[str] = Field(default=None, max_length=4000)


class MeetingChatResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID
    message_id: UUID
    answer: str = Field(min_length=1, max_length=20000)
    status: ChatStatus
    sources: list[MeetingChatSource] = Field(default_factory=list, max_length=60)
    suggested_questions: list[str] = Field(default_factory=list, max_length=5)
    # Which agent route answered this turn: grounded RAG over the meeting/KB,
    # a direct general-knowledge LLM answer, or an explicit refusal.
    route: Optional[ChatRoute] = None
