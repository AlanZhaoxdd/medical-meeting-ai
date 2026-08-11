from __future__ import annotations

from datetime import datetime
from math import ceil
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from app.models.meeting import AnalysisStatus, MeetingStatus, VerificationStatus


class SchemaBase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MeetingCreate(SchemaBase):
    knowledge_base_id: Optional[UUID] = None
    title: str = Field(min_length=1, max_length=255)
    starts_at: datetime
    ends_at: datetime
    location: Optional[str] = Field(default=None, max_length=500)
    online_url: Optional[HttpUrl] = None
    organizer: Optional[str] = Field(default=None, max_length=255)
    topic: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = None
    cover_url: Optional[HttpUrl] = None

    @field_validator("title", "location", "organizer", "topic", "description")
    @classmethod
    def normalize_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        return value

    @field_validator("starts_at", "ends_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("时间必须携带时区信息")
        return value

    @model_validator(mode="after")
    def validate_time_range(self) -> "MeetingCreate":
        if self.ends_at <= self.starts_at:
            raise ValueError("结束时间必须晚于开始时间")
        if not self.title:
            raise ValueError("标题不能为空")
        return self


class MeetingUpdate(SchemaBase):
    knowledge_base_id: Optional[UUID] = None
    title: Optional[str] = Field(default=None, min_length=1, max_length=255)
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    location: Optional[str] = Field(default=None, max_length=500)
    online_url: Optional[HttpUrl] = None
    organizer: Optional[str] = Field(default=None, max_length=255)
    topic: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = None
    cover_url: Optional[HttpUrl] = None

    @field_validator("title", "location", "organizer", "topic", "description")
    @classmethod
    def normalize_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        return value

    @field_validator("starts_at", "ends_at")
    @classmethod
    def require_timezone(cls, value: Optional[datetime]) -> Optional[datetime]:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("时间必须携带时区信息")
        return value

    @model_validator(mode="after")
    def validate_update(self) -> "MeetingUpdate":
        for field_name in ("title", "starts_at", "ends_at"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} 不能为 null")
        if (
            self.starts_at is not None
            and self.ends_at is not None
            and self.ends_at <= self.starts_at
        ):
            raise ValueError("结束时间必须晚于开始时间")
        return self


class MeetingStatusUpdate(SchemaBase):
    meeting_status: MeetingStatus


class MeetingInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meeting_purpose: Optional[str] = None
    discussion_topics: Optional[str] = None
    meeting_date: Optional[str] = None
    advisor_selection_criteria: Optional[str] = None
    advisor_names: Optional[str] = None
    internal_attendees: Optional[str] = None
    recorder: Optional[str] = None


class MeetingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    import_id: Optional[UUID] = None
    organization_id: Optional[UUID]
    knowledge_base_id: Optional[UUID]
    title: str
    starts_at: datetime
    ends_at: datetime
    location: Optional[str]
    online_url: Optional[HttpUrl]
    organizer: Optional[str]
    topic: Optional[str]
    description: Optional[str]
    meeting_info: MeetingInfo = Field(default_factory=MeetingInfo)
    cover_url: Optional[HttpUrl]
    meeting_status: MeetingStatus
    analysis_status: AnalysisStatus
    verification_status: VerificationStatus
    verification_version: int
    verification_confirmed_at: Optional[datetime]
    verification_confirmed_by: Optional[UUID]
    analysis_requested_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime


class MeetingListRead(BaseModel):
    items: list[MeetingRead]
    page: int
    page_size: int
    total: int
    total_pages: int

    @classmethod
    def create(
        cls,
        *,
        items: list[MeetingRead],
        page: int,
        page_size: int,
        total: int,
    ) -> "MeetingListRead":
        return cls(
            items=items,
            page=page,
            page_size=page_size,
            total=total,
            total_pages=ceil(total / page_size) if total else 0,
        )


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: Optional[Any] = None
