from __future__ import annotations

import re
from datetime import datetime, timezone
from operator import add
from typing import Annotated, Any, TypedDict, cast
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import AppException
from app.models.kb import (
    Chunk,
    Document,
    KnowledgeBase,
    MeetingImport,
    TranscriptRevision,
    TranscriptRevisionBlock,
)
from app.models.meeting import (
    AiTask,
    AiTaskStatus,
    AnalysisStatus,
    Meeting,
    MeetingQuestion,
    MeetingQuestionType,
)
from app.schemas.question_generation import RetrievalPlan
from app.services.model_client import ModelServiceClient
from app.services.question_generation import (
    has_required_question_types,
    normalize_question_text,
    persist_questions,
    rehydrate_authoritative_chunks,
    rehydrate_confirmed_transcript_chunks,
    rerank_chunks,
    retrieve_authoritative_chunks,
    retrieve_confirmed_transcript_chunks,
    semantic_deduplicate,
    update_task_progress,
    validate_candidate_questions,
)
from app.services.question_model_client import QuestionGenerationModelClient


class QuestionGenerationState(TypedDict, total=False):
    task_id: str
    meeting_id: str
    org_id: str
    user_id: str
    attempt_token: str
    kb_ids: list[str]
    source_minutes_version: int
    meeting_context: dict[str, Any]
    retrieval_plan: dict[str, Any]
    cutpoint_retrieved_chunks: list[dict[str, Any]]
    open_retrieved_chunks: list[dict[str, Any]]
    cutpoint_questions: list[dict[str, Any]]
    open_questions: list[dict[str, Any]]
    final_questions: list[dict[str, Any]]
    evidence_by_index: dict[int, list[dict[str, Any]]]
    validation_errors: list[str]
    retry_count: int
    max_retries: int
    status: str
    current_stage: str
    progress: int
    logs: Annotated[list[str], add]
    error_code: str | None
    error_message: str | None


def route_after_validation(state: QuestionGenerationState) -> str:
    if not state.get("validation_errors"):
        return "persist_questions"
    if state.get("retry_count", 0) < state.get("max_retries", 2):
        return "refine_retrieval_plan"
    return "mark_failed"


def validate_plan_grounding(plan: RetrievalPlan, meeting_context: dict[str, Any]) -> None:
    grounding = " ".join(
        str(meeting_context.get(key) or "")
        for key in ("title", "topic", "description", "confirmed_minutes")
    ).lower()
    def is_grounded(entity: str) -> bool:
        candidate = re.sub(r"\s+", "", entity).lower()
        if not candidate:
            return True
        if candidate in grounding:
            return True
        # Chinese model outputs often add a descriptive suffix (for example
        # “临床应用” or “管理指南”) to a real entity. Accept a meaningful
        # contiguous anchor while still rejecting wholly unrelated entities.
        for token in re.findall(r"[a-z0-9][a-z0-9./+%-]{1,}|[\u4e00-\u9fff]{2,}", candidate):
            if token in grounding:
                return True
            if re.fullmatch(r"[\u4e00-\u9fff]{2,}", token) and any(
                token[index : index + 2] in grounding
                for index in range(len(token) - 1)
            ):
                return True
        return False

    ungrounded = [
        entity
        for entity in [
            *plan.medical_entities,
            *plan.study_names,
            *plan.drug_names,
            *(query.topic for query in plan.cutpoint_queries),
            *(query.topic for query in plan.open_question_queries),
        ]
        if entity.strip() and not is_grounded(entity.strip())
    ]
    if ungrounded:
        raise AppException(
            502,
            "retrieval_plan_ungrounded",
            "检索计划包含会议中未出现的医学实体",
            {"entity_count": len(ungrounded)},
        )


