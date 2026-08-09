from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.core.exceptions import NotFoundError
from app.models.chat import ChatConversation, ChatMessage
from app.models.kb import KnowledgeBase, Organization, User
from app.models.meeting import Meeting
from app.schemas.analysis import MeetingChatRequest
from app.services.meeting_chat import (
    MeetingChatModelClient,
    MeetingChatRewriter,
    answer_meeting_question,
    get_or_create_chat_conversation,
    load_chat_history,
    persist_assistant_message,
    persist_user_message,
)


def _meeting_start() -> datetime:
    return datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)


async def _seed(session, *, meetings: int = 1) -> dict[str, object]:
    """Seed a user/organization/KB plus one or more meetings."""

    user = User(
        id=uuid4(),
        email=f"chat-{uuid4()}@example.com",
        display_name="测试用户",
        password_hash="unused",
    )
    session.add(user)
    await session.flush()
    org = Organization(id=uuid4(), name="测试组织", created_by=user.id)
    session.add(org)
    await session.flush()
    kb = KnowledgeBase(
        id=uuid4(),
        organization_id=org.id,
        name="指南库",
        description="",
    )
    session.add(kb)
    await session.flush()

    meeting_ids: list[object] = []
    for _ in range(meetings):
        meeting = Meeting(
            id=uuid4(),
            organization_id=org.id,
            knowledge_base_id=kb.id,
            title="测试会议",
            starts_at=_meeting_start(),
            ends_at=_meeting_start() + timedelta(hours=1),
        )
        session.add(meeting)
        await session.flush()
        meeting_ids.append(meeting.id)
    await session.commit()
    return {
        "user_id": user.id,
        "organization_id": org.id,
        "kb_id": kb.id,
        "meeting_ids": meeting_ids,
    }


async def _count_messages(session, conversation_id) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
        )
    )


async def test_get_or_create_conversation_reuses_explicit_id(
    test_session_factory,
) -> None:
    async with test_session_factory() as session:
        seed = await _seed(session)
        meeting_id = seed["meeting_ids"][0]
        org_id = seed["organization_id"]

        conversation = await get_or_create_chat_conversation(
            session,
            meeting_id=meeting_id,
            organization_id=org_id,
        )
        assert conversation.id is not None
        same = await get_or_create_chat_conversation(
            session,
            meeting_id=meeting_id,
            organization_id=org_id,
            conversation_id=conversation.id,
        )
        assert same.id == conversation.id
        total = int(
            await session.scalar(
                select(func.count()).select_from(ChatConversation)
            )
        )
        assert total == 1


async def test_get_or_create_rejects_cross_meeting_conversation(
    test_session_factory,
) -> None:
    async with test_session_factory() as session:
        seed = await _seed(session, meetings=2)
        org_id = seed["organization_id"]
        first, second = seed["meeting_ids"]
        conversation = await get_or_create_chat_conversation(
            session,
            meeting_id=first,
            organization_id=org_id,
        )
        with pytest.raises(NotFoundError) as excinfo:
            await get_or_create_chat_conversation(
                session,
                meeting_id=second,
                organization_id=org_id,
                conversation_id=conversation.id,
            )
        assert excinfo.value.code == "conversation_not_found"


async def test_load_chat_history_returns_completed_turns_and_skips_failed(
    test_session_factory,
) -> None:
    async with test_session_factory() as session:
        seed = await _seed(session)
        meeting_id = seed["meeting_ids"][0]
        org_id = seed["organization_id"]
        conversation = await get_or_create_chat_conversation(
            session,
            meeting_id=meeting_id,
            organization_id=org_id,
        )
        await persist_user_message(
            session,
            conversation_id=conversation.id,
            question="剂量是多少？",
            rewritten_question=None,
            scope="MEETING_AND_KB",
        )
        await persist_assistant_message(
            session,
            conversation_id=conversation.id,
            answer="一天两次。",
            status="COMPLETED",
            scope="MEETING_AND_KB",
            model="test-model",
            prompt_version="meeting-chat-v1",
            sources=[],
        )
        await persist_user_message(
            session,
            conversation_id=conversation.id,
            question="那副作用呢？",
            rewritten_question="该药物的副作用有哪些？",
            scope="MEETING_AND_KB",
        )
        await persist_assistant_message(
            session,
            conversation_id=conversation.id,
            answer=None,
            status="FAILED",
            scope="MEETING_AND_KB",
            model="test-model",
            prompt_version="meeting-chat-v1",
            sources=[],
            error_code="chat_generation_failed",
        )

        history = await load_chat_history(
            session,
            conversation_id=conversation.id,
            limit=8,
        )
        assert len(history) == 1
        assert history[0]["question"] == "剂量是多少？"
        assert history[0]["answer"] == "一天两次。"


