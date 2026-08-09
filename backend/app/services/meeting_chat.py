from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import AppException, ForbiddenError, NotFoundError
from app.models.chat import ChatConversation, ChatMessage
from app.models.kb import (
    KnowledgeBase,
    MeetingImport,
    TranscriptRevision,
    TranscriptRevisionBlock,
)
from app.models.meeting import Meeting
from app.schemas.analysis import (
    MeetingChatRequest,
    MeetingChatResponse,
    MeetingChatSource,
)
from app.services.question_generation import (
    rehydrate_authoritative_chunks,
    rehydrate_confirmed_transcript_chunks,
    rerank_chunks,
    retrieve_authoritative_chunks,
    retrieve_confirmed_transcript_chunks,
)

CHAT_PROMPT_VERSION = "meeting-chat-v1"
CHAT_REWRITE_PROMPT_VERSION = "chat-rewrite-v1"

INSUFFICIENT_ANSWER = (
    "根据当前会议记录和已连接的知识库，暂时无法确认该问题。"
    "你可以调整问题，或者扩大检索范围。"
)

CHAT_SYSTEM_PROMPT = (
    "你是医药会议智能问答助手。你只能依据输入材料中的检索片段回答问题，"
    "禁止调用外部知识或编造输入中不存在的内容。\n"
    "输入材料包括：\n"
    "- meeting_context：会议基本信息；\n"
    "- confirmed_minutes：确认版会议转写全文"
    "（仅作背景参考，正文引用仍必须使用 source_registry）；\n"
    "- source_registry：带编号的检索片段，每条含 content 全文。\n"
    "回答规则：\n"
    "1. 答案必须基于 source_registry 中对应编号的内容，并在相关论断后标注引用编号 [n]。\n"
    "2. 严禁引用 source_registry 中不存在的编号，严禁编造内容。\n"
    "3. 参会者观点、时间、数字等只能来自检索片段原文。\n"
    f"4. 如果材料不足以回答用户问题，只能原样输出以下文案，不要附加任何其他内容：\n"
    f"{INSUFFICIENT_ANSWER}\n"
    "5. 使用简体中文和 Markdown 格式，回答简洁直接。"
)

CHAT_REWRITE_SYSTEM_PROMPT = (
    "你是多轮会议问答的查询改写助手。用户在当前问题里可能使用指代（如“那”“它”“这个方案”“上一条回答”）"
    "或省略了上下文，你需要把当前问题改写成一个不依赖历史也能独立理解的完整问题，只用于检索。\n"
    "规则：\n"
    "1. 只能解析指代和补全省略成分，不得引入对话历史中不存在的事实、数字、药名或假设；\n"
    "2. 如果当前问题已经完整、无需历史即可理解，原样输出；\n"
    "3. 只输出改写后的问题本身，不要解释、不要加引号、不要输出任何其他内容。"
)


def _format_time_range(start_ms: int | None, end_ms: int | None) -> str | None:
    def format_ms(value: int | None) -> str | None:
        if value is None or value < 0:
            return None
        total_seconds = value // 1000
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return (
            f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            if hours
            else f"{minutes:02d}:{seconds:02d}"
        )

    start = format_ms(start_ms)
    end = format_ms(end_ms)
    if start and end and start != end:
        return f"{start} - {end}"
    return start or end


