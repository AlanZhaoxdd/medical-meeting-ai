from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.models.kb import Chunk, Document, MeetingImport, TranscriptRevision
from app.models.meeting import (
    AiTask,
    AiTaskStatus,
    AiTaskType,
    Meeting,
    MeetingQuestion,
    MeetingQuestionType,
    QuestionEvidence,
)
from app.schemas.question_generation import GeneratedQuestion
from app.services.model_client import ModelServiceClient
from app.services.vector_store import VectorStore


def semantic_deduplicate(
    items: list[dict[str, Any]], embeddings: list[list[float]], threshold: float = 0.92
) -> list[dict[str, Any]]:
    """Deterministic cosine deduplication; callers must supply real embeddings."""
    kept: list[dict[str, Any]] = []
    vectors: list[list[float]] = []
    for item, vector in zip(items, embeddings, strict=False):
        norm = sum(value * value for value in vector) ** 0.5
        if not norm:
            kept.append(item)
            vectors.append(vector)
            continue
        duplicate = False
        for previous in vectors:
            previous_norm = sum(value * value for value in previous) ** 0.5
            if (
                previous_norm
                and sum(a * b for a, b in zip(vector, previous, strict=False))
                / (norm * previous_norm)
                >= threshold
            ):
                duplicate = True
                break
        if not duplicate:
            kept.append(item)
            vectors.append(vector)
    return kept


_PUNCTUATION = str.maketrans({
    "，": ",",
    "。": ".",
    "？": "?",
    "！": "!",
    "：": ":",
    "；": ";",
    "（": "(",
    "）": ")",
})


def normalize_question_text(value: str) -> str:
    normalized = value.strip().lower().translate(_PUNCTUATION)
    return re.sub(r"\s+", "", normalized)


def has_required_question_types(cutpoint_count: int, open_count: int) -> bool:
    return cutpoint_count > 0 and open_count > 0


