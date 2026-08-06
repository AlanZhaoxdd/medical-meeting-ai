from __future__ import annotations

import re
from datetime import datetime, timezone
from operator import add
from typing import Annotated, Any, TypedDict
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
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
    QuestionEvidence,
)
from app.schemas.analysis import AnalysisModuleOut, AnalysisResult, SourceRegistryItem
from app.services.analysis_model_client import AnalysisModelClient
from app.services.analysis_service import (
    persist_analysis_run,
    validate_citation_indices,
)
from app.services.question_generation import (
    rehydrate_authoritative_chunks,
    rehydrate_confirmed_transcript_chunks,
    rerank_chunks,
    retrieve_authoritative_chunks,
    retrieve_confirmed_transcript_chunks,
    update_task_progress,
)


class AnalysisState(TypedDict, total=False):
    task_id: str
    meeting_id: str
    org_id: str
    attempt_token: str
    source_version: int
    meeting_context: dict[str, Any]
    selected_cutpoint_questions: list[dict[str, Any]]
    selected_open_questions: list[dict[str, Any]]
    retrieved_chunks: list[dict[str, Any]]
    source_registry: list[dict[str, Any]]
    result: dict[str, Any]
    validation_errors: list[str]
    retry_count: int
    max_retries: int
    status: str
    current_stage: str
    progress: int
    logs: Annotated[list[str], add]
    error_code: str | None
    error_message: str | None


def _question_queries(question: dict[str, Any]) -> list[str]:
    queries = [str(question.get("content") or "")]
    rationale = str(question.get("rationale") or "")
    topic = str(question.get("topic") or "")
    if rationale:
        queries.append(rationale[:200])
    if topic and topic not in queries[0]:
        queries.append(topic)
    return [query.strip() for query in queries if query.strip()][:2]