class MeetingChatModelClient:
    """LLM boundary for the meeting Q&A endpoint.

    ``generator`` is injectable in tests; when omitted the client talks to the
    configured OpenAI-compatible endpoint.
    """

    def __init__(
        self,
        generator: Callable[[dict[str, Any]], Awaitable[str]] | None = None,
        *,
        model_name: str | None = None,
    ) -> None:
        self.generator = generator
        self.model_name = model_name or get_settings().llm_model or "unconfigured"

    async def answer(self, payload: dict[str, Any]) -> str:
        if self.generator is not None:
            return await self.generator(payload)
        return await self._invoke(payload)

    async def stream_answer(self, payload: dict[str, Any]) -> AsyncIterator[str]:
        """Yield answer text as soon as the configured model produces it."""

        if self.generator is not None:
            answer = await self.generator(payload)
            if answer:
                yield answer
            return

        settings = get_settings()
        model = self._build_model(settings)
        saw_content = False
        async for chunk in model.astream(self._build_prompt(payload)):
            content = getattr(chunk, "content", "")
            if isinstance(content, list):
                content = "".join(
                    str(part.get("text", ""))
                    for part in content
                    if isinstance(part, dict)
                )
            text = str(content or "")
            if text:
                saw_content = True
                yield text
        if not saw_content:
            raise AppException(502, "chat_empty_response", "问答模型未返回内容")

    @staticmethod
    def _build_model(settings: Any) -> Any:
        if (
            not settings.llm_base_url
            or not settings.resolved_llm_api_key
            or not settings.llm_model
        ):
            raise AppException(503, "chat_model_unavailable", "AI 问答模型不可用，请检查 LLM 配置")
        from langchain_openai import ChatOpenAI

        options: dict[str, Any] = {
            "base_url": settings.llm_base_url,
            "api_key": SecretStr(settings.resolved_llm_api_key),
            "model": settings.llm_model,
            "temperature": 0.2,
            "timeout": 60,
            "max_retries": 1,
        }
        if "api.deepseek.com" in settings.llm_base_url:
            options["extra_body"] = {"thinking": {"type": "disabled"}}
        return ChatOpenAI(**options)

    @staticmethod
    def _build_prompt(payload: dict[str, Any]) -> str:
        return (
            f"{CHAT_SYSTEM_PROMPT}\n"
            f"用户问题：{payload['question']}\n"
            f"输入材料："
            f"{json.dumps(payload, ensure_ascii=False, default=str)[:120000]}"
        )

    async def _invoke(self, payload: dict[str, Any]) -> str:
        settings = get_settings()
        response = await self._build_model(settings).ainvoke(self._build_prompt(payload))
        content = getattr(response, "content", None)
        if isinstance(content, list):
            content = "".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict)
            )
        answer = str(content or "").strip()
        if not answer:
            raise AppException(502, "chat_empty_response", "问答模型未返回内容")
        return answer


def _render_rewrite_history(
    history: list[dict[str, str]], *, cap: int = 4_000
) -> str:
    """Render recent turns for rewriting, keeping the most recent turns first."""

    blocks = [
        f"用户：{(turn.get('question') or '').strip()}\n"
        f"助手：{(turn.get('answer') or '').strip()}"
        for turn in history
    ]
    blocks.reverse()
    kept: list[str] = []
    used = 0
    for block in blocks:
        if len(block) > cap:
            block = block[:cap]
        if used + len(block) > cap:
            break
        kept.append(block)
        used += len(block)
    kept.reverse()
    return "\n\n".join(kept)


def build_rewrite_prompt(
    history: list[dict[str, str]],
    question: str,
    *,
    history_cap: int = 4_000,
) -> str:
    """Build the query-rewrite prompt: static rules first, dynamic history last."""

    rendered = _render_rewrite_history(history, cap=history_cap)
    return (
        f"{CHAT_REWRITE_SYSTEM_PROMPT}\n"
        f"对话历史：\n{rendered}\n\n"
        f"当前问题：{question}\n"
        f"改写后的问题："
    )


class MeetingChatRewriter:
    """Rewrite a follow-up question into a self-contained retrieval query.

    The rewrite is only used for retrieval and generation; the original
    question is always persisted for audit. Any model failure, empty output or
    overlong output falls back to the original question so follow-up handling
    never makes the request fail.
    """

    def __init__(
        self,
        generator: Callable[[str], Awaitable[str]] | None = None,
        *,
        model_name: str | None = None,
    ) -> None:
        self.generator = generator
        self.model_name = model_name or get_settings().llm_model or "unconfigured"

    async def rewrite(
        self,
        *,
        history: list[dict[str, str]],
        question: str,
        max_result_chars: int = 2_000,
    ) -> str:
        if not history:
            return question
        prompt = build_rewrite_prompt(history, question)
        try:
            if self.generator is not None:
                result = await self.generator(prompt)
            else:
                result = await self._invoke(prompt)
        except Exception:
            return question
        result = (result or "").strip().strip('"').strip("“”")
        if not result or len(result) > max_result_chars or result == question:
            return question
        return result

    async def _invoke(self, prompt: str) -> str:
        settings = get_settings()
        if (
            not settings.llm_base_url
            or not settings.resolved_llm_api_key
            or not settings.llm_model
        ):
            raise AppException(503, "chat_model_unavailable", "AI 问答模型不可用，请检查 LLM 配置")
        response = await MeetingChatModelClient._build_model(settings).ainvoke(prompt)
        content = getattr(response, "content", None)
        if isinstance(content, list):
            content = "".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict)
            )
        return str(content or "").strip()


