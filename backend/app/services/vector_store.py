from __future__ import annotations

from typing import Any

import anyio
from pymilvus import (
    CollectionSchema,
    DataType,
    FieldSchema,
    MilvusClient,
)

from app.core.config import get_settings


def reciprocal_rank_fusion(
    dense_ids: list[str], sparse_ids: list[str], *, k: int = 60
) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranking in (dense_ids, sparse_ids):
        for rank, record_id in enumerate(ranking, start=1):
            scores[record_id] = scores.get(record_id, 0.0) + 1.0 / (k + rank)
    return scores


class VectorStore:
    def __init__(self) -> None:
        settings = get_settings()
        self.collection = settings.milvus_collection
        kwargs: dict[str, Any] = {"uri": settings.milvus_uri}
        if settings.milvus_token:
            kwargs["token"] = settings.milvus_token
        self.client = MilvusClient(**kwargs)
        # Cached per-store so per-batch upserts do not repeat a collection
        # existence RPC for every embedding batch.
        self._collection_ready = False

    def ensure_collection(self, dense_dimension: int = 1024) -> None:
        if self._collection_ready:
            return
        if self.client.has_collection(self.collection):
            self._collection_ready = True
            return
        fields = [
            FieldSchema("record_id", DataType.VARCHAR, is_primary=True, max_length=100),
            FieldSchema("record_type", DataType.VARCHAR, max_length=32),
            FieldSchema("organization_id", DataType.VARCHAR, max_length=36),
            FieldSchema("knowledge_base_id", DataType.VARCHAR, max_length=36),
            FieldSchema("meeting_id", DataType.VARCHAR, max_length=36),
            FieldSchema("document_id", DataType.VARCHAR, max_length=36),
            FieldSchema("document_version", DataType.INT64),
            FieldSchema("publication_status", DataType.VARCHAR, max_length=32),
            FieldSchema("content_type", DataType.VARCHAR, max_length=32),
            FieldSchema("dense_vector", DataType.FLOAT_VECTOR, dim=dense_dimension),
            FieldSchema("sparse_vector", DataType.SPARSE_FLOAT_VECTOR),
            FieldSchema("embedding_version", DataType.VARCHAR, max_length=100),
        ]
        schema = CollectionSchema(fields=fields, enable_dynamic_field=False)
        index_params = self.client.prepare_index_params()
        index_params.add_index(
            field_name="dense_vector",
            index_type="HNSW",
            metric_type="COSINE",
            params={"M": 32, "efConstruction": 200},
        )
        index_params.add_index(
            field_name="sparse_vector",
            index_type="SPARSE_INVERTED_INDEX",
            metric_type="IP",
        )
        self.client.create_collection(
            collection_name=self.collection,
            schema=schema,
            index_params=index_params,
        )
        self._collection_ready = True

    async def upsert(self, records: list[dict[str, Any]]) -> None:
        if not self._collection_ready:
            await anyio.to_thread.run_sync(self.ensure_collection)
        await anyio.to_thread.run_sync(
            self.client.upsert, self.collection, records
        )

    async def delete_document(self, document_id: str) -> None:
        escaped = document_id.replace('"', '\\"')
        exists = await anyio.to_thread.run_sync(
            self.client.has_collection, self.collection
        )
        if not exists:
            return
        await anyio.to_thread.run_sync(
            lambda: self.client.delete(
                self.collection, filter=f'document_id == "{escaped}"'
            )
        )

    async def hybrid_search(
        self,
        *,
        dense_vector: list[float],
        sparse_vector: dict[int, float],
        filter_expression: str,
        dense_limit: int,
        sparse_limit: int,
        fusion_limit: int,
    ) -> list[dict[str, Any]]:
        def search() -> list[dict[str, Any]]:
            output_fields = [
                "record_id",
                "document_id",
                "document_version",
                "publication_status",
                "content_type",
            ]
            dense = self.client.search(
                collection_name=self.collection,
                data=[dense_vector],
                anns_field="dense_vector",
                filter=filter_expression,
                limit=dense_limit,
                search_params={"metric_type": "COSINE", "params": {"ef": 128}},
                output_fields=output_fields,
            )[0]
            sparse = self.client.search(
                collection_name=self.collection,
                data=[sparse_vector],
                anns_field="sparse_vector",
                filter=filter_expression,
                limit=sparse_limit,
                search_params={"metric_type": "IP", "params": {}},
                output_fields=output_fields,
            )[0]
            dense_ids = [str(hit["entity"]["record_id"]) for hit in dense]
            sparse_ids = [str(hit["entity"]["record_id"]) for hit in sparse]
            fused = reciprocal_rank_fusion(dense_ids, sparse_ids)
            candidates: dict[str, dict[str, Any]] = {}
            for hit in dense:
                record_id = str(hit["entity"]["record_id"])
                candidates[record_id] = {
                    "chunk_id": record_id,
                    "dense_score": float(hit["distance"]),
                    "sparse_score": 0.0,
                    **hit["entity"],
                }
            for hit in sparse:
                record_id = str(hit["entity"]["record_id"])
                candidate = candidates.setdefault(
                    record_id,
                    {
                        "chunk_id": record_id,
                        "dense_score": 0.0,
                        **hit["entity"],
                    },
                )
                candidate["sparse_score"] = float(hit["distance"])
            ordered_ids = sorted(
                fused, key=lambda record_id: fused[record_id], reverse=True
            )[:fusion_limit]
            return [
                {**candidates[record_id], "fused_score": fused[record_id]}
                for record_id in ordered_ids
            ]

        return await anyio.to_thread.run_sync(search)
