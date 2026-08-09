import hashlib
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    app_name: str = "医药会议智能分析平台"
    app_environment: str = "development"
    app_debug: bool = False
    log_level: str = "INFO"
    api_v1_prefix: str = "/api/v1"
    database_url: str = (
        "postgresql+asyncpg://medical:medical@localhost:5432/medical_meeting"
    )
    sql_echo: bool = False
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = ""
    minio_secure: bool = False
    minio_bucket: str = "medical-kb"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"
    model_service_url: str = "http://localhost:8100"
    # BGE runs on CPU in the default compose setup. A large table batch can
    # legitimately take more than one minute to embed.
    model_service_timeout_seconds: float = 300.0
    model_service_max_input_characters: int = 32_000
    embedding_model: str = "BAAI/bge-m3"
    embedding_version: str = "bge-m3-v1"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    milvus_uri: str = "http://localhost:19530"
    milvus_token: str = ""
    milvus_collection: str = "medical_kb_records"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_host: str = ""
    max_upload_bytes: int = 52_428_800
    meeting_import_stale_seconds: int = 3_600
    # Maximum number of persisted chat messages loaded as follow-up history.
    # Only completed user->assistant pairs are used for query rewriting.
    chat_history_max_messages: int = 8
    # Maximum ranked candidates per question type kept in the selectable pool.
    meeting_question_candidate_limit: int = 10
    # Default page size when the frontend opens the candidate picker.
    question_candidate_page_size: int = 5
    chunk_target_tokens: int = 700
    chunk_max_tokens: int = 1000
    chunk_overlap_tokens: int = 100
    chunk_similarity_threshold: float = 0.65
    dense_top_k: int = 50
    sparse_top_k: int = 50
    fusion_top_k: int = 15
    rerank_top_k: int = 5
    bge_device: Literal["cpu", "cuda", "mps"] = "cpu"
    bge_batch_size: int = 8
    bge_max_input_tokens: int = 8192
    # single_pass_pool embeds semantic units once (dense + sparse) and derives
    # chunk vectors by averaging unit dense vectors and unioning lexical
    # weights, avoiding a second full embedding pass. two_pass keeps the
    # original behavior (dense-only unit pass for boundaries, then dense+sparse
    # encoding of every final chunk).
    bge_embedding_strategy: Literal["two_pass", "single_pass_pool"] = (
        "single_pass_pool"
    )

    model_config = SettingsConfigDict(
        # The app is commonly started from ``backend/``, while the documented
        # compose setup keeps the shared .env at the repository root.
        env_file=(PROJECT_ROOT / "backend/.env", PROJECT_ROOT / ".env"),
        extra="ignore",
    )

    @property
    def resolved_llm_api_key(self) -> str:
        """Return the configured key, including common provider fallbacks.

        ``LLM_API_KEY`` remains the canonical project setting. Provider-specific
        variables make local development work without copying a secret into the
        repository's .env file.
        """

        return (
            self.llm_api_key.strip()
            or os.getenv("DEEPSEEK_API_KEY", "").strip()
            or os.getenv("OPENAI_API_KEY", "").strip()
        )

    @property
    def embedding_identity(self) -> str:
        """Version persisted with vectors and tied to the deployed model."""

        identity = (
            f"{self.embedding_version}@{self.embedding_model}:"
            f"{self.bge_embedding_strategy}"
        )
        if len(identity) <= 100:
            return identity
        model_digest = hashlib.sha256(self.embedding_model.encode()).hexdigest()[:16]
        return f"{self.embedding_version[:82]}@{model_digest}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