def validate_candidate_questions(
    questions: list[dict[str, Any]],
    *,
    available_chunks: list[dict[str, Any]],
    existing_contents: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Program-only validation and evidence enrichment before LLM review."""
    available = {str(item["chunk_id"]): item for item in available_chunks}
    seen = set(existing_contents or set())
    accepted: list[dict[str, Any]] = []
    errors: list[str] = []
    for index, raw in enumerate(questions):
        try:
            question = GeneratedQuestion.model_validate(raw)
        except ValueError:
            errors.append(f"schema_invalid:{index}")
            continue
        normalized = normalize_question_text(question.content)
        if not normalized or normalized in seen:
            errors.append(f"duplicate_question:{index}")
            continue
        if question.question_type.value == "open_ended" and re.match(
            r"^(是否|有没有|多少|几|何时|哪一|哪个)", question.content
        ):
            errors.append(f"open_question_too_factual:{index}")
            continue
        evidence: list[dict[str, Any]] = []
        seen_evidence: set[str] = set()
        for reference in question.evidence:
            source = available.get(reference.chunk_id)
            if source is None or reference.chunk_id in seen_evidence:
                continue
            if str(source["document_id"]) != str(reference.document_id):
                continue
            if reference.quote not in str(source["content"]):
                continue
            evidence.append(
                {
                    **reference.model_dump(mode="json"),
                    "document_id": source["document_id"],
                    "block_id": reference.block_id or source.get("block_id"),
                    "retrieval_query": source.get("query_source"),
                    "vector_score": source.get("dense_score"),
                    "keyword_score": source.get("sparse_score"),
                    "rerank_score": source.get("rerank_score"),
                    "source_type": source.get("source_type", "knowledge_base"),
                }
            )
            seen_evidence.add(reference.chunk_id)
        if not evidence:
            errors.append(f"invalid_evidence:{index}")
            continue
        item = question.model_dump(mode="json")
        item["evidence"] = evidence
        accepted.append(item)
        seen.add(normalized)
    return accepted, errors


async def retrieve_authoritative_chunks(
    session: AsyncSession,
    *,
    query: str,
    organization_id: UUID,
    knowledge_base_id: UUID,
    model_client: ModelServiceClient | Any | None = None,
    vector_store: VectorStore | Any | None = None,
    top_k: int = 8,
) -> list[dict[str, Any]]:
    """Milvus candidate search followed by authoritative PostgreSQL hydration."""
    client = model_client or ModelServiceClient()
    store = vector_store or VectorStore()
    if isinstance(client, ModelServiceClient):
        async with client as embedding_client:
            embedding = (await embedding_client.embeddings([query]))[0]
    else:
        embedding = (await client.embeddings([query]))[0]
    filter_expression = (
        f'organization_id == "{organization_id}" and '
        f'knowledge_base_id == "{knowledge_base_id}" and publication_status == "PUBLISHED"'
    )
    hits = await store.hybrid_search(
        dense_vector=embedding["dense"],
        sparse_vector={int(k): float(v) for k, v in embedding.get("sparse", {}).items()},
        filter_expression=filter_expression,
        dense_limit=top_k,
        sparse_limit=top_k,
        fusion_limit=top_k,
    )
    ids = [str(hit.get("chunk_id") or hit.get("record_id")) for hit in hits]
    if not ids:
        return []
    newer_document = aliased(Document)
    rows = (
        await session.execute(
            select(Chunk, Document)
            .join(Document, Document.id == Chunk.document_id)
            .where(
                Chunk.chunk_id.in_(ids),
                Chunk.organization_id == organization_id,
                Chunk.knowledge_base_id == knowledge_base_id,
                Chunk.publication_status == "PUBLISHED",
                Document.organization_id == organization_id,
                Document.knowledge_base_id == knowledge_base_id,
                Document.status == "PUBLISHED",
                Document.deleted_at.is_(None),
                ~exists(
                    select(newer_document.id).where(
                        newer_document.organization_id == organization_id,
                        newer_document.knowledge_base_id == knowledge_base_id,
                        newer_document.safe_filename == Document.safe_filename,
                        newer_document.version > Document.version,
                        newer_document.status == "PUBLISHED",
                        newer_document.deleted_at.is_(None),
                    )
                ),
            )
        )
    ).all()
    by_id = {chunk.chunk_id: (chunk, document) for chunk, document in rows}
    hydrated: list[dict[str, Any]] = []
    for hit in hits:
        chunk = by_id.get(str(hit.get("chunk_id") or hit.get("record_id")))
        if chunk is None:
            continue
        row, document = chunk
        hydrated.append(
            {
                "chunk_id": row.chunk_id,
                "document_id": row.document_id,
                "document_title": document.filename,
                "section_title": (row.heading_path or [None])[0],
                "block_id": str((row.source_block_ids or [""])[0]) or None,
                "content": row.content,
                "dense_score": hit.get("dense_score"),
                "sparse_score": hit.get("sparse_score"),
                "fused_score": hit.get("fused_score"),
                "query_source": query,
            }
        )
    return hydrated


async def retrieve_confirmed_transcript_chunks(
    session: AsyncSession,
    *,
    query: str,
    organization_id: UUID,
    meeting_id: UUID,
    confirmed_document_id: UUID,
    knowledge_base_id: UUID,
    source_version: int,
    model_client: ModelServiceClient | Any | None = None,
    vector_store: VectorStore | Any | None = None,
    top_k: int = 8,
) -> list[dict[str, Any]]:
    """Retrieve only the exact confirmed transcript for one question task.

    This deliberately uses a separate DRAFT search path. SQL hydration remains
    authoritative and requires the confirmed import, revision, document, org,
    KB, meeting, and source version to all match.
    """
    client = model_client or ModelServiceClient()
    store = vector_store or VectorStore()
    if isinstance(client, ModelServiceClient):
        async with client as embedding_client:
            embedding = (await embedding_client.embeddings([query]))[0]
    else:
        embedding = (await client.embeddings([query]))[0]
    filter_expression = (
        f'organization_id == "{organization_id}" and '
        f'knowledge_base_id == "{knowledge_base_id}" and '
        f'document_id == "{confirmed_document_id}" and publication_status == "DRAFT"'
    )
    hits = await store.hybrid_search(
        dense_vector=embedding["dense"],
        sparse_vector={int(k): float(v) for k, v in embedding.get("sparse", {}).items()},
        filter_expression=filter_expression,
        dense_limit=top_k,
        sparse_limit=top_k,
        fusion_limit=top_k,
    )
    ids = [str(hit.get("chunk_id") or hit.get("record_id")) for hit in hits]
    if not ids:
        return []
    rows = (
        await session.execute(
            select(Chunk, Document, TranscriptRevision)
            .join(Document, Document.id == Chunk.document_id)
            .join(
                TranscriptRevision,
                TranscriptRevision.id == Document.active_transcript_revision_id,
            )
            .join(MeetingImport, MeetingImport.confirmed_revision_id == TranscriptRevision.id)
            .where(
                Chunk.chunk_id.in_(ids),
                Chunk.organization_id == organization_id,
                Chunk.knowledge_base_id == knowledge_base_id,
                Chunk.publication_status == "DRAFT",
                Document.id == confirmed_document_id,
                Document.organization_id == organization_id,
                Document.knowledge_base_id == knowledge_base_id,
                Document.meeting_id == meeting_id,
                Document.deleted_at.is_(None),
                TranscriptRevision.version == source_version,
                TranscriptRevision.status == "CONFIRMED",
                MeetingImport.meeting_id == meeting_id,
                MeetingImport.document_id == Document.id,
                MeetingImport.organization_id == organization_id,
                MeetingImport.knowledge_base_id == knowledge_base_id,
                MeetingImport.status == "CONFIRMED",
                MeetingImport.confirmed_revision_id == TranscriptRevision.id,
            )
        )
    ).all()
    by_id = {chunk.chunk_id: (chunk, document) for chunk, document, _revision in rows}
    return [
        {
            "chunk_id": chunk.chunk_id,
            "document_id": chunk.document_id,
            "document_title": document.filename,
            "section_title": (chunk.heading_path or [None])[0],
            "block_id": str((chunk.source_block_ids or [""])[0]) or None,
            "content": chunk.content,
            "dense_score": hit.get("dense_score"),
            "sparse_score": hit.get("sparse_score"),
            "fused_score": hit.get("fused_score"),
            "query_source": query,
            "source_type": "confirmed_transcript",
        }
        for hit in hits
        if (pair := by_id.get(str(hit.get("chunk_id") or hit.get("record_id")))) is not None
        for chunk, document in [pair]
    ]


async def rerank_chunks(
    query: str,
    chunks: list[dict[str, Any]],
    *,
    model_client: Any | None = None,
    top_k: int = 8,
) -> list[dict[str, Any]]:
    if not chunks:
        return []
    client = model_client or ModelServiceClient()
    if isinstance(client, ModelServiceClient):
        async with client as rerank_client:
            scores = await rerank_client.rerank(
                query, [str(item["content"]) for item in chunks], top_k
            )
    else:
        scores = await client.rerank(query, [str(item["content"]) for item in chunks], top_k)
    ordered: list[dict[str, Any]] = []
    for result in scores:
        index = int(result.get("index", result.get("document_index", 0)))
        if 0 <= index < len(chunks):
            ordered.append(
                {
                    **chunks[index],
                    "rerank_score": float(result.get("relevance_score", result.get("score", 0.0))),
                }
            )
    return ordered[:top_k]


async def rehydrate_authoritative_chunks(
    session: AsyncSession,
    *,
    candidates: list[dict[str, Any]],
    organization_id: UUID,
    knowledge_base_id: UUID,
) -> list[dict[str, Any]]:
    """Revalidate authorization/publication immediately before an external model call."""
    ids = {str(item["chunk_id"]) for item in candidates}
    if not ids:
        return []
    newer_document = aliased(Document)
    rows = (
        await session.execute(
            select(Chunk, Document)
            .join(Document, Document.id == Chunk.document_id)
            .where(
                Chunk.chunk_id.in_(ids),
                Chunk.organization_id == organization_id,
                Chunk.knowledge_base_id == knowledge_base_id,
                Chunk.publication_status == "PUBLISHED",
                Document.organization_id == organization_id,
                Document.knowledge_base_id == knowledge_base_id,
                Document.status == "PUBLISHED",
                Document.deleted_at.is_(None),
                ~exists(
                    select(newer_document.id).where(
                        newer_document.organization_id == organization_id,
                        newer_document.knowledge_base_id == knowledge_base_id,
                        newer_document.safe_filename == Document.safe_filename,
                        newer_document.version > Document.version,
                        newer_document.status == "PUBLISHED",
                        newer_document.deleted_at.is_(None),
                    )
                ),
            )
        )
    ).all()
    authoritative = {chunk.chunk_id: (chunk, document) for chunk, document in rows}
    refreshed: list[dict[str, Any]] = []
    for candidate in candidates:
        pair = authoritative.get(str(candidate["chunk_id"]))
        if pair is None:
            continue
        chunk, document = pair
        refreshed.append(
            {
                **candidate,
                "document_id": chunk.document_id,
                "document_title": document.filename,
                "section_title": (chunk.heading_path or [None])[0],
                "block_id": str((chunk.source_block_ids or [""])[0]) or None,
                "content": chunk.content,
            }
        )
    return refreshed


async def rehydrate_confirmed_transcript_chunks(
    session: AsyncSession,
    *,
    candidates: list[dict[str, Any]],
    organization_id: UUID,
    meeting_id: UUID,
    confirmed_document_id: UUID,
    knowledge_base_id: UUID,
    source_version: int,
) -> list[dict[str, Any]]:
    ids = {str(item["chunk_id"]) for item in candidates}
    if not ids:
        return []
    rows = (
        await session.execute(
            select(Chunk, Document)
            .join(Document, Document.id == Chunk.document_id)
            .join(
                TranscriptRevision,
                TranscriptRevision.id == Document.active_transcript_revision_id,
            )
            .join(MeetingImport, MeetingImport.confirmed_revision_id == TranscriptRevision.id)
            .where(
                Chunk.chunk_id.in_(ids),
                Chunk.organization_id == organization_id,
                Chunk.knowledge_base_id == knowledge_base_id,
                Chunk.publication_status == "DRAFT",
                Document.organization_id == organization_id,
                Document.knowledge_base_id == knowledge_base_id,
                Document.meeting_id == meeting_id,
                Document.id == confirmed_document_id,
                Document.deleted_at.is_(None),
                TranscriptRevision.version == source_version,
                TranscriptRevision.status == "CONFIRMED",
                MeetingImport.meeting_id == meeting_id,
                MeetingImport.document_id == Document.id,
                MeetingImport.organization_id == organization_id,
                MeetingImport.knowledge_base_id == knowledge_base_id,
                MeetingImport.status == "CONFIRMED",
            )
        )
    ).all()
    authoritative = {chunk.chunk_id: (chunk, document) for chunk, document in rows}
    return [
        {
            **candidate,
            "document_id": chunk.document_id,
            "document_title": document.filename,
            "section_title": (chunk.heading_path or [None])[0],
            "block_id": str((chunk.source_block_ids or [""])[0]) or None,
            "content": chunk.content,
            "source_type": "confirmed_transcript",
        }
        for candidate in candidates
        if (pair := authoritative.get(str(candidate["chunk_id"]))) is not None
        for chunk, document in [pair]
    ]


def thread_id(meeting_id: UUID, source_version: int) -> str:
    return f"meeting:{meeting_id}:question-generation:v{source_version}"


async def claim_task(
    session: AsyncSession, task_id: UUID, *, lease_seconds: int = 1800
) -> tuple[AiTask | None, UUID]:
    token = UUID(bytes=secrets.token_bytes(16))
    now = datetime.now(timezone.utc)
    result = await session.execute(
        update(AiTask)
        .where(
            AiTask.id == task_id,
            or_(
                AiTask.status.in_([AiTaskStatus.QUEUED, AiTaskStatus.RETRYING]),
                and_(
                    AiTask.status == AiTaskStatus.RUNNING,
                    or_(AiTask.lease_expires_at.is_(None), AiTask.lease_expires_at < now),
                ),
            ),
        )
        .values(
            status=AiTaskStatus.RUNNING,
            attempt_token=token,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            started_at=now,
            current_stage="load_meeting_context",
        )
    )
    if cast(Any, result).rowcount != 1:
        return None, token
    await session.commit()
    return await session.get(AiTask, task_id), token


async def get_or_create_task(
    session: AsyncSession,
    *,
    meeting_id: UUID,
    organization_id: UUID,
    source_version: int,
) -> AiTask:
    existing = await session.scalar(
        select(AiTask).where(
            AiTask.meeting_id == meeting_id,
            AiTask.task_type == AiTaskType.QUESTION_GENERATION,
            AiTask.source_version == source_version,
        )
    )
    if existing is not None:
        return existing
    task = AiTask(
        meeting_id=meeting_id,
        organization_id=organization_id,
        task_type=AiTaskType.QUESTION_GENERATION,
        source_version=source_version,
        thread_id=thread_id(meeting_id, source_version),
    )
    session.add(task)
    await session.flush()
    return task


def validate_evidence(
    evidence: list[dict[str, Any]],
    *,
    valid_chunks: dict[str, Chunk],
    organization_id: UUID,
    knowledge_base_id: UUID,
) -> list[dict[str, Any]]:
    """Return only evidence whose chunk belongs to the current published scope.

    This intentionally validates both the retrieval id and the authoritative
    PostgreSQL row; Milvus results are never treated as source text.
    """
    valid: list[dict[str, Any]] = []
    for item in evidence:
        chunk_id = str(item.get("chunk_id", ""))
        quote = str(item.get("quote", "")).strip()
        chunk = valid_chunks.get(chunk_id)
        if chunk is None or not quote:
            continue
        if item.get("document_id") and str(chunk.document_id) != str(item["document_id"]):
            continue
        if chunk.organization_id != organization_id or chunk.knowledge_base_id != knowledge_base_id:
            continue
        if chunk.publication_status != "PUBLISHED":
            continue
        if quote not in chunk.content:
            continue
        valid.append({**item, "chunk_id": chunk_id, "quote": quote})
    return valid


def validate_confirmed_transcript_evidence(
    evidence: list[dict[str, Any]],
    *,
    valid_chunks: dict[str, Chunk],
    organization_id: UUID,
    knowledge_base_id: UUID,
    meeting_id: UUID,
    confirmed_document_id: UUID,
) -> list[dict[str, Any]]:
    """Validate evidence against the explicitly authorized confirmed transcript."""
    valid: list[dict[str, Any]] = []
    for item in evidence:
        chunk_id = str(item.get("chunk_id", ""))
        quote = str(item.get("quote", "")).strip()
        chunk = valid_chunks.get(chunk_id)
        if (
            chunk is None
            or not quote
            or chunk.publication_status != "DRAFT"
            or chunk.organization_id != organization_id
            or chunk.knowledge_base_id != knowledge_base_id
            or str(chunk.document_id) != str(confirmed_document_id)
            or str(item.get("document_id")) != str(confirmed_document_id)
            or quote not in chunk.content
        ):
            continue
        valid.append(
            {
                **item,
                "chunk_id": chunk_id,
                "quote": quote,
                "source_type": "confirmed_transcript",
            }
        )
    return valid


async def persist_questions(
    session: AsyncSession,
    *,
    task: AiTask,
    questions: list[dict[str, Any]],
    evidence_by_index: dict[int, list[dict[str, Any]]],
    valid_chunks: dict[str, Chunk],
    confirmed_chunk_ids: set[str] | None = None,
    confirmed_document_id: UUID | None = None,
    candidate_limit: int | None = None,
) -> tuple[int, int]:
    meeting = await session.get(Meeting, task.meeting_id)
    if meeting is None:
        raise NotFoundError("会议", "meeting_not_found")
    cutpoint_count = 0
    open_count = 0
    for index, item in enumerate(questions):
        content = str(item.get("content", "")).strip()
        if not content:
            continue
        try:
            structured = GeneratedQuestion.model_validate(
                {
                    **item,
                    "evidence": [
                        {
                            key: value
                            for key, value in evidence.items()
                            if key
                            in {
                                "chunk_id",
                                "document_id",
                                "block_id",
                                "quote",
                                "evidence_summary",
                            }
                        }
                        for evidence in item.get("evidence", [])
                    ],
                }
            )
        except ValueError:
            continue
        qtype = structured.question_type
        # Never overwrite manually edited/confirmed rows; duplicates are ignored.
        existing = await session.scalar(
            select(MeetingQuestion).where(
                MeetingQuestion.meeting_id == task.meeting_id,
                MeetingQuestion.question_type == qtype,
                MeetingQuestion.deleted_at.is_(None),
                func.lower(func.trim(MeetingQuestion.content)) == content.lower(),
            )
        )
        if existing is not None:
            continue
        q = MeetingQuestion(
            meeting_id=task.meeting_id,
            question_type=qtype,
            content=content,
            source="ai",
            topic=structured.topic,
            rationale=structured.rationale,
            confidence=structured.support_score,
            support_score=structured.support_score,
            expected_answer_type=structured.expected_answer_type,
            origin="AI_GENERATED",
            review_status="AI_DRAFT",
            generated_task_id=task.id,
        )
        session.add(q)
        await session.flush()
        confirmed_ids = confirmed_chunk_ids or set()
        valid = (
            validate_confirmed_transcript_evidence(
                evidence_by_index.get(index, [e.model_dump() for e in structured.evidence]),
                valid_chunks=valid_chunks,
                organization_id=task.organization_id,
                knowledge_base_id=meeting.knowledge_base_id,
                meeting_id=task.meeting_id,
                confirmed_document_id=confirmed_document_id,
            )
            if confirmed_document_id is not None
            and meeting.knowledge_base_id is not None
            and any(
                str(evidence.get("chunk_id")) in confirmed_ids
                and evidence.get("source_type") == "confirmed_transcript"
                for evidence in evidence_by_index.get(index, [])
            )
            else (
            validate_evidence(
                evidence_by_index.get(index, [e.model_dump() for e in structured.evidence]),
                valid_chunks=valid_chunks,
                organization_id=task.organization_id,
                knowledge_base_id=meeting.knowledge_base_id,
            )
                if meeting.knowledge_base_id is not None
                else []
            )
        )
        if not valid:
            await session.delete(q)
            continue
        for evidence in valid:
            session.add(
                QuestionEvidence(
                    question_id=q.id,
                    chunk_id=evidence["chunk_id"],
                    document_id=evidence.get("document_id"),
                    block_id=evidence.get("block_id"),
                    retrieval_query=evidence.get("retrieval_query"),
                    quote=evidence["quote"],
                    evidence_summary=evidence.get("evidence_summary") or evidence.get("summary"),
                    relevance_score=evidence.get("relevance_score"),
                    vector_score=evidence.get("vector_score"),
                    keyword_score=evidence.get("keyword_score"),
                    rerank_score=evidence.get("rerank_score"),
                    metadata_json=evidence.get("metadata", {}),
                    source_type=evidence.get("source_type", "knowledge_base"),
                )
            )
        q.evidence_count = len(valid)
        if qtype.value == "cut_point":
            cutpoint_count += 1
        else:
            open_count += 1
    limit = (
        candidate_limit
        if candidate_limit is not None
        else get_settings().meeting_question_candidate_limit
    )
    for qtype in (MeetingQuestionType.CUT_POINT, MeetingQuestionType.OPEN_ENDED):
        rows = list(
            (
                await session.scalars(
                    select(MeetingQuestion)
                    .where(
                        MeetingQuestion.meeting_id == task.meeting_id,
                        MeetingQuestion.question_type == qtype,
                        MeetingQuestion.deleted_at.is_(None),
                        MeetingQuestion.source == "ai",
                    )
                    .order_by(
                        MeetingQuestion.support_score.desc().nullslast(),
                        MeetingQuestion.created_at.asc(),
                    )
                )
            ).all()
        )
        for rank, row in enumerate(rows[:limit], start=1):
            row.candidate_rank = rank
        for row in rows[limit:]:
            row.candidate_rank = None
    return cutpoint_count, open_count


async def update_task_progress(
    session: AsyncSession,
    *,
    task_id: UUID,
    attempt_token: UUID | None,
    stage: str,
    progress: int,
    message: str,
    status: AiTaskStatus | None = None,
    retry_count: int | None = None,
) -> None:
    conditions: list[Any] = [AiTask.id == task_id]
    if attempt_token is not None:
        conditions.append(AiTask.attempt_token == attempt_token)
    values: dict[str, Any] = {
        "current_stage": stage,
        "progress": progress,
        "message": message,
        "lease_expires_at": datetime.now(timezone.utc) + timedelta(minutes=30),
    }
    if status is not None:
        values["status"] = status
    if retry_count is not None:
        values["retry_count"] = retry_count
    await session.execute(
        update(AiTask)
        .where(*conditions)
        .values(**values)
    )
    await session.commit()


async def mark_task_failed(session: AsyncSession, task: AiTask, exc: Exception) -> None:
    task.status = AiTaskStatus.FAILED
    task.error_code = str(getattr(exc, "code", "question_generation_failed"))
    task.error_message = str(exc)[:2000]
    task.completed_at = datetime.now(timezone.utc)
    await session.commit()
