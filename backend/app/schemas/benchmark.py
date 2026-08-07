from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.kb import SchemaBase

BenchmarkKind = Literal[
    "retrieval_quality",
    "search_latency",
    "embedding_throughput",
    "ragas_quality",
]


class BenchmarkCreate(SchemaBase):
    kind: BenchmarkKind
    name: str = Field(default="", max_length=200)
    params: dict[str, Any] = Field(default_factory=dict)


class BenchmarkRead(BaseModel):
    id: str
    kind: str
    name: str
    status: str
    progress: int
    message: str
    environment: dict[str, Any]
    params: dict[str, Any]
    metrics: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None


class EnvironmentRead(BaseModel):
    device: str
    embedding_model: str
    embedding_strategy: str
    reranker_model: str
    bge_batch_size: int
