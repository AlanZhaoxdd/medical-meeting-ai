from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.meeting import MeetingQuestionType
from app.schemas.meeting import MeetingRead


class ReviewSchemaBase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MeetingQuestionCreate(ReviewSchemaBase):
    question_type: MeetingQuestionType
    content: str = Field(min_length=1, max_length=4000)

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("问题内容不能为空")
        return value


class MeetingQuestionUpdate(ReviewSchemaBase):
    content: str = Field(min_length=1, max_length=4000)
    expected_version: int = Field(ge=1)

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("问题内容不能为空")
        return value


class VerificationMutation(ReviewSchemaBase):
    expected_version: int = Field(ge=1)


class MeetingQuestionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    meeting_id: UUID
    question_type: MeetingQuestionType
    content: str
    source: str
    confidence: Optional[float]
    topic: Optional[str] = None
    rationale: Optional[str] = None
    origin: str = "USER_CREATED"
    review_status: str = "USER_EDITED"
    support_score: Optional[float] = None
    expected_answer_type: Optional[str] = None
    evidence_count: int = 0
    version: int
    created_at: datetime
    updated_at: datetime


class VerificationEligibility(BaseModel):
    model_config = ConfigDict(extra="forbid")

    can_confirm: bool
    can_submit_analysis: bool
    missing_conditions: list[str]


class MeetingVerificationRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meeting: MeetingRead
    cut_point_questions: list[MeetingQuestionRead]
    open_ended_questions: list[MeetingQuestionRead]
    verification_version: int
    eligibility: VerificationEligibility


class AnalysisSubmissionRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verification: MeetingVerificationRead
    message: str


class QuestionGenerationRead(BaseModel):
    task_id: UUID
    status: str
    current_stage: str
    progress: int = Field(ge=0, le=100)
    message: Optional[str] = None
    cutpoint_count: int = 0
    open_question_count: int = 0
    error_message: Optional[str] = None
    retry_count: int = 0


class QuestionEvidenceRead(BaseModel):
    document_title: str | None = None
    section_title: str | None = None
    quote: str
    evidence_summary: str
    chunk_text: str
    vector_score: Optional[float] = None
    keyword_score: Optional[float] = None
    rerank_score: Optional[float] = None


class QuestionGenerationRetryRead(QuestionGenerationRead):
    pass
