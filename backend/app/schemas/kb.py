from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Role(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    EDITOR = "editor"
    REVIEWER = "reviewer"
    VIEWER = "viewer"


class DocumentStatus(str, Enum):
    """Single lifecycle state for a knowledge-base document.

    A document has exactly one status; review and publication are stages of
    this same machine instead of separate parallel fields.
    """

    UPLOADED = "UPLOADED"
    PARSING = "PARSING"
    PARSED = "PARSED"
    CHUNKING = "CHUNKING"
    EMBEDDING = "EMBEDDING"
    EXTRACTING = "EXTRACTING"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    IN_REVIEW = "IN_REVIEW"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    DELETED = "DELETED"


class ReviewStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    NEEDS_CHANGES = "NEEDS_CHANGES"


class SchemaBase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RegisterRequest(SchemaBase):
    email: str = Field(min_length=5, max_length=320)
    password: str = Field(min_length=10, max_length=256)
    display_name: str = Field(min_length=1, max_length=100)
    organization_name: Optional[str] = Field(default=None, min_length=1, max_length=120)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized:
            raise ValueError("邮箱格式无效")
        return normalized


class LoginRequest(SchemaBase):
    email: str
    password: str


class RefreshRequest(SchemaBase):
    refresh_token: str


class LogoutRequest(SchemaBase):
    refresh_token: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class CurrentUser(BaseModel):
    id: str
    email: str
    display_name: str
    organization_id: str
    role: Role


class MemberCreate(SchemaBase):
    email: str
    role: Role


class MemberUpdate(SchemaBase):
    role: Role


class MemberRead(BaseModel):
    user_id: str
    email: str
    display_name: str
    role: Role
    status: str
    created_at: datetime


class KnowledgeBaseCreate(SchemaBase):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)


class KnowledgeBaseUpdate(SchemaBase):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    default_template_id: Optional[str] = None
    status: Optional[Literal["active", "archived"]] = None


class KnowledgeBaseRead(BaseModel):
    id: str
    organization_id: str
    name: str
    description: str
    default_template_id: Optional[str] = None
    status: str
    document_count: int = 0
    published_knowledge_count: int = 0
    created_by: str
    created_at: datetime
    updated_at: datetime


ALLOWED_TEMPLATE_FIELDS = {
    "participants",
    "topics",
    "insights",
    "consensus",
    "disagreements",
    "evidence_claims",
    "evidence_gaps",
    "action_items",
}


class TemplateCreate(SchemaBase):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    fields: list[str] = Field(min_length=1)

    @field_validator("fields")
    @classmethod
    def validate_fields(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(value))
        invalid = set(normalized) - ALLOWED_TEMPLATE_FIELDS
        if invalid:
            raise ValueError(f"不支持的字段: {', '.join(sorted(invalid))}")
        return normalized


class TemplateRead(BaseModel):
    id: str
    knowledge_base_id: str
    name: str
    description: str
    fields: list[str]
    version: int
    created_at: datetime


class DocumentRead(BaseModel):
    id: str
    organization_id: str
    knowledge_base_id: str
    meeting_id: Optional[str] = None
    filename: str
    safe_filename: str
    mime_type: str
    source_type: str
    sha256: str
    version: int
    previous_version_id: Optional[str] = None
    template_id: str
    template_version: int
    status: DocumentStatus
    vector_sync_status: str
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    published_at: Optional[datetime] = None


class UploadResponse(BaseModel):
    document: DocumentRead
    job_id: Optional[str] = None
    duplicate: bool = False


class JobRead(BaseModel):
    job_id: str
    document_id: str
    status: str
    current_node: str
    progress: int
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    result_summary: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class SourceRef(BaseModel):
    block_id: Optional[str] = None
    chunk_id: Optional[str] = None
    page_number: Optional[int] = None
    slide_number: Optional[int] = None
    speaker: Optional[str] = None
    start_ms: Optional[int] = None
    end_ms: Optional[int] = None
    quote: str = Field(min_length=1)

    @model_validator(mode="after")
    def require_block_or_chunk(self) -> "SourceRef":
        if self.chunk_id is None and self.block_id is None:
            raise ValueError("来源必须引用 block_id 或 chunk_id")
        return self


class KnowledgeItemUpdate(SchemaBase):
    title: Optional[str] = Field(default=None, min_length=1, max_length=500)
    normalized_content: Optional[str] = Field(default=None, min_length=1)
    structured_data: Optional[dict[str, Any]] = None
    source_refs: Optional[list[SourceRef]] = None


class ReviewRequest(SchemaBase):
    status: ReviewStatus
    comment: str = Field(default="", max_length=2000)


class SearchRequest(SchemaBase):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=10, ge=1, le=50)
    content_types: list[str] = Field(default_factory=list)
    meeting_ids: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    include_drafts: bool = False


class SearchResult(BaseModel):
    chunk_id: str
    content: str
    dense_score: float
    sparse_score: float
    fused_score: float
    rerank_score: float
    document_id: str
    filename: str
    document_version: int
    content_type: str
    page_number: Optional[int] = None
    slide_number: Optional[int] = None
    speaker: Optional[str] = None
    time_range: Optional[dict[str, int]] = None
    publication_status: str
    source_locator: dict[str, Any]


class SearchResponse(BaseModel):
    items: list[SearchResult]
    took_ms: int
