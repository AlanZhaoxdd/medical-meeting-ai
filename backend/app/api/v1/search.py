from __future__ import annotations

import hashlib
import time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import ROLE_LEVEL, CurrentUserDependency, require_kb_access
from app.core.config import get_settings
from app.core.exceptions import ForbiddenError
from app.db.session import get_session
from app.models.kb import Chunk, Document, RetrievalLog
from app.schemas.kb import Role, SearchRequest, SearchResponse, SearchResult
from app.services.audit import record_audit
from app.services.model_client import ModelServiceClient
from app.services.observability import observe
from app.services.vector_store import VectorStore

router = APIRouter(prefix="/knowledge-bases/{kb_id}", tags=["检索"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]


def _escaped(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _milvus_filter(
    organization_id: UUID, kb_id: UUID, payload: SearchRequest
) -> str:
    filters = [
        f'organization_id == "{organization_id}"',
        f'knowledge_base_id == "{kb_id}"',
    ]
    if not payload.include_drafts:
        filters.append('publication_status == "PUBLISHED"')
    if payload.content_types:
        values = ", ".join(f'"{_escaped(value)}"' for value in payload.content_types)
        filters.append(f"content_type in [{values}]")
    if payload.meeting_ids:
        values = ", ".join(f'"{_escaped(value)}"' for value in payload.meeting_ids)
        filters.append(f"meeting_id in [{values}]")
    if payload.document_ids:
        values = ", ".join(f'"{_escaped(value)}"' for value in payload.document_ids)
        filters.append(f"document_id in [{values}]")
    return " and ".join(filters)


@router.post("/search", response_model=SearchResponse)
async def search(
    kb_id: UUID,
    payload: SearchRequest,
    session: SessionDependency,
    current: CurrentUserDependency,
) -> SearchResponse:
    started = time.perf_counter()
    await require_kb_access(session, current, kb_id)
    if payload.include_drafts and ROLE_LEVEL[current.role] < ROLE_LEVEL[Role.EDITOR]:
        await record_audit(
            session,
            organization_id=current.organization_id,
            actor_id=current.user_id,
            action="search.draft_denied",
            resource_type="knowledge_base",
            resource_id=str(kb_id),
        )
        await session.commit()
        raise ForbiddenError("当前角色不能检索暂存内容")
    settings = get_settings()
    query_hash = hashlib.sha256(payload.query.encode()).hexdigest()
    with observe(
        "retrieval.embed_query",
        metadata={"knowledge_base_id": str(kb_id), "query_hash": query_hash},
    ) as observation:
        query_embedding = (await ModelServiceClient().embeddings([payload.query]))[0]
        observation.update(
            output={
                "dense_dimensions": len(query_embedding["dense"]),
                "sparse_dimensions": len(query_embedding["sparse"]),
            }
        )
    with observe(
        "retrieval.hybrid_search",
        metadata={
            "knowledge_base_id": str(kb_id),
            "dense_limit": settings.dense_top_k,
            "sparse_limit": settings.sparse_top_k,
            "fusion_limit": settings.fusion_top_k,
        },
    ) as observation:
        candidates = await VectorStore().hybrid_search(
            dense_vector=query_embedding["dense"],
            sparse_vector={
                int(key): float(value)
                for key, value in query_embedding["sparse"].items()
            },
            filter_expression=_milvus_filter(current.organization_id, kb_id, payload),
            dense_limit=settings.dense_top_k,
            sparse_limit=settings.sparse_top_k,
            fusion_limit=settings.fusion_top_k,
        )
        observation.update(
            output={
                "candidate_count": len(candidates),
                "ranking": [candidate["chunk_id"] for candidate in candidates],
            }
        )
    candidate_ids = [candidate["chunk_id"] for candidate in candidates]
    if not candidate_ids:
        return SearchResponse(items=[], took_ms=int((time.perf_counter() - started) * 1000))
    statement = (
        select(Chunk, Document)
        .join(Document, Document.id == Chunk.document_id)
        .where(
            Chunk.chunk_id.in_(candidate_ids),
            Chunk.organization_id == current.organization_id,
            Chunk.knowledge_base_id == kb_id,
            Document.organization_id == current.organization_id,
            Document.knowledge_base_id == kb_id,
            Document.deleted_at.is_(None),
        )
    )
    if not payload.include_drafts:
        latest_versions = (
            select(
                Document.safe_filename.label("filename"),
                func.max(Document.version).label("version"),
            )
            .where(
                Document.organization_id == current.organization_id,
                Document.knowledge_base_id == kb_id,
                Document.status == "PUBLISHED",
                Document.deleted_at.is_(None),
            )
            .group_by(Document.safe_filename)
            .subquery()
        )
        statement = statement.join(
            latest_versions,
            (Document.safe_filename == latest_versions.c.filename)
            & (Document.version == latest_versions.c.version),
        )
    rows = (await session.execute(statement)).all()
    authoritative = {chunk.chunk_id: (chunk, document) for chunk, document in rows}
    if not payload.include_drafts:
        authoritative = {
            key: value
            for key, value in authoritative.items()
            if value[0].publication_status == "PUBLISHED"
            and value[1].status == "PUBLISHED"
        }
    ordered = [item for item in candidates if item["chunk_id"] in authoritative]
    if not ordered:
        return SearchResponse(items=[], took_ms=int((time.perf_counter() - started) * 1000))
    rerank_limit = min(len(ordered), payload.top_k, settings.rerank_top_k)
    rerank_candidates = ordered[:rerank_limit]
    with observe(
        "retrieval.rerank",
        metadata={
            "knowledge_base_id": str(kb_id),
            "candidate_count": len(rerank_candidates),
            "top_k": rerank_limit,
        },
    ) as observation:
        reranked = await ModelServiceClient().rerank(
            payload.query,
            [
                authoritative[item["chunk_id"]][0].content
                for item in rerank_candidates
            ],
            rerank_limit,
        )
        observation.update(
            output={
                "ranking": [
                    {
                        "chunk_id": rerank_candidates[int(item["index"])]["chunk_id"],
                        "score": float(item["score"]),
                    }
                    for item in reranked
                ]
            }
        )
    results: list[SearchResult] = []
    for rerank in reranked:
        index = int(rerank["index"])
        candidate = rerank_candidates[index]
        chunk, document = authoritative[candidate["chunk_id"]]
        locator = chunk.source_locator
        results.append(
            SearchResult(
                chunk_id=chunk.chunk_id,
                content=chunk.content,
                dense_score=float(candidate.get("dense_score", 0.0)),
                sparse_score=float(candidate.get("sparse_score", 0.0)),
                fused_score=float(candidate.get("fused_score", 0.0)),
                rerank_score=float(rerank["score"]),
                document_id=str(document.id),
                filename=document.filename,
                document_version=document.version,
                content_type=chunk.content_type,
                page_number=locator.get("page_number"),
                slide_number=locator.get("slide_number"),
                speaker=locator.get("speaker"),
                time_range=locator.get("time_range"),
                publication_status=chunk.publication_status,
                source_locator=locator,
            )
        )
    elapsed = int((time.perf_counter() - started) * 1000)
    session.add(
        RetrievalLog(
            organization_id=current.organization_id,
            knowledge_base_id=kb_id,
            user_id=current.user_id,
            query_hash=query_hash,
            filters=payload.model_dump(exclude={"query"}),
            candidates=[
                {"chunk_id": result.chunk_id, "rerank_score": result.rerank_score}
                for result in results
            ],
            included_drafts=payload.include_drafts,
            latency_ms=elapsed,
        )
    )
    if payload.include_drafts:
        await record_audit(
            session,
            organization_id=current.organization_id,
            actor_id=current.user_id,
            action="search.include_drafts",
            resource_type="knowledge_base",
            resource_id=str(kb_id),
        )
    await session.commit()
    return SearchResponse(items=results, took_ms=elapsed)
