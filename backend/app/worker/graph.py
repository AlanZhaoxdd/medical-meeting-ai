from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any, TypedDict
from uuid import UUID

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.exceptions import AppException
from app.ingestion.chunking import CHUNKER_VERSION, build_chunks, prepare_semantic_units
from app.models.kb import (
    Chunk,
    Document,
    DocumentBlock,
    ExtractionTemplateVersion,
    IngestionJob,
    KnowledgeItem,
    NodeExecution,
    OutboxEvent,
    TranscriptRevision,
    TranscriptRevisionBlock,
)
from app.schemas.kb import DocumentStatus
from app.services.model_client import ModelServiceClient
from app.services.observability import observe
from app.services.storage import ObjectStorage
from app.services.vector_store import VectorStore
from app.worker.extraction import extract_knowledge
from app.worker.meeting_import import document_lock_key
from app.worker.parser import parse_document_bytes
from app.worker.progress import publish_progress


class IngestionState(TypedDict, total=False):
    job_id: str
    document_id: str
    start_node: str
    input_version: str
    status: str
    summary: dict[str, Any]
    revision_id: str
    revision_version: int
    vector_only: bool


NodeHandler = Callable[[AsyncSession, IngestionState], Awaitable[dict[str, Any]]]

PROGRESS = {
    "validate_source": 8,
    "parse_document": 20,
    "normalize_blocks": 32,
    "build_chunks": 45,
    "embed_chunks": 60,
    "extract_knowledge": 75,
    "validate_evidence": 84,
    "publish_document": 97,
    "finalize": 100,
}