async def load_chat_context(
    session: AsyncSession,
    *,
    meeting_id: UUID,
    organization_id: UUID,
    need_kb: bool,
) -> dict[str, Any]:
    """Load meeting, knowledge base and confirmed transcript for one chat turn."""

    meeting = await session.get(Meeting, meeting_id)
    if meeting is None or meeting.deleted_at is not None:
        raise NotFoundError("会议", "meeting_not_found")
    if meeting.organization_id != organization_id:
        raise ForbiddenError("会议不存在或无权访问")

    kb: KnowledgeBase | None = None
    if need_kb:
        if meeting.knowledge_base_id is None:
            # Degrade gracefully to meeting-only scope, matching the UI mock:
            # meetings without a linked KB still answer from the transcript.
            kb = None
        else:
            kb = await session.scalar(
                select(KnowledgeBase).where(
                    KnowledgeBase.id == meeting.knowledge_base_id,
                    KnowledgeBase.organization_id == organization_id,
                    KnowledgeBase.deleted_at.is_(None),
                )
            )
            if kb is None:
                raise ForbiddenError("知识库不存在或无权访问")

    revision = await session.scalar(
        select(TranscriptRevision)
        .join(MeetingImport, MeetingImport.confirmed_revision_id == TranscriptRevision.id)
        .where(
            MeetingImport.meeting_id == meeting.id,
            MeetingImport.organization_id == organization_id,
            MeetingImport.status == "CONFIRMED",
            TranscriptRevision.status == "CONFIRMED",
        )
        .order_by(TranscriptRevision.version.desc())
    )
    blocks: list[TranscriptRevisionBlock] = []
    minutes = ""
    confirmed_document_id: str | None = None
    source_version: int | None = None
    if revision is not None:
        blocks = list(
            (
                await session.scalars(
                    select(TranscriptRevisionBlock)
                    .where(TranscriptRevisionBlock.revision_id == revision.id)
                    .order_by(TranscriptRevisionBlock.order)
                )
            ).all()
        )
        minutes = "\n".join(
            block.text.strip()
            for block in blocks
            if (block.text or "").strip()
        )
        confirmed_document_id = str(revision.document_id)
        source_version = revision.version

    attendees: list[str] = []
    for key in ("advisor_names", "internal_attendees"):
        value = meeting.meeting_info.get(key)
        if isinstance(value, str):
            attendees.extend(
                item.strip() for item in re.split(r"[;,，、\n]", value) if item.strip()
            )
        elif isinstance(value, list):
            attendees.extend(str(item) for item in value if str(item).strip())

    return {
        "meeting": meeting,
        "meeting_context": {
            "title": meeting.title,
            "date": meeting.starts_at.isoformat() if meeting.starts_at else None,
            "location": meeting.location,
            "organizer": meeting.organizer,
            "topic": meeting.topic,
            "meeting_purpose": meeting.meeting_info.get("meeting_purpose"),
            "attendees": attendees[:40],
            "knowledge_base_name": kb.name if kb is not None else None,
        },
        "kb": kb,
        "kb_id": str(meeting.knowledge_base_id) if meeting.knowledge_base_id else None,
        "kb_name": kb.name if kb is not None else None,
        "revision": revision,
        "blocks": blocks,
        "minutes": minutes,
        "confirmed_document_id": confirmed_document_id,
        "source_version": source_version,
    }


