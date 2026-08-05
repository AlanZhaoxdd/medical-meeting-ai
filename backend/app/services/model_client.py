from __future__ import annotations

from types import TracebackType
from typing import Any, cast

import httpx

from app.core.config import get_settings
from app.core.exceptions import AppException


class ModelServiceClient:
    def __init__(self) -> None:
        settings = get_settings()
        self.base_url = settings.model_service_url.rstrip("/")
        self.timeout = settings.model_service_timeout_seconds
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> ModelServiceClient:
        self._client = httpx.AsyncClient(timeout=self.timeout)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _post(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        if self._client is not None:
            return await self._client.post(f"{self.base_url}{path}", json=payload)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            return await client.post(f"{self.base_url}{path}", json=payload)

    async def embeddings(
        self, texts: list[str], include_sparse: bool = True
    ) -> list[dict[str, Any]]:
        try:
            response = await self._post(
                "/v1/embeddings",
                {"texts": texts, "include_sparse": include_sparse},
            )
            response.raise_for_status()
            data = response.json()["data"]
            if not isinstance(data, list):
                raise TypeError("embedding data must be a list")
            return cast(list[dict[str, Any]], data)
        except (httpx.HTTPError, KeyError, TypeError) as exc:
            raise AppException(503, "embedding_service_unavailable", "向量模型服务不可用") from exc

    async def rerank(self, query: str, documents: list[str], top_k: int) -> list[dict[str, Any]]:
        try:
            response = await self._post(
                "/v1/rerank",
                {"query": query, "documents": documents, "top_k": top_k},
            )
            response.raise_for_status()
            results = response.json()["results"]
            if not isinstance(results, list):
                raise TypeError("rerank results must be a list")
            return cast(list[dict[str, Any]], results)
        except (httpx.HTTPError, KeyError, TypeError) as exc:
            raise AppException(503, "reranker_service_unavailable", "重排模型服务不可用") from exc
