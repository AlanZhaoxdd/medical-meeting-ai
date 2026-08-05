from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.meeting import MeetingQuestionType


class RetrievalQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=3, max_length=1000)
    purpose: str = Field(min_length=1, max_length=500)
    topic: str = Field(min_length=1, max_length=200)
    keywords: list[str] = Field(default_factory=list, max_length=20)
    question_type: MeetingQuestionType
    top_k: int = Field(default=12, ge=1, le=30)


class RetrievalPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    meeting_topics: list[str] = Field(default_factory=list, max_length=20)
    medical_entities: list[str] = Field(default_factory=list, max_length=30)
    study_names: list[str] = Field(default_factory=list, max_length=20)
    drug_names: list[str] = Field(default_factory=list, max_length=20)
    cutpoint_queries: list[RetrievalQuery] = Field(default_factory=list, max_length=10)
    open_question_queries: list[RetrievalQuery] = Field(default_factory=list, max_length=10)
    suggested_specialties: list[str] = Field(default_factory=list, max_length=10)
    version: str = Field(default="retrieval-plan-v2", min_length=1, max_length=64)

    @model_validator(mode="after")
    def require_queries(self) -> "RetrievalPlan":
        if not self.cutpoint_queries and not self.open_question_queries:
            raise ValueError("检索计划至少需要一条查询")
        if any(q.question_type is not MeetingQuestionType.CUT_POINT for q in self.cutpoint_queries):
            raise ValueError("切点检索计划包含错误题型")
        if any(
            q.question_type is not MeetingQuestionType.OPEN_ENDED
            for q in self.open_question_queries
        ):
            raise ValueError("开放性检索计划包含错误题型")
        return self


class EvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    chunk_id: str = Field(min_length=1, max_length=100)
    document_id: UUID
    block_id: str | None = Field(default=None, max_length=100)
    quote: str = Field(min_length=1, max_length=2000)
    evidence_summary: str = Field(min_length=1, max_length=300)


ExpectedAnswerType = Literal[
    "NUMBER",
    "PERCENTAGE",
    "DOSAGE",
    "PRICE",
    "DATE",
    "TIME_POINT",
    "GRADE",
    "THRESHOLD",
    "TERM",
    "DISCUSSION",
]


class GeneratedQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question_type: MeetingQuestionType
    content: str = Field(min_length=5, max_length=300)
    topic: str = Field(min_length=1, max_length=100)
    rationale: str = Field(min_length=1, max_length=500)
    expected_answer_type: ExpectedAnswerType
    evidence: list[EvidenceReference] = Field(min_length=1, max_length=10)
    support_level: Literal["HIGH", "MEDIUM", "LOW"]
    support_score: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_answer_type(self) -> "GeneratedQuestion":
        if (
            self.question_type is MeetingQuestionType.CUT_POINT
            and self.expected_answer_type == "DISCUSSION"
        ):
            raise ValueError("切点问题不能使用 DISCUSSION")
        if (
            self.question_type is MeetingQuestionType.OPEN_ENDED
            and self.expected_answer_type != "DISCUSSION"
        ):
            raise ValueError("开放性问题必须使用 DISCUSSION")
        return self


class QuestionBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    questions: list[GeneratedQuestion] = Field(default_factory=list, max_length=16)


class QuestionReviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_index: int = Field(ge=0)
    decision: Literal["pass", "reject", "revise"]
    reason: str = Field(min_length=1, max_length=500)


class QualityReview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviews: list[QuestionReviewItem]
