from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

from app.models.kb import MeetingImportStatus


class MeetingImportConfig(BaseModel):
    max_upload_bytes: int
    allowed_extensions: list[str]
    allowed_mime_types: list[str]
    # Kept as a compatibility alias for early clients.
    mime_types: dict[str, list[str]]
    statuses: list[MeetingImportStatus]
    supports_existing_document: bool = True


class MeetingImportRead(BaseModel):
    import_id: str
    org_id: str
    kb_id: str
    organization_id: str
    knowledge_base_id: str
    document_id: str
    file: dict[str, Any]
    status: MeetingImportStatus
    current_step: str
    progress: int
    failure: dict[str, Any] | None = None
    can_retry: bool
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any]


class BlockEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)
    block_id: str = Field(min_length=1, max_length=100)
    text: str | None = None
    block_type: str | None = None
    heading_path: list[Any] | None = None
    table_markdown: str | None = None


class RevisionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    block_edits: list[BlockEdit] = Field(default_factory=list)


class FindRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1)
    scope: Literal["FULL", "BLOCK"] = "FULL"
    case_sensitive: bool = False
    block_id: str | None = None


class ReplaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["CURRENT", "ALL"] = "CURRENT"
    query: str = Field(min_length=1)
    replacement: str
    scope: Literal["FULL", "BLOCK"] = "FULL"
    case_sensitive: bool = False
    expected_version: int = Field(ge=1)
    block_id: str | None = None
    match_index: int | None = Field(default=None, ge=0)
    preview: bool = False


class UndoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)


class MeetingMetadataPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=255)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    location: str | None = Field(default=None, max_length=500)
    online_url: HttpUrl | None = None
    organizer: str | None = Field(default=None, max_length=255)
    topic: str | None = Field(default=None, max_length=255)
    description: str | None = None
    meeting_purpose: str | None = None
    discussion_topics: str | None = None
    meeting_date: str | None = None
    advisor_selection_criteria: str | None = None
    advisor_names: str | None = None
    internal_attendees: str | None = None
    recorder: str | None = None

    @field_validator("starts_at", "ends_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("时间必须携带时区信息")
        return value


class ConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    expected_metadata_version: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=255)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    location: str | None = Field(default=None, max_length=500)
    online_url: HttpUrl | None = None
    organizer: str | None = Field(default=None, max_length=255)
    topic: str | None = Field(default=None, max_length=255)
    description: str | None = None
    meeting_purpose: str | None = None
    discussion_topics: str | None = None
    meeting_date: str | None = None
    advisor_selection_criteria: str | None = None
    advisor_names: str | None = None
    internal_attendees: str | None = None
    recorder: str | None = None

    @field_validator("starts_at", "ends_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("时间必须携带时区信息")
        return value


class SourceRefRead(BaseModel):
    block_id: str
    page_number: int | None = None
    slide_number: int | None = None
    speaker: str | None = None
    start_ms: int | None = None
    end_ms: int | None = None


class RevisionBlockRead(BaseModel):
    block_id: str
    block_type: str
    order: int
    text: str
    heading_path: list[Any] = Field(default_factory=list)
    table_markdown: str | None = None
    source_ref: SourceRefRead | None = None


class RevisionRead(BaseModel):
    revision_id: UUID
    document_id: UUID
    version: int
    revision_number: int
    status: str
    created_by: UUID
    blocks: list[RevisionBlockRead]
    created_at: datetime
    confirmed_at: datetime | None = None
    confirmed_by: UUID | None = None


class MeetingMetadataField(BaseModel):
    value: Any = None
    suggested_value: Any = None
    confidence: float | str | None = None
    confidence_label: str | None = None
    source: list[dict[str, Any]] = Field(default_factory=list)
    needs_confirmation: bool = False
    user_modified: bool = False


class ReviewRead(BaseModel):
    import_id: UUID
    organization_id: UUID
    knowledge_base_id: UUID
    document: dict[str, Any]
    file: dict[str, Any]
    status: str
    meeting_id: UUID | None = None
    original_blocks: list[RevisionBlockRead]
    current_revision: RevisionRead | None = None
    revision_history: list[RevisionRead] = Field(default_factory=list)
    metadata: dict[str, MeetingMetadataField]
    metadata_version: int
    needs_confirmation_count: int
    vectorization: "VectorizationRead"


class VectorizationRead(BaseModel):
    job_id: str | None = None
    status: Literal["PENDING", "RUNNING", "SYNCED", "STALE", "FAILED"]
    revision_id: UUID | None = None
    current_revision_version: int | None = None
    vectorized_revision_version: int | None = None
    current_node: str | None = None
    progress: int = 0
    error_code: str | None = None
    error_message: str | None = None
    error: dict[str, Any] | None = None
    retryable: bool = False


class VectorizationReadResponse(VectorizationRead):
    import_id: UUID


class VectorizeRequest(BaseModel):
    expected_version: int = Field(ge=1)


class ConfirmRead(BaseModel):
    meeting_id: UUID
    import_id: UUID
    revision_id: UUID
    status: str
    rag_job_id: str | None = None
    rag_status: str | None = None
    rag_error: str | None = None
    rag_retryable: bool = False
    ai_task_id: UUID | None = None
    question_generation_status: str | None = None
    meeting_status: str = "QUESTION_GENERATION_QUEUED"