def _input_version(
    *,
    sha256: str,
    template_id: str,
    template_version: int,
    embedding_version: str,
    chunker_version: str = CHUNKER_VERSION,
    chunker_config: str = "",
    revision_id: str = "",
    revision_version: int | str = "",
) -> str:
    """Build a fixed-length version identifier for ingestion idempotency.

    The underlying inputs include a 64-character content hash and a UUID, so
    storing their concatenation can exceed a bounded database VARCHAR column.
    A SHA-256 digest preserves deterministic change detection within 64 chars.
    """
    source = "\x1f".join(
        (
            sha256,
            template_id,
            str(template_version),
            embedding_version,
            chunker_version,
            chunker_config,
            revision_id,
            str(revision_version),
        )
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def _checkpoint_url() -> str:
    return get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _document_lock_key(document_id: UUID) -> int:
    """Compatibility alias for the shared revision/vector lock key."""
    return document_lock_key(document_id)


def _vector_revision_is_stale(
    *, expected_version: int | None, current_version: int | None, current_status: str | None
) -> bool:
    """Whether a queued vector job no longer targets the current revision."""
    return (
        expected_version is None
        or current_version != expected_version
        or current_status not in {"DRAFT", "CONFIRMED"}
    )


async def _document(session: AsyncSession, state: IngestionState) -> Document:
    document = await session.get(Document, UUID(state["document_id"]))
    if document is None or document.deleted_at is not None:
        raise AppException(404, "document_not_found", "文档不存在")
    return document


async def _require_current_vector_revision(
    session: AsyncSession, state: IngestionState
) -> None:
    if not state.get("vector_only"):
        return
    revision_id = state.get("revision_id")
    revision = await session.get(TranscriptRevision, UUID(revision_id)) if revision_id else None
    status = (
        revision.status.value
        if revision is not None and hasattr(revision.status, "value")
        else (revision.status if revision is not None else None)
    )
    if revision is None or _vector_revision_is_stale(
        expected_version=state.get("revision_version"),
        current_version=revision.version if revision is not None else None,
        current_status=status,
    ):
        raise AppException(409, "vectorization_superseded", "向量任务修订版本已被更新")


async def _finish_if_vector_revision_superseded(
    session_factory: async_sessionmaker[AsyncSession],
    state: IngestionState,
) -> bool:
    if not state.get("vector_only"):
        return False
    async with session_factory() as session:
        try:
            await _require_current_vector_revision(session, state)
        except AppException as exc:
            if exc.code != "vectorization_superseded":
                raise
            await publish_progress(
                session,
                state["job_id"],
                status="COMPLETED",
                node="finalize",
                progress=100,
                summary={
                    "vector_only": True,
                    "superseded": True,
                    "revision_id": state.get("revision_id"),
                    "revision_version": state.get("revision_version"),
                },
                terminal=True,
            )
            return True
    return False


async def _validate_source(session: AsyncSession, state: IngestionState) -> dict[str, Any]:
    document = await _document(session, state)
    if document.status not in {
        DocumentStatus.UPLOADED.value,
        DocumentStatus.FAILED.value,
        DocumentStatus.AWAITING_REVIEW.value,
        DocumentStatus.IN_REVIEW.value,
        DocumentStatus.PUBLISHED.value,
    }:
        raise AppException(409, "invalid_ingestion_state", "文档当前状态不能执行入库")
    return {"sha256": document.sha256, "version": document.version}


async def _parse_document(session: AsyncSession, state: IngestionState) -> dict[str, Any]:
    document = await _document(session, state)
    document.status = DocumentStatus.PARSING.value
    await session.commit()
    content = await ObjectStorage().get(document.minio_object_key)
    blocks = await parse_document_bytes(content, document.safe_filename, document.source_type)
    if not blocks:
        raise AppException(422, "no_document_content", "文档没有可解析内容")
    await session.execute(delete(DocumentBlock).where(DocumentBlock.document_id == document.id))
    for block in blocks:
        session.add(
            DocumentBlock(
                document_id=document.id,
                block_id=block["block_id"],
                block_type=block["block_type"],
                order=block["order"],
                heading_path=block.get("heading_path", []),
                text=block.get("text", ""),
                table_markdown=block.get("table_markdown"),
                page_number=block.get("page_number"),
                slide_number=block.get("slide_number"),
                speaker=block.get("speaker"),
                start_ms=block.get("start_ms"),
                end_ms=block.get("end_ms"),
                bbox=block.get("bbox"),
                content_hash=block["content_hash"],
            )
        )
    document.status = DocumentStatus.PARSED.value
    await session.commit()
    return {"block_count": len(blocks)}


async def _normalize_blocks(session: AsyncSession, state: IngestionState) -> dict[str, Any]:
    document = await _document(session, state)
    blocks = (
        await session.scalars(select(DocumentBlock).where(DocumentBlock.document_id == document.id))
    ).all()
    invalid = [block.block_id for block in blocks if not block.text.strip()]
    if invalid:
        raise AppException(
            422, "invalid_normalized_blocks", "标准化 Block 包含空正文", {"block_ids": invalid}
        )
    return {"validated_blocks": len(blocks)}


def pooled_dense(indexes: list[int], unit_dense: list[list[float]]) -> list[float]:
    """Average the unit dense vectors that were merged into one chunk."""

    if len(indexes) == 1:
        return unit_dense[indexes[0]]
    dimension = len(unit_dense[indexes[0]])
    sums = [0.0] * dimension
    for index in indexes:
        for offset, value in enumerate(unit_dense[index]):
            sums[offset] += value
    return [value / len(indexes) for value in sums]


def pooled_sparse(
    indexes: list[int], unit_sparse: list[dict[int, float]]
) -> dict[int, float]:
    """Union the unit lexical weights that were merged into one chunk."""

    if len(indexes) == 1:
        return unit_sparse[indexes[0]]
    weights: dict[int, float] = {}
    for index in indexes:
        for token, weight in unit_sparse[index].items():
            weights[token] = weights.get(token, 0.0) + weight
    return weights


def pool_chunk_records(
    document: Document,
    chunks: list[dict[str, Any]],
    unit_dense: list[list[float]],
    unit_sparse: list[dict[int, float]],
    *,
    embedding_identity: str,
    publication_status: str = "DRAFT",
) -> list[dict[str, Any]]:
    """Build Milvus records from one embedding pass over semantic units."""

    records: list[dict[str, Any]] = []
    for chunk in chunks:
        indexes = chunk["unit_indexes"]
        records.append(
            {
                "record_id": chunk["chunk_id"],
                "record_type": "chunk",
                "organization_id": str(document.organization_id),
                "knowledge_base_id": str(document.knowledge_base_id),
                "meeting_id": (
                    str(document.meeting_id) if document.meeting_id else ""
                ),
                "document_id": str(document.id),
                "document_version": document.version,
                "publication_status": publication_status,
                "content_type": chunk["content_type"],
                "dense_vector": pooled_dense(indexes, unit_dense),
                "sparse_vector": pooled_sparse(indexes, unit_sparse),
                "embedding_version": embedding_identity,
            }
        )
    return records


async def upsert_records_batched(
    vector_store: VectorStore,
    records: list[dict[str, Any]],
    *,
    batch_size: int = 256,
) -> int:
    """Write all records with a handful of large upserts instead of one per
    embedding batch, keeping payloads well below Milvus message limits."""

    for start in range(0, len(records), batch_size):
        await vector_store.upsert(records[start : start + batch_size])
    return len(records)


async def _build_chunks(session: AsyncSession, state: IngestionState) -> dict[str, Any]:
    settings = get_settings()
    await _require_current_vector_revision(session, state)
    document = await _document(session, state)
    document.status = DocumentStatus.CHUNKING.value
    blocks: list[Any]
    revision_id = state.get("revision_id")
    if revision_id:
        blocks = list((await session.scalars(
            select(TranscriptRevisionBlock)
            .where(TranscriptRevisionBlock.revision_id == UUID(revision_id))
            .join(TranscriptRevision, TranscriptRevision.id == TranscriptRevisionBlock.revision_id)
            .where(TranscriptRevision.status.in_(["DRAFT", "CONFIRMED"]))
            .order_by(TranscriptRevisionBlock.order)
        )).all())
    elif document.active_transcript_revision_id:
        blocks = list(
            (
                await session.scalars(
                    select(TranscriptRevisionBlock)
                    .where(
                        TranscriptRevisionBlock.revision_id
                        == document.active_transcript_revision_id
                    )
                    .join(
                        TranscriptRevision,
                        TranscriptRevision.id == TranscriptRevisionBlock.revision_id,
                    )
                    .where(TranscriptRevision.status == "CONFIRMED")
                    .order_by(TranscriptRevisionBlock.order)
                )
            ).all()
        )
    else:
        blocks = list(
            (
                await session.scalars(
                    select(DocumentBlock)
                    .where(DocumentBlock.document_id == document.id)
                    .order_by(DocumentBlock.order)
                )
            ).all()
        )
    raw_blocks = [
        {
            "block_id": block.block_id,
            "block_type": block.block_type,
            "order": block.order,
            "heading_path": block.heading_path,
            "text": block.text,
            "table_markdown": block.table_markdown,
            "page_number": block.page_number,
            "slide_number": block.slide_number,
            "speaker": block.speaker,
            "start_ms": block.start_ms,
            "end_ms": block.end_ms,
        }
        for block in blocks
    ]
    semantic_units = prepare_semantic_units(
        raw_blocks,
        settings.chunk_max_tokens,
        settings.model_service_max_input_characters,
    )
    strategy = settings.bge_embedding_strategy
    include_sparse = strategy == "single_pass_pool"
    unit_dense: list[list[float]] = []
    unit_sparse: list[dict[int, float]] = []
    batch_size = max(1, min(settings.bge_batch_size, 128))
    async with ModelServiceClient() as client:
        for start in range(0, len(semantic_units), batch_size):
            embeddings = await client.embeddings(
                [
                    str(unit.get("table_markdown") or unit.get("text", ""))
                    for unit in semantic_units[start : start + batch_size]
                ],
                include_sparse=include_sparse,
            )
            for embedding in embeddings:
                unit_dense.append(
                    [float(value) for value in embedding["dense"]]
                )
                if include_sparse:
                    unit_sparse.append(
                        {
                            int(key): float(value)
                            for key, value in embedding["sparse"].items()
                        }
                    )
    chunks = build_chunks(
        raw_blocks,
        document_id=str(document.id),
        target_tokens=settings.chunk_target_tokens,
        max_tokens=settings.chunk_max_tokens,
        overlap_tokens=settings.chunk_overlap_tokens,
        semantic_vectors=unit_dense,
        similarity_threshold=settings.chunk_similarity_threshold,
        max_unit_characters=settings.model_service_max_input_characters,
        prepared_units=semantic_units,
        include_unit_indexes=include_sparse,
    )
    await _require_current_vector_revision(session, state)
    await session.execute(delete(Chunk).where(Chunk.document_id == document.id))
    if include_sparse:
        await publish_progress(
            session,
            state["job_id"],
            status="RUNNING",
            node="build_chunks",
            progress=55,
            summary={"chunk_count": len(chunks)},
        )
        records = pool_chunk_records(
            document,
            chunks,
            unit_dense,
            unit_sparse,
            embedding_identity=settings.embedding_identity,
            publication_status=("DRAFT" if state.get("vector_only") else "PUBLISHED"),
        )
        vector_store = VectorStore()
        await vector_store.delete_document(str(document.id))
        await upsert_records_batched(vector_store, records)
    for chunk in chunks:
        session.add(
            Chunk(
                chunk_id=chunk["chunk_id"],
                organization_id=document.organization_id,
                knowledge_base_id=document.knowledge_base_id,
                document_id=document.id,
                meeting_id=document.meeting_id,
                chunk_index=chunk["chunk_index"],
                content=chunk["content"],
                heading_path=chunk["heading_path"],
                content_type=chunk["content_type"],
                token_count=chunk["token_count"],
                source_block_ids=chunk["source_block_ids"],
                source_locator=chunk["source_locator"],
                content_hash=chunk["content_hash"],
                embedding_version=settings.embedding_identity,
                chunker_version=chunk["chunker_version"],
                publication_status="DRAFT",
            )
        )
    document.status = DocumentStatus.EMBEDDING.value
    await session.commit()
    return {
        "chunk_count": len(chunks),
        "chunker_version": CHUNKER_VERSION,
        "embedding_strategy": strategy,
    }


async def _embed_chunks(session: AsyncSession, state: IngestionState) -> dict[str, Any]:
    settings = get_settings()
    await _require_current_vector_revision(session, state)
    document = await _document(session, state)
    document.status = DocumentStatus.EMBEDDING.value
    chunks = (
        await session.scalars(
            select(Chunk).where(Chunk.document_id == document.id).order_by(Chunk.chunk_index)
        )
    ).all()
    if settings.bge_embedding_strategy != "single_pass_pool":
        batch_size = max(1, min(settings.bge_batch_size, 128))
        vector_store = VectorStore()
        await vector_store.delete_document(str(document.id))
        all_records: list[dict[str, Any]] = []
        async with ModelServiceClient() as client:
            for start in range(0, len(chunks), batch_size):
                await _require_current_vector_revision(session, state)
                chunk_batch = chunks[start : start + batch_size]
                embeddings = await client.embeddings(
                    [chunk.content for chunk in chunk_batch]
                )
                await _require_current_vector_revision(session, state)
                records = [
                    {
                        "record_id": chunk.chunk_id,
                        "record_type": "chunk",
                        "organization_id": str(document.organization_id),
                        "knowledge_base_id": str(document.knowledge_base_id),
                        "meeting_id": (
                            str(document.meeting_id) if document.meeting_id else ""
                        ),
                        "document_id": str(document.id),
                        "document_version": document.version,
                        "publication_status": (
                            "DRAFT" if state.get("vector_only") else "PUBLISHED"
                        ),
                        "content_type": chunk.content_type,
                        "dense_vector": embedding["dense"],
                        "sparse_vector": {
                            int(key): float(value)
                            for key, value in embedding["sparse"].items()
                        },
                        "embedding_version": settings.embedding_identity,
                    }
                    for chunk, embedding in zip(chunk_batch, embeddings, strict=True)
                ]
                all_records.extend(records)
        await _require_current_vector_revision(session, state)
        record_count = await upsert_records_batched(vector_store, all_records)
    else:
        # single_pass_pool already wrote the pooled vectors to Milvus inside
        # build_chunks; this node finalizes document status after the durable
        # chunk rows exist.
        record_count = len(chunks)
    await _require_current_vector_revision(session, state)
    document.vector_sync_status = "SYNCED"
    document.status = (
        DocumentStatus.AWAITING_REVIEW.value
        if state.get("vector_only")
        else DocumentStatus.EXTRACTING.value
    )
    await session.execute(
        pg_insert(OutboxEvent)
        .values(
            idempotency_key=f"vector.upsert:{document.id}:{settings.embedding_identity}",
            event_type="vector.upsert_document",
            aggregate_id=str(document.id),
            payload={"record_count": record_count, "document_id": str(document.id)},
            status="PROCESSED",
        )
        .on_conflict_do_nothing(index_elements=[OutboxEvent.idempotency_key])
    )
    await session.commit()
    return {
        "embedded_chunks": record_count,
        "embedding_version": settings.embedding_identity,
        "revision_id": state.get("revision_id"),
        "revision_version": (
            await session.scalar(
                select(TranscriptRevision.version).where(
                    TranscriptRevision.id == UUID(state["revision_id"])
                )
            )
            if state.get("revision_id")
            else None
        ),
    }


async def _extract_knowledge(session: AsyncSession, state: IngestionState) -> dict[str, Any]:
    settings = get_settings()
    document = await _document(session, state)
    document.status = DocumentStatus.EXTRACTING.value
    await session.commit()
    chunks = (
        await session.scalars(
            select(Chunk).where(Chunk.document_id == document.id).order_by(Chunk.chunk_index)
        )
    ).all()
    version = await session.scalar(
        select(ExtractionTemplateVersion).where(
            ExtractionTemplateVersion.template_id == document.template_id,
            ExtractionTemplateVersion.version == document.template_version,
        )
    )
    if version is None:
        raise AppException(409, "frozen_template_missing", "任务绑定的模板版本不存在")
    extraction = await extract_knowledge(
        [{"chunk_id": chunk.chunk_id, "content": chunk.content} for chunk in chunks],
        version.fields,
    )
    await session.execute(
        delete(KnowledgeItem).where(
            KnowledgeItem.document_id == document.id,
            KnowledgeItem.publication_status == "DRAFT",
        )
    )
    for item in extraction.items:
        session.add(
            KnowledgeItem(
                organization_id=document.organization_id,
                knowledge_base_id=document.knowledge_base_id,
                document_id=document.id,
                meeting_id=document.meeting_id,
                item_type=item.item_type,
                title=item.title,
                normalized_content=item.normalized_content,
                structured_data=item.structured_data,
                source_refs=[ref.model_dump(mode="json") for ref in item.source_refs],
                confidence=item.confidence,
                extraction_template_id=document.template_id,
                extraction_template_version=document.template_version,
                prompt_version="kb-extraction-v1",
                model_name=settings.llm_model,
                # Review is no longer a user-facing workflow. Keep the legacy
                # column populated for existing data and API compatibility.
                review_status="APPROVED",
                publication_status="DRAFT",
                revision=1,
            )
        )
    await session.commit()
    return {"knowledge_item_count": len(extraction.items)}


async def _validate_evidence(session: AsyncSession, state: IngestionState) -> dict[str, Any]:
    document = await _document(session, state)
    valid_chunk_ids = set(
        (
            await session.scalars(select(Chunk.chunk_id).where(Chunk.document_id == document.id))
        ).all()
    )
    valid_block_ids = set(
        (
            await session.scalars(
                select(DocumentBlock.block_id).where(DocumentBlock.document_id == document.id)
            )
        ).all()
    )
    items = (
        await session.scalars(select(KnowledgeItem).where(KnowledgeItem.document_id == document.id))
    ).all()
    invalid: list[str] = []
    for item in items:
        if not item.source_refs:
            invalid.append(str(item.id))
            continue
        for ref in item.source_refs:
            chunk_valid = ref.get("chunk_id") in valid_chunk_ids
            block_valid = ref.get("block_id") in valid_block_ids if ref.get("block_id") else False
            if not (chunk_valid or block_valid) or not ref.get("quote"):
                invalid.append(str(item.id))
                break
    if invalid:
        raise AppException(
            422,
            "knowledge_evidence_invalid",
            "模型产生了无有效来源的知识项",
            {"item_ids": invalid},
        )
    return {"validated_items": len(items)}


async def _publish_document(session: AsyncSession, state: IngestionState) -> dict[str, Any]:
    document = await _document(session, state)
    # Documents are published automatically after parsing, vectorization and
    # evidence validation. The legacy manual-publish branch below is retained
    # only as a compatibility fallback for old checkpoints.
    document.status = DocumentStatus.PUBLISHED.value
    document.published_at = datetime.now(timezone.utc)
    document.vector_sync_status = "SYNCED"
    await session.execute(
        text(
            "UPDATE chunks SET publication_status = 'PUBLISHED' "
            "WHERE document_id = :document_id"
        ),
        {"document_id": document.id},
    )
    await session.execute(
        text(
            "UPDATE knowledge_items SET review_status = 'APPROVED', "
            "publication_status = 'PUBLISHED' WHERE document_id = :document_id"
        ),
        {"document_id": document.id},
    )
    # The vector rows may have been created as DRAFT during the meeting-import
    # review flow.  Publishing the SQL rows alone leaves Milvus filtered out
    # by question/search retrieval, so reconcile the vector publication state
    # through the outbox after the transaction commits.
    await session.execute(
        pg_insert(OutboxEvent)
        .values(
            idempotency_key=f"vector.publish_document:{document.id}:{document.version}",
            event_type="vector.publish_document",
            aggregate_id=str(document.id),
            payload={
                "document_id": str(document.id),
                "document_version": document.version,
            },
            status="PENDING",
        )
        .on_conflict_do_nothing(index_elements=[OutboxEvent.idempotency_key])
    )
    await session.commit()
    return {"document_status": document.status, "auto_published": True}


async def _finalize(session: AsyncSession, state: IngestionState) -> dict[str, Any]:
    return {"finalized": True}


HANDLERS: dict[str, NodeHandler] = {
    "validate_source": _validate_source,
    "parse_document": _parse_document,
    "normalize_blocks": _normalize_blocks,
    "build_chunks": _build_chunks,
    "embed_chunks": _embed_chunks,
    "extract_knowledge": _extract_knowledge,
    "validate_evidence": _validate_evidence,
    "publish_document": _publish_document,
    "finalize": _finalize,
}


def build_graph(
    session_factory: async_sessionmaker[AsyncSession],
    checkpointer: AsyncPostgresSaver,
) -> Any:
    builder = StateGraph(IngestionState)

    for node_name, handler in HANDLERS.items():

        async def node(
            state: IngestionState,
            *,
            _name: str = node_name,
            _handler: NodeHandler = handler,
        ) -> IngestionState:
            key = f"{state['job_id']}:{_name}:{state['input_version']}"
            async with session_factory() as session:
                existing = await session.scalar(
                    select(NodeExecution).where(NodeExecution.idempotency_key == key)
                )
                if existing is not None:
                    return {"status": _name, "summary": existing.result_summary}
                await publish_progress(
                    session,
                    state["job_id"],
                    status="RUNNING",
                    node=_name,
                    progress=PROGRESS[_name],
                )
                with observe(
                    f"ingestion.{_name}",
                    metadata={
                        "job_id": state["job_id"],
                        "document_id": state["document_id"],
                        "input_version": state["input_version"],
                    },
                ) as observation:
                    summary = await _handler(session, state)
                    observation.update(output=summary)
                session.add(
                    NodeExecution(
                        idempotency_key=key,
                        job_id=state["job_id"],
                        node_name=_name,
                        input_version=state["input_version"],
                        result_summary=summary,
                    )
                )
                await session.commit()
                return {"status": _name, "summary": summary}

        builder.add_node(node_name, node)

    async def review_gate(state: IngestionState) -> IngestionState:
        # Kept as a no-op so jobs created by the former workflow can resume.
        return {"status": "review_gate"}

    builder.add_node("review_gate", review_gate)
    routes = list(HANDLERS) + ["review_gate"]
    builder.add_conditional_edges(
        START,
        lambda state: state.get("start_node", "validate_source"),
        {name: name for name in routes},
    )
    ordered = [
        "validate_source",
        "parse_document",
        "normalize_blocks",
        "build_chunks",
        "embed_chunks",
        "extract_knowledge",
        "validate_evidence",
        "publish_document",
        "finalize",
    ]
    for source, target in zip(ordered, ordered[1:], strict=False):
        if source == "embed_chunks":
            builder.add_conditional_edges(
                source,
                lambda state: END if state.get("vector_only") else "extract_knowledge",
                {"extract_knowledge": "extract_knowledge", END: END},
            )
        else:
            builder.add_edge(source, target)
    # Legacy waiting jobs can still be resumed after the review workflow was
    # removed. New jobs never enter this node.
    builder.add_edge("review_gate", "publish_document")
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer)