async def test_answer_meeting_question_persists_and_rewrites_follow_up(
    test_session_factory,
) -> None:
    async with test_session_factory() as session:
        seed = await _seed(session)
        meeting_id = seed["meeting_ids"][0]
        org_id = seed["organization_id"]

        retrieved: list[str] = []

        async def retriever(question, organization_id, kb_id, top_k, source="knowledge_base"):
            retrieved.append(question)
            return [
                {
                    "chunk_id": "k1",
                    "content": "指南规定随访周期为一个月。",
                    "source_type": "knowledge_base",
                    "fused_score": 1.0,
                    "document_title": "随访指南.pdf",
                }
            ]

        async def reranker(question, candidates, top_k):
            return candidates

        async def model_generator(payload):
            return "随访周期为一个月。 [1]"

        async def rewrite_generator(prompt):
            assert "那副作用呢" in prompt
            return "该药物的副作用有哪些？"

        model_client = MeetingChatModelClient(generator=model_generator)
        rewriter = MeetingChatRewriter(generator=rewrite_generator)

        first = await answer_meeting_question(
            session,
            meeting_id=meeting_id,
            payload=MeetingChatRequest(
                meeting_id=meeting_id,
                question="该药物剂量是多少？",
            ),
            organization_id=org_id,
            model_client=model_client,
            rewriter=rewriter,
            retriever=retriever,
            reranker=reranker,
        )
        assert first.status == "COMPLETED"
        assert retrieved == ["该药物剂量是多少？"]

        second = await answer_meeting_question(
            session,
            meeting_id=meeting_id,
            payload=MeetingChatRequest(
                meeting_id=meeting_id,
                conversation_id=first.conversation_id,
                question="那副作用呢？",
            ),
            organization_id=org_id,
            model_client=model_client,
            rewriter=rewriter,
            retriever=retriever,
            reranker=reranker,
        )
        assert second.status == "COMPLETED"
        assert retrieved[-1] == "该药物的副作用有哪些？"
        assert second.conversation_id == first.conversation_id

        rows = list(
            (
                await session.scalars(
                    select(ChatMessage)
                    .where(ChatMessage.conversation_id == first.conversation_id)
                    .order_by(ChatMessage.created_at, ChatMessage.id)
                )
            ).all()
        )
        assert len(rows) == 4
        assert rows[0].role == "user"
        assert rows[0].rewritten_question is None
        assert rows[1].role == "assistant"
        assert rows[1].status == "COMPLETED"
        assert rows[2].question == "那副作用呢？"
        assert rows[2].rewritten_question == "该药物的副作用有哪些？"
        assert rows[3].status == "COMPLETED"
        assert rows[3].sources[0]["chunk_id"] == "k1"
        assert second.message_id == rows[3].id


async def test_answer_meeting_question_persists_failed_turn(
    test_session_factory,
) -> None:
    async with test_session_factory() as session:
        seed = await _seed(session)
        meeting_id = seed["meeting_ids"][0]
        org_id = seed["organization_id"]

        async def retriever(question, organization_id, kb_id, top_k, source="knowledge_base"):
            return [
                {
                    "chunk_id": "k1",
                    "content": "正文",
                    "source_type": "knowledge_base",
                    "fused_score": 1.0,
                }
            ]

        async def reranker(question, candidates, top_k):
            return candidates

        async def broken_generator(payload):
            raise RuntimeError("boom")

        client = MeetingChatModelClient(generator=broken_generator)
        with pytest.raises(Exception) as excinfo:
            await answer_meeting_question(
                session,
                meeting_id=meeting_id,
                payload=MeetingChatRequest(
                    meeting_id=meeting_id,
                    question="问题",
                ),
                organization_id=org_id,
                model_client=client,
                retriever=retriever,
                reranker=reranker,
            )
        assert getattr(excinfo.value, "code", None) == "chat_generation_failed"

        conversation = await session.scalar(
            select(ChatConversation).where(
                ChatConversation.meeting_id == meeting_id,
                ChatConversation.organization_id == org_id,
            )
        )
        assert conversation is not None
        rows = list(
            (
                await session.scalars(
                    select(ChatMessage)
                    .where(ChatMessage.conversation_id == conversation.id)
                    .order_by(ChatMessage.created_at, ChatMessage.id)
                )
            ).all()
        )
        assert len(rows) == 2
        assert rows[0].role == "user"
        assert rows[1].role == "assistant"
        assert rows[1].status == "FAILED"
        assert rows[1].error_code == "chat_generation_failed"
