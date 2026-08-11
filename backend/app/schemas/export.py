from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SchemaBase(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TextExportSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    title: str
    content: Optional[str] = None
    items: list[str] = Field(default_factory=list)
    citations: list[int] = Field(default_factory=list)


class TextPreviewRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meeting_id: UUID
    meeting_title: str
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None
    location: Optional[str] = None
    organizer: Optional[str] = None
    topic: Optional[str] = None
    analysis_version: int
    include_cover: bool
    sections: list[TextExportSection]
    sources: list[dict[str, Any]] = Field(default_factory=list)


class TextExportCreate(SchemaBase):
    format: Literal["docx", "pdf"]
    file_name: Optional[str] = Field(default=None, max_length=150)
    include_cover: bool = True
    show_attendee_names: bool = True
    include_references: bool = True
    include_citation_markers: bool = True


class PptBulletOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=200)
    sourceIds: list[str] = Field(default_factory=list, max_length=12)


class PptSlideOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pageNumber: int = Field(ge=1, le=12)
    type: str = Field(max_length=40)
    title: str = Field(min_length=1, max_length=200)
    bullets: list[PptBulletOut] = Field(default_factory=list, max_length=8)
    chartIds: list[str] = Field(default_factory=list, max_length=6)
    speakerNotes: Optional[str] = Field(default=None, max_length=1000)


class PptDeckSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    subtitle: Optional[str] = Field(default=None, max_length=500)
    theme: Literal["formal", "minimal"] = "formal"
    # Base LLM outline is 6-8 pages; user-selected chart pages are appended, so
    # allow up to 12 slides.
    slides: list[PptSlideOut] = Field(default_factory=list, min_length=6, max_length=12)


class PptOutlineRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    meeting_id: UUID
    analysis_version: int
    spec: PptDeckSpec
    generated_at: datetime


class PptOutlineRegenerateRequest(SchemaBase):
    page_number: int = Field(ge=1, le=12)
    instruction: Optional[str] = Field(default=None, max_length=500)


class PptExportCreate(SchemaBase):
    file_name: Optional[str] = Field(default=None, max_length=150)
    theme: Literal["formal", "minimal"] = "formal"
    include_charts: bool = True
    include_references: bool = True
    anonymous_attendees: bool = False
    page_count: Literal["auto", "6", "7", "8"] = "auto"
    title: Optional[str] = Field(default=None, max_length=200)
    report_unit: Optional[str] = Field(default=None, max_length=200)
    presenter: Optional[str] = Field(default=None, max_length=200)
    logo_url: Optional[str] = Field(default=None, max_length=2048)
    # Keep this in sync with PptDeckSpec: chart pages can expand the outline
    # beyond the base 6-8 slide deck, up to 12 slides total.
    slides: Optional[list[PptSlideOut]] = Field(default=None, max_length=12)


class ChartEvidenceOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speakerId: Optional[str] = None
    speakerName: Optional[str] = None
    sourceId: str
    timestamp: Optional[str] = None
    snippet: str = ""


class ChartCategoryOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    label: str
    value: float = Field(ge=0)
    lower: Optional[float] = None
    upper: Optional[float] = None
    percentage: Optional[float] = None
    evidence: list[ChartEvidenceOut] = Field(default_factory=list)


class ChartSpecRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    meeting_id: UUID
    analysis_version: int
    type: Literal["bar", "pie"]
    title: str
    subtitle: str
    metric: str
    target_id: Optional[UUID] = None
    target_label: Optional[str] = None
    denominator: Optional[dict[str, Any]] = None
    categories: list[ChartCategoryOut] = Field(default_factory=list)
    validation: dict[str, Any] = Field(default_factory=dict)
    interpretation: Optional[str] = None
    generated_at: str
    template_id: Optional[UUID] = None
    template_version: Optional[int] = None
    cutpoint_key: Optional[str] = None
    indicator_mode: Optional[str] = None
    unit: Optional[str] = None
    count_mode: Optional[Literal["unique_speakers", "evidence_count"]] = None
    bin_definition: list[dict[str, Any]] = Field(default_factory=list)
    valid_observation_count: int = 0
    excluded_observation_count: int = 0
    excluded_reasons: list[dict[str, Any]] = Field(default_factory=list)
    data_origin: Optional[Literal["demo", "model"]] = None


class ChartBin(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=200)
    lower: Optional[float] = None
    upper: Optional[float] = None
    lower_inclusive: bool = True
    upper_inclusive: bool = False

    @model_validator(mode="after")
    def validate_bounds(self) -> "ChartBin":
        if self.lower is None and self.upper is None:
            raise ValueError("区间至少需要一个边界")
        if self.lower is not None and self.upper is not None and self.lower >= self.upper:
            raise ValueError("区间下界必须小于上界")
        if self.lower is None and self.lower_inclusive:
            self.lower_inclusive = False
        if self.upper is None and self.upper_inclusive:
            self.upper_inclusive = False
        return self


class ChartCutpointItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=200)
    question: str = Field(default="", max_length=500)
    chart_title: str = Field(default="", max_length=300)
    aliases: list[str] = Field(default_factory=list, max_length=30)
    indicator: str = Field(min_length=1, max_length=300)
    indicator_options: list[str] = Field(default_factory=list, max_length=10)
    unit: str = Field(max_length=50)
    unit_aliases: list[str] = Field(default_factory=list, max_length=20)
    count_mode: Literal["unique_speakers", "evidence_count"] = "unique_speakers"
    bins: list[ChartBin] = Field(default_factory=list)


class ChartCutpointTemplateRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    template_key: str
    name: str
    description: str
    version: int
    items: list[ChartCutpointItem]
    created_at: datetime


class ChartCutpointTemplateCreate(SchemaBase):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    items: list[ChartCutpointItem] = Field(min_length=11, max_length=11)


class ChartCutpointTemplateVersionCreate(SchemaBase):
    items: list[ChartCutpointItem] = Field(min_length=11, max_length=11)


class ChartPlanCreate(SchemaBase):
    chart_type: Literal["bar", "pie"]
    title: Optional[str] = Field(default=None, max_length=300)
    template_id: Optional[UUID] = None
    template_version: Optional[int] = Field(default=None, ge=1)
    cutpoint_key: Optional[str] = Field(default=None, max_length=80)
    indicator_mode: Optional[str] = Field(default=None, max_length=100)
    count_mode: Optional[Literal["unique_speakers", "evidence_count"]] = None


class ChartSelectionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chart_ids: list[UUID] = Field(default_factory=list, max_length=8)


class ChartSelectionRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chart_ids: list[UUID] = Field(default_factory=list)


class ExportRecordRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    export_id: UUID
    meeting_id: UUID
    analysis_version: int
    export_type: str
    file_format: Optional[str] = None
    status: str
    progress: int = Field(ge=0, le=100)
    current_stage: str
    message: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    file_name: Optional[str] = None
    download_url: Optional[str] = None
    config: dict[str, Any] = Field(default_factory=dict)
    retry_count: int = 0
    created_by: Optional[UUID] = None
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class ExportRecordListRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ExportRecordRead]
    total: int
    page: int
    page_size: int


class ChartPlanStatusRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: UUID
    status: str
    message: Optional[str] = None