async def run_graph(job_id: str, *, resume: bool = False) -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    lock_connection = None
    try:
        async with session_factory() as session:
            job = await session.scalar(
                select(IngestionJob)
                .where(IngestionJob.job_id == job_id)
                .with_for_update()
            )
            if job is None:
                raise AppException(404, "job_not_found", "任务不存在")
            if job.status in {"RUNNING", "COMPLETED"}:
                return
            job.status = "RUNNING"
            await session.commit()
            document = await session.get(Document, job.document_id)
            if document is None:
                raise AppException(404, "document_not_found", "文档不存在")
            job_summary = job.result_summary or {}
            revision_id = str(job_summary.get("revision_id") or "")
            revision_version = int(job_summary.get("revision_version") or 0)
            state = IngestionState(
                job_id=job.job_id,
                document_id=str(document.id),
                start_node=job.current_node,
                input_version=_input_version(
                    sha256=document.sha256,
                    template_id=str(document.template_id),
                    template_version=document.template_version,
                    embedding_version=settings.embedding_identity,
                    chunker_config=(
                        f"{settings.chunk_target_tokens}:"
                        f"{settings.chunk_max_tokens}:"
                        f"{settings.chunk_overlap_tokens}:"
                        f"{settings.chunk_similarity_threshold}"
                    ),
                    revision_id=revision_id,
                    revision_version=revision_version,
                ),
                revision_id=revision_id,
                revision_version=revision_version,
                vector_only=job_summary.get("mode") == "vector_only",
                status=job.status,
                summary={},
            )
            document_lock_key = _document_lock_key(document.id)
        if await _finish_if_vector_revision_superseded(session_factory, state):
            return
        # Poll without retaining a pool connection while another same-document
        # graph is active. Revision writers take the identical transaction lock,
        # closing the freshness-check-to-vector-mutation race as well.
        while lock_connection is None:
            if await _finish_if_vector_revision_superseded(session_factory, state):
                return
            candidate = await engine.connect()
            acquired = bool(
                await candidate.scalar(
                    text("SELECT pg_try_advisory_lock(:lock_key)"),
                    {"lock_key": document_lock_key},
                )
            )
            if acquired:
                lock_connection = candidate
                # pg_try_advisory_lock is session-scoped, so commit the implicit
                # SELECT transaction here. Leaving it open ("idle in transaction")
                # blocks checkpointer.setup()'s CREATE INDEX CONCURRENTLY, which
                # waits for other open transactions -> first-run ingestion hung.
                await lock_connection.commit()
                break
            await candidate.close()
            await asyncio.sleep(0.25)
        if await _finish_if_vector_revision_superseded(session_factory, state):
            return
        async with AsyncPostgresSaver.from_conn_string(_checkpoint_url()) as checkpointer:
            await checkpointer.setup()
            graph = build_graph(session_factory, checkpointer)
            config = {"configurable": {"thread_id": job_id}}
            if resume:
                await graph.ainvoke(Command(resume={"published": True}), config)
                async with session_factory() as session:
                    await publish_progress(
                        session,
                        job_id,
                        status="COMPLETED",
                        node="finalize",
                        progress=100,
                        summary={"published": True},
                        terminal=True,
                    )
            else:
                await graph.ainvoke(state, config)
                if state.get("vector_only"):
                    async with session_factory() as session:
                        await publish_progress(
                            session,
                            job_id,
                            status="COMPLETED",
                            node="embed_chunks",
                            progress=PROGRESS["embed_chunks"],
                            summary={
                                "vector_only": True,
                                "revision_id": state.get("revision_id"),
                                "revision_version": (state.get("summary") or {}).get(
                                    "revision_version", state.get("revision_version")
                                ),
                            },
                            terminal=True,
                        )
                else:
                    async with session_factory() as session:
                        await publish_progress(
                            session,
                            job_id,
                            status="COMPLETED",
                            node="finalize",
                            progress=100,
                            summary={"published": True, "auto_published": True},
                            terminal=True,
                        )
    finally:
        if lock_connection is not None:
            try:
                try:
                    await lock_connection.execute(
                        text("SELECT pg_advisory_unlock(:lock_key)"),
                        {"lock_key": document_lock_key},
                    )
                except Exception:
                    # A broken connection releases session locks server-side;
                    # do not mask the original graph failure during cleanup.
                    pass
            finally:
                await lock_connection.close()
        await engine.dispose()