def build_analysis_graph(
    session_factory: async_sessionmaker[AsyncSession],
    checkpointer: Any = None,
    *,
    model_client: AnalysisModelClient | None = None,
    retriever: Any | None = None,
    reranker: Any | None = None,
) -> Any:
    model = model_client or AnalysisModelClient()
    builder = StateGraph(AnalysisState)

    async def set_progress(
        state: AnalysisState,
        stage: str,
        progress: int,
        message: str,
        *,
        status: AiTaskStatus | None = None,
        retry_count: int | None = None,
    ) -> AnalysisState:
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

    async def load_meeting_context(state: AnalysisState) -> AnalysisState:
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
                )
                .order_by(TranscriptRevision.version.desc())
            )
            if revision is None:
                raise AppException(409, "confirmed_revision_required", "仅确认版纪要可生成分析")
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
            questions = list(
                (
                    await session.scalars(
                        select(MeetingQuestion).where(
                            MeetingQuestion.meeting_id == meeting.id,
                            MeetingQuestion.deleted_at.is_(None),
                            MeetingQuestion.analysis_selected.is_(True),
                        )
                    )
                ).all()
            )
            if not questions:
                raise AppException(422, "analysis_selection_empty", "尚未选择带入分析的问题")
            selected: list[dict[str, Any]] = []
            for question in questions:
                evidences = list(
                    (
                        await session.scalars(
                            select(QuestionEvidence).where(
                                QuestionEvidence.question_id == question.id
                            )
                        )
                    ).all()
                )
                selected.append(
                    {
                        "id": str(question.id),
                        "question_type": question.question_type.value,
                        "content": question.content,
                        "rationale": question.rationale,
                        "topic": question.topic,
                        "expected_answer_type": question.expected_answer_type,
                        "evidence": [
                            {
                                "chunk_id": evidence.chunk_id,
                                "document_id": (
                                    str(evidence.document_id) if evidence.document_id else None
                                ),
                                "quote": evidence.quote,
                                "evidence_summary": evidence.evidence_summary,
                            }
                            for evidence in evidences
                        ],
                    }
                )
            cutpoints = [
                item
                for item in selected
                if item["question_type"] == MeetingQuestionType.CUT_POINT.value
            ]
            opens = [
                item
                for item in selected
                if item["question_type"] == MeetingQuestionType.OPEN_ENDED.value
            ]
            if not cutpoints or not opens:
                raise AppException(
                    422, "analysis_selection_incomplete", "切点问题和开放性问题均须至少选中一条"
                )
            attendees: list[str] = []
            for key in ("advisor_names", "internal_attendees"):
                value = meeting.meeting_info.get(key)
                if isinstance(value, str):
                    attendees.extend(
                        item.strip()
                        for item in re.split(r"[;,，、\n]", value)
                        if item.strip()
                    )
                elif isinstance(value, list):
                    attendees.extend(str(item) for item in value if str(item).strip())
            context = {
                "title": meeting.title,
                "date": meeting.starts_at.isoformat(),
                "location": meeting.location,
                "organizer": meeting.organizer,
                "topic": meeting.topic,
                "description": meeting.description,
                "meeting_purpose": meeting.meeting_info.get("meeting_purpose"),
                "recorder": meeting.meeting_info.get("recorder"),
                "attendees": attendees[:40],
                "organization_id": str(org_id),
                "knowledge_base_id": str(kb.id),
                "knowledge_base_name": kb.name,
                "source_version": revision.version,
                "confirmed_revision_id": str(revision.id),
                "confirmed_document_id": str(revision.document_id),
                "confirmed_minutes": minutes,
            }
        progress = await set_progress(state, "LOADING_MEETING", 10, "已加载会议与选中问题")
        return {
            **progress,
            "meeting_context": context,
            "selected_cutpoint_questions": cutpoints,
            "selected_open_questions": opens,
        }

    async def retrieve_evidence(state: AnalysisState) -> AnalysisState:
        merged: dict[str, dict[str, Any]] = {}
        org_id = UUID(state["org_id"])
        kb_id = UUID(state["meeting_context"]["knowledge_base_id"])
        meeting_id = UUID(state["meeting_id"])
        questions = state["selected_cutpoint_questions"] + state["selected_open_questions"]
        async with session_factory() as session:
            for question in questions:
                for query in _question_queries(question):
                    if retriever is not None:
                        kb_rows = await retriever(query, org_id, kb_id, 8)
                    else:
                        kb_rows = await retrieve_authoritative_chunks(
                            session,
                            query=query,
                            organization_id=org_id,
                            knowledge_base_id=kb_id,
                            top_k=8,
                        )
                    transcript_rows = await retrieve_confirmed_transcript_chunks(
                        session,
                        query=query,
                        organization_id=org_id,
                        meeting_id=meeting_id,
                        confirmed_document_id=UUID(
                            state["meeting_context"]["confirmed_document_id"]
                        ),
                        knowledge_base_id=kb_id,
                        source_version=state["meeting_context"]["source_version"],
                        top_k=8,
                    )
                    for row in [*kb_rows, *transcript_rows]:
                        chunk_id = str(row["chunk_id"])
                        current = merged.get(chunk_id)
                        if current is None or float(row.get("fused_score") or 0) > float(
                            current.get("fused_score") or 0
                        ):
                            merged[chunk_id] = row
        progress = await set_progress(state, "RETRIEVING_KNOWLEDGE", 40, "已完成会议与知识库检索")
        return {**progress, "retrieved_chunks": list(merged.values())[:60]}

    async def rerank_evidence(state: AnalysisState) -> AnalysisState:
        source = state.get("retrieved_chunks", [])
        if not source:
            return {
                "retrieved_chunks": [],
                "logs": ["analysis:no_evidence"],
            }
        async with session_factory() as session:
            candidates = source
            if state["meeting_context"].get("confirmed_revision_id"):
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
        questions = state["selected_cutpoint_questions"] + state["selected_open_questions"]
        query_text = "；".join(question["content"] for question in questions)[:4000]
        if reranker is not None:
            rows = await reranker(query_text, source, 24)
        else:
            rows = await rerank_chunks(query_text, source, top_k=24)
        progress = await set_progress(state, "RERANKING_EVIDENCE", 55, "已完成证据重排")
        return {**progress, "retrieved_chunks": rows}

    async def build_source_registry(state: AnalysisState) -> AnalysisState:
        chunks = state.get("retrieved_chunks", [])
        registry: list[dict[str, Any]] = []
        if chunks:
            ids = [str(item["chunk_id"]) for item in chunks]
            async with session_factory() as session:
                rows = (
                    await session.execute(
                        select(Chunk, Document)
                        .join(Document, Document.id == Chunk.document_id)
                        .where(
                            Chunk.chunk_id.in_(ids),
                            Chunk.organization_id == UUID(state["org_id"]),
                        )
                    )
                ).all()
            meta = {chunk.chunk_id: (chunk, document) for chunk, document in rows}
            for item in chunks:
                pair = meta.get(str(item["chunk_id"]))
                chunk, document = pair if pair else (None, None)
                if chunk is None:
                    continue
                locator = chunk.source_locator or {}
                is_transcript = item.get("source_type") == "confirmed_transcript"
                registry.append(
                    {
                        "index": 0,  # renumbered below
                        "type": "transcript" if is_transcript else "knowledge_base",
                        "title": "会议转写片段"
                        if is_transcript
                        else (document.filename if document else "知识库片段"),
                        "snippet": str(item.get("content") or "")[:200],
                        "speaker_name": locator.get("speaker"),
                        "timestamp": locator.get("time_range"),
                        "page_number": locator.get("page_number"),
                        "chunk_id": chunk.chunk_id,
                        "document_id": str(chunk.document_id),
                        "document_title": document.filename if document else None,
                        "knowledge_base_name": state["meeting_context"].get(
                            "knowledge_base_name"
                        ),
                        "block_id": item.get("block_id"),
                    }
                )
        for question in state["selected_cutpoint_questions"]:
            registry.append(
                {
                    "index": 0,
                    "type": "cutoff_question",
                    "title": "切点问题",
                    "snippet": question["content"][:200],
                    "question_id": question["id"],
                }
            )
        for question in state["selected_open_questions"]:
            registry.append(
                {
                    "index": 0,
                    "type": "open_question",
                    "title": "开放性问题",
                    "snippet": question["content"][:200],
                    "question_id": question["id"],
                }
            )
        for index, item in enumerate(registry, start=1):
            item["index"] = index
        progress = await set_progress(
            state, "ORGANIZING_EVIDENCE", 65, f"已组织 {len(registry)} 条引用来源"
        )
        return {**progress, "source_registry": registry}

    async def generate(state: AnalysisState) -> AnalysisState:
        if not state.get("source_registry"):
            raise AppException(422, "analysis_no_evidence", "未检索到可用于分析的资料")
        transcript = [
            {
                "chunk_id": item.get("chunk_id"),
                "speaker": item.get("speaker_name"),
                "time_range": item.get("timestamp"),
                "content": next(
                    (
                        chunk["content"]
                        for chunk in state.get("retrieved_chunks", [])
                        if str(chunk["chunk_id"]) == item.get("chunk_id")
                    ),
                    item.get("snippet"),
                ),
            }
            for item in state["source_registry"]
            if item.get("type") == "transcript"
        ]
        knowledge_base = [
            {
                "chunk_id": item.get("chunk_id"),
                "document_title": item.get("document_title"),
                "section_title": item.get("section_title"),
                "content": next(
                    (
                        chunk["content"]
                        for chunk in state.get("retrieved_chunks", [])
                        if str(chunk["chunk_id"]) == item.get("chunk_id")
                    ),
                    item.get("snippet"),
                ),
            }
            for item in state["source_registry"]
            if item.get("type") == "knowledge_base"
        ]
        meeting_context = {
            key: value
            for key, value in state["meeting_context"].items()
            if key != "confirmed_minutes"
        }
        result = await model.generate(
            {
                "meeting_context": meeting_context,
                "confirmed_minutes": state["meeting_context"].get("confirmed_minutes", ""),
                "transcript": transcript,
                "knowledge_base": knowledge_base,
                "selected_cutpoint_questions": state["selected_cutpoint_questions"],
                "selected_open_questions": state["selected_open_questions"],
                "source_registry": state["source_registry"],
            }
        )
        progress = await set_progress(state, "GENERATING_ANALYSIS", 85, "已完成 AI 分析生成")
        return {**progress, "result": result.model_dump(mode="json")}

    async def validate_and_persist(state: AnalysisState) -> AnalysisState:
        registry = state.get("source_registry", [])
        raw_modules = state.get("result", {}).get("modules", [])
        modules = validate_citation_indices(raw_modules, len(registry))
        notes = list(state.get("result", {}).get("insufficient_notes", []))
        allowed_categories = {"meeting", "transcript", "questions", "knowledge", "ai"}
        normalized: list[dict[str, Any]] = []
        for module in modules:
            content = str(module["content"]) if module.get("content") is not None else None
            items = [str(item) for item in (module.get("items") or [])]
            citations = module.get("citations") or []
            if not content and not items:
                notes.append(f"{module.get('title', '模块')}：资料不足，未生成")
                module = {**module, "content": None, "items": []}
            elif content and not citations:
                notes.append(f"{module.get('title', '模块')}：缺少可核验引用，内容未保留")
                module = {**module, "content": None, "items": []}
            module = {
                **module,
                "content": content,
                "items": items,
                "category": (
                    module.get("category")
                    if module.get("category") in allowed_categories
                    else "meeting"
                ),
            }
            normalized.append(module)
        result = AnalysisResult(
            modules=[AnalysisModuleOut.model_validate(module) for module in normalized],
            insufficient_notes=notes,
        )
        sources = [SourceRegistryItem.model_validate(item) for item in registry]
        async with session_factory() as session:
            task = await session.get(AiTask, UUID(state["task_id"]), with_for_update=True)
            if task is None:
                raise AppException(404, "ai_task_not_found", "AI 任务不存在")
            attempt = UUID(state["attempt_token"]) if state.get("attempt_token") else None
            if attempt is not None and task.attempt_token != attempt:
                raise AppException(409, "stale_task_attempt", "任务执行租约已失效")
            await persist_analysis_run(session, task=task, result=result, sources=sources)
            meeting = await session.get(Meeting, task.meeting_id)
            if meeting is not None:
                meeting.analysis_status = AnalysisStatus.SUCCEEDED
            task.status = AiTaskStatus.SUCCEEDED
            task.current_stage = "SUCCEEDED"
            task.progress = 100
            task.message = "AI 分析已完成"
            task.completed_at = datetime.now(timezone.utc)
            task.lease_expires_at = None
            await session.commit()
        return {
            "status": AiTaskStatus.SUCCEEDED.value,
            "current_stage": "SUCCEEDED",
            "progress": 100,
            "logs": [f"analysis:modules={len(normalized)}"],
        }

    nodes = {
        "load_meeting_context": load_meeting_context,
        "retrieve_evidence": retrieve_evidence,
        "rerank_evidence": rerank_evidence,
        "build_source_registry": build_source_registry,
        "generate": generate,
        "validate_and_persist": validate_and_persist,
    }
    builder.add_node("load_meeting_context", nodes["load_meeting_context"])
    builder.add_node("retrieve_evidence", nodes["retrieve_evidence"])
    builder.add_node("rerank_evidence", nodes["rerank_evidence"])
    builder.add_node("build_source_registry", nodes["build_source_registry"])
    builder.add_node("generate", nodes["generate"])
    builder.add_node("validate_and_persist", nodes["validate_and_persist"])
    builder.add_edge(START, "load_meeting_context")
    builder.add_edge("load_meeting_context", "retrieve_evidence")
    builder.add_edge("retrieve_evidence", "rerank_evidence")
    builder.add_edge("rerank_evidence", "build_source_registry")
    builder.add_edge("build_source_registry", "generate")
    builder.add_edge("generate", "validate_and_persist")
    builder.add_edge("validate_and_persist", END)
    return builder.compile(checkpointer=checkpointer)
