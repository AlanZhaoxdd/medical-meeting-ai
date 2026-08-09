from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ChatConversation(Base):
    """One persisted chat thread for a meeting.

    Messages are stored for audit and for follow-up rewriting; the backend
    itself stays stateless per request and re-retrieves evidence every turn.
    """

    __tablename__ = "chat_conversations"
    __table_args__ = (
        Index("ix_chat_conversations_meeting_created", "meeting_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    meeting_id: Mapped[UUID] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[Optional[UUID]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class ChatMessage(Base):
    """One persisted user/assistant message in a chat conversation.

    ``question`` keeps the original user input; ``rewritten_question`` records
    the self-contained query actually used for retrieval when a rewrite
    happened, which doubles as an audit trail for follow-up turns.
    """

    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_chat_messages_conversation_created", "conversation_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("chat_conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    rewritten_question: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    answer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    scope: Mapped[str] = mapped_column(
        String(32), nullable=False, default="MEETING_AND_KB", server_default="MEETING_AND_KB"
    )
    model: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    prompt_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    sources: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'")
    )
    error_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
