from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.api.v1.meeting_imports import _vectorization_status
from app.main import app
from app.schemas.meeting_import import VectorizeRequest
from app.worker.celery_app import celery_app
from app.worker.graph import (
    _document_lock_key,
    _input_version,
    _vector_revision_is_stale,
    pool_chunk_records,
    pooled_dense,
    pooled_sparse,
    upsert_records_batched,
)
from app.worker.tasks import _dispatch_queued_ingestion_jobs


def test_input_version_changes_for_revision() -> None:
    kwargs = dict(
        sha256="a" * 64,
        template_id=str(uuid4()),
        template_version=1,
        embedding_version="bge-v1",
    )
    assert _input_version(**kwargs, revision_id="r1", revision_version=1) != _input_version(
        **kwargs, revision_id="r1", revision_version=2
    )


def test_document_lock_key_is_stable_signed_int64() -> None:
    document_id = uuid4()
    key = _document_lock_key(document_id)
    assert key == _document_lock_key(document_id)
    assert -(2**63) <= key < 2**63


def test_stale_vector_job_is_skipped_after_lock() -> None:
    assert _vector_revision_is_stale(
        expected_version=1, current_version=2, current_status="DRAFT"
    )
    assert not _vector_revision_is_stale(
        expected_version=2, current_version=2, current_status="DRAFT"
    )


def test_review_vectorization_status_is_revision_aware() -> None:
    revision = SimpleNamespace(id=uuid4(), version=2)
    item = SimpleNamespace(id=uuid4())
    document = SimpleNamespace(vector_sync_status="SYNCED")
    job = SimpleNamespace(
        status="COMPLETED",
        progress=60,
        error_code=None,
        error_message=None,
        result_summary={"revision_id": str(revision.id), "revision_version": 1},
    )
    status = _vectorization_status(item, document, revision, job)
    assert status.status == "STALE"
    assert status.retryable is True


def test_vectorization_endpoint_is_in_openapi() -> None:
    assert "/api/v1/meeting-imports/{import_id}/vectorize" in app.openapi()["paths"]
    assert "/api/v1/meeting-imports/{import_id}/vectorization" in app.openapi()["paths"]
    assert VectorizeRequest(expected_version=2).expected_version == 2


def test_queued_ingestion_reconciler_redelivers_durable_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delivered: list[str] = []

    def send_task(_name: str, *, args: list[str]) -> None:
        delivered.extend(args)

    monkeypatch.setattr(celery_app, "send_task", send_task)
    assert _dispatch_queued_ingestion_jobs(["job-1", "job-2"]) == {
        "dispatched": 2,
        "failed": 0,
    }
    assert delivered == ["job-1", "job-2"]
    assert celery_app.conf.beat_schedule["reconcile-queued-ingestion-jobs"]["task"] == (
        "app.worker.tasks.reconcile_ingestion_jobs"
    )


def test_pooled_dense_averages_unit_vectors() -> None:
    assert pooled_dense([0], [[1.0, 2.0]]) == [1.0, 2.0]
    assert pooled_dense([0, 2], [[1.0, 2.0], [9.0, 9.0], [3.0, 4.0]]) == [2.0, 3.0]


def test_pooled_sparse_unions_lexical_weights() -> None:
    weights = pooled_sparse(
        [0, 1],
        [{1: 0.5, 2: 0.3}, {2: 0.4, 3: 0.7}],
    )
    assert weights == {1: 0.5, 2: 0.7, 3: 0.7}
    assert pooled_sparse([0], [{2: 0.4, 3: 0.7}]) == {2: 0.4, 3: 0.7}


def test_pool_chunk_records_builds_milvus_records() -> None:
    document = SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        knowledge_base_id=uuid4(),
        meeting_id=uuid4(),
        version=3,
    )
    chunks = [
        {
            "chunk_id": "chunk-1",
            "content_type": "paragraph",
            "unit_indexes": [0],
        },
        {
            "chunk_id": "chunk-2",
            "content_type": "table",
            "unit_indexes": [1, 2],
        },
    ]

    records = pool_chunk_records(
        document,
        chunks,
        [[1.0, 2.0], [1.0, 0.0], [3.0, 4.0]],
        [{1: 0.5}, {2: 0.3}, {2: 0.4, 3: 0.7}],
        embedding_identity="bge-m3-v1@BAAI/bge-m3:single_pass_pool",
    )

    assert records[0]["record_id"] == "chunk-1"
    assert records[0]["dense_vector"] == [1.0, 2.0]
    assert records[0]["sparse_vector"] == {1: 0.5}
    assert records[1]["record_id"] == "chunk-2"
    assert records[1]["dense_vector"] == [2.0, 2.0]
    assert records[1]["sparse_vector"] == {2: 0.7, 3: 0.7}
    assert records[1]["document_id"] == str(document.id)
    assert records[1]["document_version"] == 3
    assert records[1]["publication_status"] == "DRAFT"
    assert records[1]["embedding_version"] == (
        "bge-m3-v1@BAAI/bge-m3:single_pass_pool"
    )


@pytest.mark.asyncio
async def test_upsert_records_batched_splits_large_payloads() -> None:
    upserted_sizes: list[int] = []

    class Store:
        async def upsert(self, records: list[dict[str, object]]) -> None:
            upserted_sizes.append(len(records))

    published = await upsert_records_batched(Store(), [{}] * 700, batch_size=256)

    assert published == 700
    assert upserted_sizes == [256, 256, 188]
