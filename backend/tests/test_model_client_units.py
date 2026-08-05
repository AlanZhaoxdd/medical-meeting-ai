from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from app.core.exceptions import AppException
from app.services.model_client import ModelServiceClient


def _mock_async_client(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> list[httpx.AsyncClient]:
    real_async_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    clients: list[httpx.AsyncClient] = []

    def factory(*, timeout: float) -> httpx.AsyncClient:
        client = real_async_client(transport=transport, timeout=timeout)
        clients.append(client)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return clients


@pytest.mark.asyncio
async def test_embeddings_sends_include_sparse_and_closes_standalone_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "dense": [1.0, 2.0]}]},
            request=request,
        )

    clients = _mock_async_client(monkeypatch, handler)
    result = await ModelServiceClient().embeddings(["语义分块"], include_sparse=False)

    assert result == [{"index": 0, "dense": [1.0, 2.0]}]
    assert len(requests) == 1
    assert requests[0].url.path == "/v1/embeddings"
    assert json.loads(requests[0].content) == {
        "texts": ["语义分块"],
        "include_sparse": False,
    }
    assert len(clients) == 1
    assert clients[0].is_closed


@pytest.mark.asyncio
async def test_context_reuses_one_client_for_multiple_model_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v1/embeddings":
            return httpx.Response(
                200,
                json={"data": [{"index": 0, "dense": [1.0], "sparse": {}}]},
                request=request,
            )
        return httpx.Response(200, json={"results": [{"index": 0, "score": 1.0}]}, request=request)

    clients = _mock_async_client(monkeypatch, handler)
    client = ModelServiceClient()
    async with client:
        assert await client.embeddings(["first"]) == [{"index": 0, "dense": [1.0], "sparse": {}}]
        assert await client.rerank("query", ["document"], top_k=1) == [{"index": 0, "score": 1.0}]
        assert len(clients) == 1
        assert not clients[0].is_closed

    assert len(requests) == 2
    assert [request.url.path for request in requests] == ["/v1/embeddings", "/v1/rerank"]
    embedding_payload = json.loads(requests[0].content)
    assert isinstance(embedding_payload, dict)
    assert embedding_payload["include_sparse"] is True
    assert clients[0].is_closed


@pytest.mark.asyncio
async def test_context_client_is_closed_when_request_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request)

    clients = _mock_async_client(monkeypatch, handler)

    with pytest.raises(AppException):
        async with ModelServiceClient() as client:
            await client.embeddings(["failure"])

    assert len(clients) == 1
    assert clients[0].is_closed
