from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.worker import tasks


@pytest.mark.asyncio
async def test_publish_vectors_respects_model_batch_limit(monkeypatch) -> None:
    requested_sizes: list[int] = []
    upserted_sizes: list[int] = []

    class ModelClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def embeddings(self, texts: list[str]):
            requested_sizes.append(len(texts))
            return [
                {"dense": [float(index)], "sparse": {"1": 1.0}}
                for index, _text in enumerate(texts)
            ]

    class Store:
        async def upsert(self, records):
            upserted_sizes.append(len(records))

    monkeypatch.setattr(tasks, "ModelServiceClient", ModelClient)
    monkeypatch.setattr(tasks, "VectorStore", Store)

    document = SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        knowledge_base_id=uuid4(),
        meeting_id=None,
        version=1,
    )
    chunks = [
        SimpleNamespace(
            chunk_id=f"chunk-{index}",
            content=f"content-{index}",
            content_type="paragraph",
        )
        for index in range(257)
    ]

    published = await tasks._publish_chunk_vectors(
        document,
        chunks,
        batch_size=128,
        embedding_identity="test-v1@BAAI/test",
    )

    assert requested_sizes == [128, 128, 1]
    assert upserted_sizes == [128, 128, 1]
    assert published == 257