async def retrieve_chat_evidence(
    session: AsyncSession,
    *,
    context: dict[str, Any],
    question: str,
    need_kb: bool,
    retriever: Callable[..., Awaitable[list[dict[str, Any]]]] | None = None,
    reranker: Callable[..., Awaitable[list[dict[str, Any]]]] | None = None,
    top_k: int = 8,
) -> list[dict[str, Any]]:
    """Hybrid retrieval over the confirmed transcript and (optionally) the KB."""

    organization_id = UUID(str(context["meeting"].organization_id))
    meeting_id = UUID(str(context["meeting"].id))
    kb_id = context.get("kb_id")
    rows: list[dict[str, Any]] = []

    if context.get("confirmed_document_id") and kb_id:
        if retriever is not None:
            transcript_rows = await retriever(
                question, organization_id, UUID(kb_id), top_k, source="transcript"
            )
        else:
            transcript_rows = await retrieve_confirmed_transcript_chunks(
                session,
                query=question,
                organization_id=organization_id,
                meeting_id=meeting_id,
                confirmed_document_id=UUID(context["confirmed_document_id"]),
                knowledge_base_id=UUID(kb_id),
                source_version=context["source_version"],
                top_k=top_k,
            )
        rows.extend(transcript_rows)

    if need_kb and kb_id:
        if retriever is not None:
            kb_rows = await retriever(
                question, organization_id, UUID(kb_id), top_k, source="knowledge_base"
            )
        else:
            kb_rows = await retrieve_authoritative_chunks(
                session,
                query=question,
                organization_id=organization_id,
                knowledge_base_id=UUID(kb_id),
                top_k=top_k,
            )
        for row in kb_rows:
            row.setdefault("source_type", "knowledge_base")
        rows.extend(kb_rows)

    if not rows:
        return []
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        chunk_id = str(row.get("chunk_id") or "")
        if not chunk_id:
            continue
        current = merged.get(chunk_id)
        if current is None or float(row.get("fused_score") or 0) > float(
            current.get("fused_score") or 0
        ):
            merged[chunk_id] = row
    candidates = list(merged.values())
    if reranker is not None:
        ordered = await reranker(question, candidates, top_k)
    else:
        ordered = await rerank_chunks(question, candidates, top_k=top_k)

    kb_candidates = [
        row for row in ordered if row.get("source_type") != "confirmed_transcript"
    ]
    transcript_candidates = [
        row for row in ordered if row.get("source_type") == "confirmed_transcript"
    ]
    rehydrated: list[dict[str, Any]] = []
    if kb_candidates:
        if retriever is not None:
            rehydrated.extend(kb_candidates)
        else:
            rehydrated.extend(
                await rehydrate_authoritative_chunks(
                    session,
                    candidates=kb_candidates,
                    organization_id=organization_id,
                    knowledge_base_id=UUID(kb_id),
                )
            )
    if transcript_candidates:
        if retriever is not None:
            rehydrated.extend(transcript_candidates)
        else:
            rehydrated.extend(
                await rehydrate_confirmed_transcript_chunks(
                    session,
                    candidates=transcript_candidates,
                    organization_id=organization_id,
                    meeting_id=meeting_id,
                    confirmed_document_id=UUID(context["confirmed_document_id"]),
                    knowledge_base_id=UUID(kb_id),
                    source_version=context["source_version"],
                )
            )
    return rehydrated


def _block_sources(
    context: dict[str, Any], *, cap: int = 40
) -> list[dict[str, Any]]:
    """Fallback transcript sources from the confirmed revision blocks."""

    sources: list[dict[str, Any]] = []
    for index, block in enumerate(context.get("blocks") or [], start=1):
        if len(sources) >= cap:
            break
        text = (block.text or block.table_markdown or "").strip()
        if not text:
            continue
        block_id = str(block.block_id or "")
        speaker = block.speaker
        sources.append(
            {
                "id": f"transcript-block-{block_id or len(sources) + 1}",
                "index": index,
                "type": "transcript",
                "title": f"会议转写片段 · {speaker}" if speaker else "会议转写片段",
                "snippet": text[:200],
                "speaker_name": speaker,
                "timestamp": _format_time_range(block.start_ms, block.end_ms),
                "page_number": block.page_number,
                "document_id": context.get("confirmed_document_id"),
                "block_id": block_id or None,
                "content": text[:4000],
            }
        )
    return sources


