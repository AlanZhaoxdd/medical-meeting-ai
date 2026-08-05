from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import anyio
from fastapi import FastAPI
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    embedding_model: str = "BAAI/bge-m3"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    device: str = "cpu"
    batch_size: int = 8
    max_input_characters: int = 32_000
    reranker_query_max_length: int = 128
    reranker_max_length: int = 512
    lazy_load: bool = False

    model_config = SettingsConfigDict(env_prefix="BGE_", extra="ignore")


class EmbeddingRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=128)
    include_sparse: bool = True


class RerankRequest(BaseModel):
    query: str = Field(min_length=1)
    documents: list[str] = Field(min_length=1, max_length=128)
    top_k: int = Field(default=10, ge=1, le=128)


settings = Settings()
embedding_model: Any = None
reranker_model: Any = None
model_lock = asyncio.Lock()


def load_models() -> None:
    global embedding_model, reranker_model
    if embedding_model is not None:
        return
    from FlagEmbedding import BGEM3FlagModel, FlagReranker

    use_fp16 = settings.device == "cuda"
    embedding_model = BGEM3FlagModel(
        settings.embedding_model,
        use_fp16=use_fp16,
        device=settings.device,
    )
    reranker_model = FlagReranker(
        settings.reranker_model,
        use_fp16=use_fp16,
        device=settings.device,
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    if not settings.lazy_load:
        await anyio.to_thread.run_sync(load_models)
    yield


app = FastAPI(title="BGE 医药知识库模型服务", version="1.0.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "loaded": embedding_model is not None,
        "embedding_model": settings.embedding_model,
        "reranker_model": settings.reranker_model,
        "device": settings.device,
        "reranker_max_length": settings.reranker_max_length,
    }


@app.post("/v1/embeddings")
async def embeddings(payload: EmbeddingRequest) -> dict[str, Any]:
    if any(len(text) > settings.max_input_characters for text in payload.texts):
        from fastapi import HTTPException

        raise HTTPException(status_code=413, detail="输入文本超过长度限制")
    def encode() -> dict[str, Any]:
        result = embedding_model.encode(
            payload.texts,
            batch_size=settings.batch_size,
            return_dense=True,
            return_sparse=payload.include_sparse,
            return_colbert_vecs=False,
        )
        dense = result["dense_vecs"]
        data = [
            {"index": index, "dense": vector.tolist()}
            for index, vector in enumerate(dense)
        ]
        if payload.include_sparse:
            sparse = result["lexical_weights"]
            for item, weights in zip(data, sparse, strict=True):
                item["sparse"] = {
                    str(key): float(value) for key, value in weights.items()
                }
        return {"data": data, "model": settings.embedding_model}

    async with model_lock:
        await anyio.to_thread.run_sync(load_models)
        return await anyio.to_thread.run_sync(encode)


@app.post("/v1/rerank")
async def rerank(payload: RerankRequest) -> dict[str, Any]:
    def score() -> dict[str, Any]:
        pairs = [[payload.query, document] for document in payload.documents]
        values = reranker_model.compute_score(
            pairs,
            batch_size=settings.batch_size,
            query_max_length=settings.reranker_query_max_length,
            max_length=settings.reranker_max_length,
            normalize=True,
        )
        if not isinstance(values, list):
            values = [values]
        ranking = sorted(
            (
                {"index": index, "score": float(value)}
                for index, value in enumerate(values)
            ),
            key=lambda item: item["score"],
            reverse=True,
        )[: payload.top_k]
        return {"results": ranking, "model": settings.reranker_model}

    async with model_lock:
        await anyio.to_thread.run_sync(load_models)
        return await anyio.to_thread.run_sync(score)