def build_question_graph(
    session_factory: async_sessionmaker[AsyncSession],
    checkpointer: Any = None,
    *,
    model_client: QuestionGenerationModelClient | None = None,
    retriever: Any | None = None,
    reranker: Any | None = None,
    embedder: Any | None = None,
) -> Any:
    model = model_client or QuestionGenerationModelClient()
    builder = StateGraph(QuestionGenerationState)

    async def set_progress(
        state: QuestionGenerationState,
        stage: str,
        progress: int,
        message: str,
        *,
        status: AiTaskStatus | None = None,
        retry_count: int | None = None,
    ) -> QuestionGenerationState:
        async with session_factory() as session:
            await update_task_progress(
                session,
                task_id=UUID(state["task_id"]),
                attempt_token=UUID(state["attempt_token"]) if state.get("attempt_token") else None,
                stage=stage,
                progress=progress,
                message=message,
                status=status,
                retry_count=retry_count,
            )
        return {"current_stage": stage, "progress": progress, "logs": [message]}

    async def load_meeting_context(state: QuestionGenerationState) -> QuestionGenerationState:
        async with session_factory() as session:
            task = await session.get(AiTask, UUID(state["task_id"]))
            meeting = await session.get(Meeting, UUID(state["meeting_id"]))
            org_id = UUID(state["org_id"])
            if task is None or meeting is None or meeting.deleted_at is not None:
                raise AppException(404, "meeting_context_not_found", "会议上下文不存在")
            if (
                task.meeting_id != meeting.id
                or task.organization_id != org_id
                or meeting.organization_id != org_id
            ):
                raise AppException(403, "meeting_context_scope_mismatch", "会议任务组织范围不一致")
            if meeting.knowledge_base_id is None:
                raise AppException(409, "meeting_kb_missing", "会议未绑定知识库")
            kb = await session.scalar(
                select(KnowledgeBase).where(
                    KnowledgeBase.id == meeting.knowledge_base_id,
                    KnowledgeBase.organization_id == org_id,
                    KnowledgeBase.deleted_at.is_(None),
                    KnowledgeBase.status == "active",
                )
            )
            if kb is None:
                raise AppException(403, "meeting_kb_forbidden", "会议知识库不可用")
            revision = await session.scalar(
                select(TranscriptRevision)
                .join(MeetingImport, MeetingImport.confirmed_revision_id == TranscriptRevision.id)
                .where(
                    MeetingImport.meeting_id == meeting.id,
                    MeetingImport.organization_id == org_id,
                    MeetingImport.knowledge_base_id == kb.id,
                    MeetingImport.status == "CONFIRMED",
                    TranscriptRevision.status == "CONFIRMED",
                    TranscriptRevision.version == task.source_version,
                )
            )
            if revision is None:
                raise AppException(409, "confirmed_revision_required", "仅确认版纪要可生成问题")
            blocks = (
                await session.scalars(
                    select(TranscriptRevisionBlock)
                    .where(TranscriptRevisionBlock.revision_id == revision.id)
                    .order_by(TranscriptRevisionBlock.order)
                )
            ).all()
            minutes = "\n".join(block.text.strip() for block in blocks if block.text.strip())
            if not minutes:
                raise AppException(422, "confirmed_minutes_empty", "确认版会议纪要为空")
            context = {
                "title": meeting.title,
                "date": meeting.starts_at.isoformat(),
                "location": meeting.location,
                "topic": meeting.topic,
                "description": meeting.description,
                "meeting_info": meeting.meeting_info,
                "confirmed_minutes": minutes,
                "organization_id": str(org_id),
                "knowledge_base_id": str(kb.id),
                "source_version": revision.version,
                "confirmed_revision_id": str(revision.id),
                "confirmed_document_id": str(revision.document_id),
                "confirmed_import_id": str(
                    await session.scalar(
                        select(MeetingImport.id).where(
                            MeetingImport.confirmed_revision_id == revision.id,
                            MeetingImport.meeting_id == meeting.id,
                            MeetingImport.organization_id == org_id,
                            MeetingImport.knowledge_base_id == kb.id,
                            MeetingImport.status == "CONFIRMED",
                        )
                    )
                ),
            }
        progress = await set_progress(state, "LOADING_MEETING", 5, "已加载确认版会议纪要")
        return {**progress, "meeting_context": context, "kb_ids": [str(kb.id)]}

    async def build_retrieval_plan(state: QuestionGenerationState) -> QuestionGenerationState:
        plan = await model.build_plan(
            {
                "meeting_context": {
                    key: value
                    for key, value in state["meeting_context"].items()
                    if key != "confirmed_minutes"
                },
                "confirmed_minutes": state["meeting_context"]["confirmed_minutes"],
            }
        )
        validate_plan_grounding(plan, state["meeting_context"])
        progress = await set_progress(state, "PLANNING_RETRIEVAL", 15, "已生成知识检索计划")
        return {**progress, "retrieval_plan": plan.model_dump(mode="json")}

    def queries_for(
        kind: MeetingQuestionType, state: QuestionGenerationState
    ) -> list[dict[str, Any]]:
        key = (
            "cutpoint_queries"
            if kind is MeetingQuestionType.CUT_POINT
            else "open_question_queries"
        )
        return list(state["retrieval_plan"].get(key, []))

    async def retrieve(
        kind: MeetingQuestionType, state: QuestionGenerationState
    ) -> QuestionGenerationState:
        merged: dict[str, dict[str, Any]] = {}
        for query in queries_for(kind, state):
            if retriever is not None:
                rows = await retriever(
                    query["query"],
                    UUID(state["org_id"]),
                    UUID(state["meeting_context"]["knowledge_base_id"]),
                    query["top_k"],
                )
                if state["meeting_context"].get("confirmed_revision_id"):
                    async with session_factory() as session:
                        transcript_rows = await retrieve_confirmed_transcript_chunks(
                            session,
                            query=query["query"],
                            organization_id=UUID(state["org_id"]),
                            meeting_id=UUID(state["meeting_id"]),
                            confirmed_document_id=UUID(
                                state["meeting_context"]["confirmed_document_id"]
                            ),
                            knowledge_base_id=UUID(
                                state["meeting_context"]["knowledge_base_id"]
                            ),
                            source_version=state["meeting_context"]["source_version"],
                            top_k=query["top_k"],
                        )
                    rows = list(rows) + transcript_rows
            else:
                async with session_factory() as session:
                    if state["meeting_context"].get("confirmed_revision_id"):
                        kb_rows = await retrieve_authoritative_chunks(
                            session,
                            query=query["query"],
                            organization_id=UUID(state["org_id"]),
                            knowledge_base_id=UUID(state["meeting_context"]["knowledge_base_id"]),
                            top_k=query["top_k"],
                        )
                        transcript_rows = await retrieve_confirmed_transcript_chunks(
                            session,
                            query=query["query"],
                            organization_id=UUID(state["org_id"]),
                            meeting_id=UUID(state["meeting_id"]),
                            confirmed_document_id=UUID(
                                state["meeting_context"]["confirmed_document_id"]
                            ),
                            knowledge_base_id=UUID(state["meeting_context"]["knowledge_base_id"]),
                            source_version=state["meeting_context"]["source_version"],
                            top_k=query["top_k"],
                        )
                        rows = kb_rows + transcript_rows
                    else:
                        rows = await retrieve_authoritative_chunks(
                            session,
                            query=query["query"],
                            organization_id=UUID(state["org_id"]),
                            knowledge_base_id=UUID(state["meeting_context"]["knowledge_base_id"]),
                            top_k=query["top_k"],
                        )
            for row in rows:
                current = merged.get(str(row["chunk_id"]))
                if current is None or float(row.get("fused_score") or 0) > float(
                    current.get("fused_score") or 0
                ):
                    merged[str(row["chunk_id"])] = row
        field = (
            "cutpoint_retrieved_chunks"
            if kind is MeetingQuestionType.CUT_POINT
            else "open_retrieved_chunks"
        )
        await set_progress(state, "RETRIEVING_KNOWLEDGE", 35, "已完成知识库检索")
        return cast(
            QuestionGenerationState,
            {field: list(merged.values()), "logs": [f"{kind.value}:retrieved={len(merged)}"]},
        )

    async def retrieve_cutpoint_docs(state: QuestionGenerationState) -> QuestionGenerationState:
        return await retrieve(MeetingQuestionType.CUT_POINT, state)

    async def retrieve_open_docs(state: QuestionGenerationState) -> QuestionGenerationState:
        return await retrieve(MeetingQuestionType.OPEN_ENDED, state)

    async def rerank(
        kind: MeetingQuestionType, state: QuestionGenerationState
    ) -> QuestionGenerationState:
        field = (
            "cutpoint_retrieved_chunks"
            if kind is MeetingQuestionType.CUT_POINT
            else "open_retrieved_chunks"
        )
        source = (
            state.get("cutpoint_retrieved_chunks", [])
            if kind is MeetingQuestionType.CUT_POINT
            else state.get("open_retrieved_chunks", [])
        )
        async with session_factory() as session:
            if state["meeting_context"].get("confirmed_revision_id"):
                candidates = source
                source = await rehydrate_authoritative_chunks(
                    session,
                    candidates=[
                        row for row in candidates
                        if row.get("source_type") != "confirmed_transcript"
                    ],
                    organization_id=UUID(state["org_id"]),
                    knowledge_base_id=UUID(state["meeting_context"]["knowledge_base_id"]),
                ) + await rehydrate_confirmed_transcript_chunks(
                    session,
                    candidates=[
                        row for row in candidates
                        if row.get("source_type") == "confirmed_transcript"
                    ],
                    organization_id=UUID(state["org_id"]),
                    meeting_id=UUID(state["meeting_id"]),
                    confirmed_document_id=UUID(
                        state["meeting_context"]["confirmed_document_id"]
                    ),
                    knowledge_base_id=UUID(state["meeting_context"]["knowledge_base_id"]),
                    source_version=state["meeting_context"]["source_version"],
                )
            else:
                source = await rehydrate_authoritative_chunks(
                    session,
                    candidates=source,
                    organization_id=UUID(state["org_id"]),
                    knowledge_base_id=UUID(state["meeting_context"]["knowledge_base_id"]),
                )
        query_text = "；".join(query["query"] for query in queries_for(kind, state))[:4000]
        if reranker is not None:
            rows = await reranker(query_text, source, 12)
        else:
            rows = await rerank_chunks(query_text, source, top_k=12)
        await set_progress(state, "RERANKING_EVIDENCE", 50, "已完成证据去重与重排")
        return cast(
            QuestionGenerationState,
            {field: rows, "logs": [f"{kind.value}:reranked={len(rows)}"]},
        )

    async def rerank_cutpoint_docs(state: QuestionGenerationState) -> QuestionGenerationState:
        return await rerank(MeetingQuestionType.CUT_POINT, state)

    async def rerank_open_docs(state: QuestionGenerationState) -> QuestionGenerationState:
        return await rerank(MeetingQuestionType.OPEN_ENDED, state)

    async def generate(
        kind: MeetingQuestionType, state: QuestionGenerationState
    ) -> QuestionGenerationState:
        evidence = (
            state.get("cutpoint_retrieved_chunks", [])
            if kind is MeetingQuestionType.CUT_POINT
            else state.get("open_retrieved_chunks", [])
        )
        async with session_factory() as session:
            if state["meeting_context"].get("confirmed_revision_id"):
                evidence_candidates = evidence
                evidence = await rehydrate_authoritative_chunks(
                    session,
                    candidates=[
                        row for row in evidence_candidates
                        if row.get("source_type") != "confirmed_transcript"
                    ],
                    organization_id=UUID(state["org_id"]),
                    knowledge_base_id=UUID(state["meeting_context"]["knowledge_base_id"]),
                ) + await rehydrate_confirmed_transcript_chunks(
                    session,
                    candidates=[
                        row for row in evidence_candidates
                        if row.get("source_type") == "confirmed_transcript"
                    ],
                    organization_id=UUID(state["org_id"]),
                    meeting_id=UUID(state["meeting_id"]),
                    confirmed_document_id=UUID(
                        state["meeting_context"]["confirmed_document_id"]
                    ),
                    knowledge_base_id=UUID(state["meeting_context"]["knowledge_base_id"]),
                    source_version=state["meeting_context"]["source_version"],
                )
            else:
                evidence = await rehydrate_authoritative_chunks(
                    session,
                    candidates=evidence,
                    organization_id=UUID(state["org_id"]),
                    knowledge_base_id=UUID(state["meeting_context"]["knowledge_base_id"]),
                )
        field = (
            "cutpoint_retrieved_chunks"
            if kind is MeetingQuestionType.CUT_POINT
            else "open_retrieved_chunks"
        )
        target = "cutpoint_questions" if kind is MeetingQuestionType.CUT_POINT else "open_questions"
        if not evidence:
            return cast(
                QuestionGenerationState,
                {
                    target: [],
                    field: [],
                    "logs": [f"{kind.value}:no_evidence"],
                },
            )
        batch = await model.generate(
            {
                "question_type": kind.value,
                "meeting_context": state["meeting_context"],
                "knowledge_base_evidence": evidence,
            }
        )
        values = [
            item.model_dump(mode="json") for item in batch.questions if item.question_type is kind
        ]
        stage = (
            "GENERATING_CUTPOINTS"
            if kind is MeetingQuestionType.CUT_POINT
            else "GENERATING_OPEN_QUESTIONS"
        )
        value = 65 if kind is MeetingQuestionType.CUT_POINT else 75
        await set_progress(state, stage, value, "已完成结构化问题生成")
        return cast(
            QuestionGenerationState,
            {
                target: values,
                field: evidence,
                "logs": [f"{kind.value}:generated={len(values)}"],
            },
        )

    async def generate_cutpoints(state: QuestionGenerationState) -> QuestionGenerationState:
        return await generate(MeetingQuestionType.CUT_POINT, state)

    async def generate_open_questions(state: QuestionGenerationState) -> QuestionGenerationState:
        return await generate(MeetingQuestionType.OPEN_ENDED, state)

    async def merge_questions(state: QuestionGenerationState) -> QuestionGenerationState:
        return {
            "final_questions": state.get("cutpoint_questions", [])
            + state.get("open_questions", []),
            "current_stage": "MERGING_QUESTIONS",
            "progress": 78,
        }

    async def validate_questions(state: QuestionGenerationState) -> QuestionGenerationState:
        all_chunks = state.get("cutpoint_retrieved_chunks", []) + state.get(
            "open_retrieved_chunks", []
        )
        async with session_factory() as session:
            existing_texts = list(
                (
                    await session.scalars(
                        select(MeetingQuestion.content).where(
                            MeetingQuestion.meeting_id == UUID(state["meeting_id"]),
                            MeetingQuestion.deleted_at.is_(None),
                        )
                    )
                ).all()
            )
            existing = {normalize_question_text(value) for value in existing_texts}
        accepted, rule_errors = validate_candidate_questions(
            state.get("final_questions", []),
            available_chunks=all_chunks,
            existing_contents=existing,
        )
        if accepted:
            accepted.sort(key=lambda item: float(item.get("support_score") or 0), reverse=True)
            corpus = [
                {"content": content, "_candidate": False} for content in existing_texts
            ] + [{**item, "_candidate": True} for item in accepted]
            texts = [item["content"] for item in corpus]
            if embedder is not None:
                vectors = await embedder(texts)
            else:
                async with ModelServiceClient() as embedding_client:
                    raw_vectors = await embedding_client.embeddings(texts, include_sparse=False)
                vectors = [list(item["dense"]) for item in raw_vectors]
            deduplicated = semantic_deduplicate(corpus, vectors)
            accepted = [
                {key: value for key, value in item.items() if key != "_candidate"}
                for item in deduplicated
                if item.get("_candidate")
            ]
        blocking_errors: list[str] = []
        if not accepted:
            blocking_errors.append("no_valid_questions")
        else:
            async with session_factory() as session:
                if state["meeting_context"].get("confirmed_revision_id"):
                    all_candidates = all_chunks
                    all_chunks = await rehydrate_authoritative_chunks(
                        session,
                        candidates=[
                            row for row in all_candidates
                            if row.get("source_type") != "confirmed_transcript"
                        ],
                        organization_id=UUID(state["org_id"]),
                        knowledge_base_id=UUID(
                            state["meeting_context"]["knowledge_base_id"]
                        ),
                    ) + await rehydrate_confirmed_transcript_chunks(
                        session,
                        candidates=[
                            row for row in all_candidates
                            if row.get("source_type") == "confirmed_transcript"
                        ],
                        organization_id=UUID(state["org_id"]),
                        meeting_id=UUID(state["meeting_id"]),
                        confirmed_document_id=UUID(
                            state["meeting_context"]["confirmed_document_id"]
                        ),
                        knowledge_base_id=UUID(
                            state["meeting_context"]["knowledge_base_id"]
                        ),
                        source_version=state["meeting_context"]["source_version"],
                    )
                else:
                    all_chunks = await rehydrate_authoritative_chunks(
                        session,
                        candidates=all_chunks,
                        organization_id=UUID(state["org_id"]),
                        knowledge_base_id=UUID(
                            state["meeting_context"]["knowledge_base_id"]
                        ),
                    )
            current_chunks = {str(item["chunk_id"]): item for item in all_chunks}
            current_questions: list[dict[str, Any]] = []
            for question in accepted:
                current_evidence = [
                    evidence
                    for evidence in question.get("evidence", [])
                    if str(evidence.get("chunk_id")) in current_chunks
                    and str(evidence.get("document_id"))
                    == str(current_chunks[str(evidence["chunk_id"])]["document_id"])
                    and str(evidence.get("quote") or "")
                    in str(current_chunks[str(evidence["chunk_id"])]["content"])
                ]
                if current_evidence:
                    current_questions.append({**question, "evidence": current_evidence})
            accepted = current_questions
        if accepted:
            review = await model.review(
                {
                    "meeting_context": state["meeting_context"],
                    "candidate_questions": accepted,
                    "available_evidence": all_chunks,
                }
            )
            decisions = {item.question_index: item for item in review.reviews}
            passed: list[dict[str, Any]] = []
            for index, question in enumerate(accepted):
                decision = decisions.get(index)
                if decision is not None and decision.decision == "pass":
                    passed.append(question)
                elif decision is not None:
                    blocking_errors.append(f"quality_{decision.decision}:{index}:{decision.reason}")
                else:
                    blocking_errors.append(f"quality_missing:{index}")
            accepted = passed
            if not accepted:
                blocking_errors.append("quality_review_rejected_all")
        elif "no_valid_questions" not in blocking_errors:
            blocking_errors.append("evidence_revoked_before_review")
        present_types = {item.get("question_type") for item in accepted}
        if MeetingQuestionType.CUT_POINT.value not in present_types:
            blocking_errors.append("missing_cutpoint_questions")
        if MeetingQuestionType.OPEN_ENDED.value not in present_types:
            blocking_errors.append("missing_open_questions")
        evidence = {i: item["evidence"] for i, item in enumerate(accepted)}
        progress = await set_progress(state, "VALIDATING_QUESTIONS", 85, "已完成规则与模型双层校验")
        return {
            **progress,
            "final_questions": accepted,
            "evidence_by_index": evidence,
            "validation_errors": blocking_errors,
            "logs": [f"rule_rejections={len(rule_errors)}"],
        }

    async def refine_retrieval_plan(state: QuestionGenerationState) -> QuestionGenerationState:
        retry_count = state.get("retry_count", 0) + 1
        plan = await model.build_plan(
            {
                "meeting_context": state["meeting_context"],
                "previous_plan": state["retrieval_plan"],
                "validation_errors": state.get("validation_errors", []),
                "instruction": "修正检索范围，不得引入会议中未出现的实体",
            }
        )
        validate_plan_grounding(plan, state["meeting_context"])
        progress = await set_progress(
            state,
            "PLANNING_RETRIEVAL",
            18,
            "校验未通过，正在修正检索计划",
            status=AiTaskStatus.RETRYING,
            retry_count=retry_count,
        )
        return {
            **progress,
            "retry_count": retry_count,
            "retrieval_plan": plan.model_dump(mode="json"),
            "validation_errors": [],
        }

    async def persist(state: QuestionGenerationState) -> QuestionGenerationState:
        referenced_ids = {
            str(evidence["chunk_id"])
            for question in state.get("final_questions", [])
            for evidence in question.get("evidence", [])
        }
        if not referenced_ids:
            raise AppException(422, "no_valid_evidence", "问题缺少合法证据")
        async with session_factory() as session:
            task = await session.get(AiTask, UUID(state["task_id"]), with_for_update=True)
            if task is None:
                raise AppException(404, "ai_task_not_found", "AI 任务不存在")
            attempt = UUID(state["attempt_token"]) if state.get("attempt_token") else None
            if attempt is not None and task.attempt_token != attempt:
                raise AppException(409, "stale_task_attempt", "任务执行租约已失效")
            confirmed_document_id = state["meeting_context"].get("confirmed_document_id")
            rows = (
                await session.execute(
                    select(Chunk, Document)
                    .join(Document, Document.id == Chunk.document_id)
                    .where(
                        Chunk.chunk_id.in_(referenced_ids),
                        Chunk.organization_id == task.organization_id,
                        Chunk.knowledge_base_id
                        == UUID(state["meeting_context"]["knowledge_base_id"]),
                        Document.organization_id == task.organization_id,
                        Document.knowledge_base_id
                        == UUID(state["meeting_context"]["knowledge_base_id"]),
                        Document.deleted_at.is_(None),
                        (
                            (Chunk.publication_status == "PUBLISHED")
                            & (Document.status == "PUBLISHED")
                        )
                        | (
                            (Chunk.publication_status == "DRAFT")
                            & (Document.id == UUID(confirmed_document_id))
                            if confirmed_document_id
                            else False
                        ),
                    )
                )
            ).all()
            confirmed_chunk_ids = {
                chunk.chunk_id
                for chunk, _document in rows
                if chunk.publication_status == "DRAFT"
                and confirmed_document_id
                and str(chunk.document_id) == str(confirmed_document_id)
            }
            if confirmed_document_id and confirmed_chunk_ids:
                authorized_rows = await rehydrate_confirmed_transcript_chunks(
                    session,
                    candidates=[
                        {
                            "chunk_id": chunk.chunk_id,
                            "document_id": chunk.document_id,
                            "source_type": "confirmed_transcript",
                        }
                        for chunk, _document in rows
                        if chunk.chunk_id in confirmed_chunk_ids
                    ],
                    organization_id=task.organization_id,
                    meeting_id=task.meeting_id,
                    confirmed_document_id=UUID(confirmed_document_id),
                    knowledge_base_id=UUID(
                        state["meeting_context"]["knowledge_base_id"]
                    ),
                    source_version=state["meeting_context"]["source_version"],
                )
                authorized_ids = {str(row["chunk_id"]) for row in authorized_rows}
                confirmed_chunk_ids &= authorized_ids
            chunks = {
                chunk.chunk_id: chunk
                for chunk, _document in rows
                if chunk.publication_status == "PUBLISHED"
                or chunk.chunk_id in confirmed_chunk_ids
            }
            cutpoints, opens = await persist_questions(
                session,
                task=task,
                questions=state["final_questions"],
                evidence_by_index=state["evidence_by_index"],
                valid_chunks=chunks,
                confirmed_chunk_ids=confirmed_chunk_ids,
                confirmed_document_id=(
                    UUID(confirmed_document_id) if confirmed_document_id else None
                ),
            )
            if not has_required_question_types(cutpoints, opens):
                raise AppException(
                    422,
                    "incomplete_question_types",
                    "切点问题和开放性问题均须至少包含一条合法证据",
                )
            meeting = await session.get(Meeting, task.meeting_id)
            if meeting is not None and meeting.analysis_status is AnalysisStatus.NOT_READY:
                meeting.analysis_status = AnalysisStatus.READY
            task.status = AiTaskStatus.PENDING_REVIEW
            task.current_stage = "PENDING_REVIEW"
            task.progress = 100
            task.message = "问题生成完成，请进行人工核验"
            task.cutpoint_count = cutpoints
            task.open_question_count = opens
            task.completed_at = datetime.now(timezone.utc)
            task.lease_expires_at = None
            await session.commit()
        return {
            "status": AiTaskStatus.PENDING_REVIEW.value,
            "current_stage": "PENDING_REVIEW",
            "progress": 100,
        }

    async def mark_failed(state: QuestionGenerationState) -> QuestionGenerationState:
        message = ";".join(state.get("validation_errors", [])) or "问题校验失败"
        async with session_factory() as session:
            conditions: list[Any] = [AiTask.id == UUID(state["task_id"])]
            if state.get("attempt_token"):
                conditions.append(AiTask.attempt_token == UUID(state["attempt_token"]))
            await session.execute(
                update(AiTask)
                .where(*conditions)
                .values(
                    status=AiTaskStatus.FAILED,
                    current_stage="FAILED",
                    progress=100,
                    error_code="question_validation_failed",
                    error_message=message[:2000],
                    completed_at=func.now(),
                    lease_expires_at=None,
                )
            )
            await session.commit()
        return {
            "status": AiTaskStatus.FAILED.value,
            "current_stage": "FAILED",
            "error_message": message,
            "progress": 100,
        }

    nodes = {
        "load_meeting_context": load_meeting_context,
        "build_retrieval_plan": build_retrieval_plan,
        "retrieve_cutpoint_docs": retrieve_cutpoint_docs,
        "retrieve_open_docs": retrieve_open_docs,
        "rerank_cutpoint_docs": rerank_cutpoint_docs,
        "rerank_open_docs": rerank_open_docs,
        "generate_cutpoints": generate_cutpoints,
        "generate_open_questions": generate_open_questions,
        "merge_questions": merge_questions,
        "validate_questions": validate_questions,
        "refine_retrieval_plan": refine_retrieval_plan,
        "persist_questions": persist,
        "mark_failed": mark_failed,
    }
    for name, node in nodes.items():
        builder.add_node(name, node)
    builder.add_edge(START, "load_meeting_context")
    builder.add_edge("load_meeting_context", "build_retrieval_plan")
    builder.add_edge("build_retrieval_plan", "retrieve_cutpoint_docs")
    builder.add_edge("build_retrieval_plan", "retrieve_open_docs")
    builder.add_edge("retrieve_cutpoint_docs", "rerank_cutpoint_docs")
    builder.add_edge("retrieve_open_docs", "rerank_open_docs")
    builder.add_edge("rerank_cutpoint_docs", "generate_cutpoints")
    builder.add_edge("rerank_open_docs", "generate_open_questions")
    builder.add_edge("generate_cutpoints", "merge_questions")
    builder.add_edge("generate_open_questions", "merge_questions")
    builder.add_edge("merge_questions", "validate_questions")
    builder.add_conditional_edges(
        "validate_questions",
        route_after_validation,
        {
            "persist_questions": "persist_questions",
            "refine_retrieval_plan": "refine_retrieval_plan",
            "mark_failed": "mark_failed",
        },
    )
    builder.add_edge("refine_retrieval_plan", "retrieve_cutpoint_docs")
    builder.add_edge("persist_questions", END)
    builder.add_edge("mark_failed", END)
    return (
        builder.compile(checkpointer=checkpointer)
        if checkpointer is not None
        else builder.compile()
    )