def build_chat_sources(
    context: dict[str, Any], chunks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Convert retrieved chunks (or fallback blocks) into response sources."""

    if not chunks:
        return _block_sources(context)
    sources: list[dict[str, Any]] = []
    kb_name = context.get("kb_name")
    for index, chunk in enumerate(chunks, start=1):
        is_transcript = chunk.get("source_type") == "confirmed_transcript"
        content = str(chunk.get("content") or "")[:4000]
        if not content:
            continue
        chunk_id = str(chunk.get("chunk_id") or "")
        locator = chunk.get("source_locator") or {}
        speaker = chunk.get("speaker_name") or locator.get("speaker")
        timestamp = chunk.get("timestamp") or locator.get("time_range")
        document_id = chunk.get("document_id")
        sources.append(
            {
                "id": f"transcript-{chunk_id}" if is_transcript else f"kb-{chunk_id}",
                "index": index,
                "type": "transcript" if is_transcript else "knowledge_base",
                "title": (
                    "会议转写片段"
                    if is_transcript
                    else str(chunk.get("document_title") or "知识库片段")
                ),
                "snippet": content[:200],
                "speaker_name": speaker,
                "timestamp": timestamp,
                "page_number": chunk.get("page_number") or locator.get("page_number"),
                "chunk_id": chunk_id or None,
                "document_id": str(document_id) if document_id else None,
                "document_title": chunk.get("document_title"),
                "knowledge_base_name": kb_name,
                "block_id": chunk.get("block_id"),
                "content": content,
            }
        )
    return sources


def build_chat_materials(
    question: str,
    context: dict[str, Any],
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    registry = [
        {key: value for key, value in item.items() if value is not None}
        for item in sources
    ]
    return {
        "question": question,
        "meeting_context": context.get("meeting_context") or {},
        "confirmed_minutes": context.get("minutes") or "",
        "source_registry": registry,
    }


def _build_completed_chat_response(
    *,
    conversation_id: UUID,
    answer: str,
    sources: list[dict[str, Any]],
) -> MeetingChatResponse:
    normalized_answer = answer.strip() or INSUFFICIENT_ANSWER
    normalized_sources: list[dict[str, Any]] = []
    for index, item in enumerate(sources, start=1):
        normalized = {**item, "index": item.get("index") or index}
        if not normalized.get("id"):
            normalized["id"] = f"src-{normalized.get('chunk_id') or index}"
        normalized_sources.append(normalized)
    return MeetingChatResponse(
        conversation_id=conversation_id,
        message_id=uuid4(),
        answer=normalized_answer,
        status=(
            "INSUFFICIENT_CONTEXT"
            if normalized_answer == INSUFFICIENT_ANSWER
            else "COMPLETED"
        ),
        sources=[MeetingChatSource.model_validate(item) for item in normalized_sources],
        suggested_questions=[],
    )


async def generate_chat_answer(
    *,
    question: str,
    context: dict[str, Any],
    sources: list[dict[str, Any]],
    conversation_id: UUID | None,
    model_client: MeetingChatModelClient | None = None,
) -> MeetingChatResponse:
    """Generate one grounded answer; no sources means an explicit decline."""

    conversation = conversation_id or uuid4()
    message_id = uuid4()
    if not sources:
        return MeetingChatResponse(
            conversation_id=conversation,
            message_id=message_id,
            answer=INSUFFICIENT_ANSWER,
            status="INSUFFICIENT_CONTEXT",
            sources=[],
            suggested_questions=[],
        )

    client = model_client or MeetingChatModelClient()
    materials = build_chat_materials(question, context, sources)
    try:
        answer = await client.answer(materials)
    except AppException:
        raise
    except Exception as exc:
        raise AppException(502, "chat_generation_failed", "问答生成失败，请稍后重试") from exc

    response = _build_completed_chat_response(
        conversation_id=conversation,
        answer=answer,
        sources=sources,
    )
    response.message_id = message_id
    return response


async def get_or_create_chat_conversation(
    session: AsyncSession,
    *,
    meeting_id: UUID,
    organization_id: UUID,
    conversation_id: UUID | None = None,
    created_by: UUID | None = None,
) -> ChatConversation:
    """Resolve the explicit conversation or create a fresh one for the turn.

    An explicit id that does not exist or belongs to another meeting/org is
    rejected instead of silently creating a new thread, so stale frontend ids
    surface as a clear error rather than mixing history.
    """

    if conversation_id is not None:
        conversation = await session.get(ChatConversation, conversation_id)
        if (
            conversation is None
            or conversation.meeting_id != meeting_id
            or conversation.organization_id != organization_id
        ):
            raise NotFoundError("会话", "conversation_not_found")
        return conversation

    conversation = ChatConversation(
        meeting_id=meeting_id,
        organization_id=organization_id,
        created_by=created_by,
    )
    session.add(conversation)
    await session.flush()
    return conversation


async def load_chat_history(
    session: AsyncSession,
    *,
    conversation_id: UUID,
    limit: int,
) -> list[dict[str, str]]:
    """Load the most recent completed user->assistant turns for rewriting."""

    rows = list(
        (
            await session.scalars(
                select(ChatMessage)
                .where(ChatMessage.conversation_id == conversation_id)
                .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
                .limit(limit)
            )
        ).all()
    )
    rows.reverse()
    turns: list[dict[str, str]] = []
    for index, row in enumerate(rows):
        if (
            row.role == "user"
            and index + 1 < len(rows)
            and rows[index + 1].role == "assistant"
            and rows[index + 1].status != "FAILED"
        ):
            turns.append({"question": row.question, "answer": rows[index + 1].answer or ""})
    return turns


async def persist_user_message(
    session: AsyncSession,
    *,
    conversation_id: UUID,
    question: str,
    rewritten_question: str | None,
    scope: str,
) -> ChatMessage:
    message = ChatMessage(
        conversation_id=conversation_id,
        role="user",
        question=question,
        rewritten_question=rewritten_question,
        scope=scope,
    )
    session.add(message)
    await session.commit()
    return message


async def persist_assistant_message(
    session: AsyncSession,
    *,
    conversation_id: UUID,
    answer: str | None,
    status: str,
    scope: str,
    model: str | None,
    prompt_version: str | None,
    sources: list[dict[str, Any]],
    error_code: str | None = None,
    error_message: str | None = None,
) -> ChatMessage:
    message = ChatMessage(
        conversation_id=conversation_id,
        role="assistant",
        question="",
        answer=answer,
        status=status,
        scope=scope,
        model=model,
        prompt_version=prompt_version,
        sources=sources,
        error_code=error_code,
        error_message=error_message,
    )
    session.add(message)
    await session.commit()
    return message


async def stream_meeting_question(
    session: AsyncSession,
    *,
    meeting_id: UUID,
    payload: MeetingChatRequest,
    organization_id: UUID,
    created_by: UUID | None = None,
    model_client: MeetingChatModelClient | None = None,
    rewriter: MeetingChatRewriter | None = None,
    retriever: Callable[..., Awaitable[list[dict[str, Any]]]] | None = None,
    reranker: Callable[..., Awaitable[list[dict[str, Any]]]] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Run the grounded chat flow and emit stage, delta and done events.

    The conversation and every user/assistant message are persisted. The
    current question is rewritten against recent history (when present) before
    retrieval so follow-ups work without shipping raw history into the prompt.
    """

    need_kb = payload.scope == "MEETING_AND_KB"
    yield {"type": "stage", "stage": "RETRIEVING_MEETING"}
    context = await load_chat_context(
        session,
        meeting_id=meeting_id,
        organization_id=organization_id,
        need_kb=need_kb,
    )
    conversation = await get_or_create_chat_conversation(
        session,
        meeting_id=meeting_id,
        organization_id=organization_id,
        conversation_id=payload.conversation_id,
        created_by=created_by,
    )
    history = await load_chat_history(
        session,
        conversation_id=conversation.id,
        limit=get_settings().chat_history_max_messages,
    )
    rewrite_client = rewriter or MeetingChatRewriter()
    effective_question = await rewrite_client.rewrite(
        history=history, question=payload.question
    )
    await persist_user_message(
        session,
        conversation_id=conversation.id,
        question=payload.question,
        rewritten_question=(
            effective_question if effective_question != payload.question else None
        ),
        scope=payload.scope,
    )
    effective_need_kb = need_kb and context.get("kb") is not None
    if effective_need_kb:
        yield {"type": "stage", "stage": "RETRIEVING_KB"}
    chunks = await retrieve_chat_evidence(
        session,
        context=context,
        question=effective_question,
        need_kb=effective_need_kb,
        retriever=retriever,
        reranker=reranker,
    )
    sources = build_chat_sources(context, chunks)
    client = model_client or MeetingChatModelClient()
    if not sources:
        response = MeetingChatResponse(
            conversation_id=conversation.id,
            message_id=uuid4(),
            answer=INSUFFICIENT_ANSWER,
            status="INSUFFICIENT_CONTEXT",
            sources=[],
            suggested_questions=[],
        )
        message = await persist_assistant_message(
            session,
            conversation_id=conversation.id,
            answer=response.answer,
            status=response.status,
            scope=payload.scope,
            model=client.model_name,
            prompt_version=CHAT_PROMPT_VERSION,
            sources=[],
        )
        response.message_id = message.id
        yield {"type": "done", **response.model_dump(mode="json")}
        return

    yield {"type": "stage", "stage": "ORGANIZING"}
    materials = build_chat_materials(effective_question, context, sources)
    answer_parts: list[str] = []
    yield {"type": "stage", "stage": "STREAMING"}
    try:
        async for delta in client.stream_answer(materials):
            answer_parts.append(delta)
            yield {"type": "delta", "delta": delta}
    except Exception as exc:
        code = getattr(exc, "code", "chat_generation_failed")
        message = getattr(exc, "message", "问答生成失败，请稍后重试")
        await persist_assistant_message(
            session,
            conversation_id=conversation.id,
            answer=None,
            status="FAILED",
            scope=payload.scope,
            model=client.model_name,
            prompt_version=CHAT_PROMPT_VERSION,
            sources=[],
            error_code=code,
            error_message=message,
        )
        if isinstance(exc, AppException):
            raise
        raise AppException(502, "chat_generation_failed", "问答生成失败，请稍后重试") from exc

    response = _build_completed_chat_response(
        conversation_id=conversation.id,
        answer="".join(answer_parts),
        sources=sources,
    )
    message = await persist_assistant_message(
        session,
        conversation_id=conversation.id,
        answer=response.answer,
        status=response.status,
        scope=payload.scope,
        model=client.model_name,
        prompt_version=CHAT_PROMPT_VERSION,
        sources=[source.model_dump(mode="json") for source in response.sources],
    )
    response.message_id = message.id
    yield {"type": "done", **response.model_dump(mode="json")}


async def answer_meeting_question(
    session: AsyncSession,
    *,
    meeting_id: UUID,
    payload: MeetingChatRequest,
    organization_id: UUID,
    created_by: UUID | None = None,
    model_client: MeetingChatModelClient | None = None,
    rewriter: MeetingChatRewriter | None = None,
    retriever: Callable[..., Awaitable[list[dict[str, Any]]]] | None = None,
    reranker: Callable[..., Awaitable[list[dict[str, Any]]]] | None = None,
) -> MeetingChatResponse:
    """Answer one turn with persistence and follow-up rewriting (JSON mode)."""

    need_kb = payload.scope == "MEETING_AND_KB"
    context = await load_chat_context(
        session,
        meeting_id=meeting_id,
        organization_id=organization_id,
        need_kb=need_kb,
    )
    conversation = await get_or_create_chat_conversation(
        session,
        meeting_id=meeting_id,
        organization_id=organization_id,
        conversation_id=payload.conversation_id,
        created_by=created_by,
    )
    history = await load_chat_history(
        session,
        conversation_id=conversation.id,
        limit=get_settings().chat_history_max_messages,
    )
    rewrite_client = rewriter or MeetingChatRewriter()
    effective_question = await rewrite_client.rewrite(
        history=history, question=payload.question
    )
    await persist_user_message(
        session,
        conversation_id=conversation.id,
        question=payload.question,
        rewritten_question=(
            effective_question if effective_question != payload.question else None
        ),
        scope=payload.scope,
    )
    effective_need_kb = need_kb and context.get("kb") is not None
    chunks = await retrieve_chat_evidence(
        session,
        context=context,
        question=effective_question,
        need_kb=effective_need_kb,
        retriever=retriever,
        reranker=reranker,
    )
    sources = build_chat_sources(context, chunks)
    client = model_client or MeetingChatModelClient()
    try:
        response = await generate_chat_answer(
            question=effective_question,
            context=context,
            sources=sources,
            conversation_id=conversation.id,
            model_client=client,
        )
    except Exception as exc:
        code = getattr(exc, "code", "chat_generation_failed")
        message = getattr(exc, "message", "问答生成失败，请稍后重试")
        await persist_assistant_message(
            session,
            conversation_id=conversation.id,
            answer=None,
            status="FAILED",
            scope=payload.scope,
            model=client.model_name,
            prompt_version=CHAT_PROMPT_VERSION,
            sources=[],
            error_code=code,
            error_message=message,
        )
        raise
    message = await persist_assistant_message(
        session,
        conversation_id=conversation.id,
        answer=response.answer,
        status=response.status,
        scope=payload.scope,
        model=client.model_name,
        prompt_version=CHAT_PROMPT_VERSION,
        sources=[source.model_dump(mode="json") for source in response.sources],
    )
    response.message_id = message.id
    return response
