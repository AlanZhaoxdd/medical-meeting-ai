from __future__ import annotations

import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUserDependency
from app.db.session import get_session
from app.schemas.analysis import MeetingChatRequest, MeetingChatResponse
from app.services.meeting_chat import answer_meeting_question, stream_meeting_question

router = APIRouter(prefix="/meetings", tags=["会议智能问答"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]


@router.post(
    "/{meeting_id}/ai-chat",
    response_model=MeetingChatResponse,
    summary="会议智能问答",
    description=(
        "基于确认版会议纪要（以及可选的已发布知识库）检索相关内容，"
        "由 LLM 生成带引用来源的答案；材料不足时返回 INSUFFICIENT_CONTEXT。"
    ),
)
async def meeting_chat(
    meeting_id: UUID,
    payload: MeetingChatRequest,
    session: SessionDependency,
    current: CurrentUserDependency,
    request: Request,
) -> MeetingChatResponse | StreamingResponse:
    if "text/event-stream" in request.headers.get("accept", ""):
        async def events():
            try:
                async for event in stream_meeting_question(
                    session,
                    meeting_id=meeting_id,
                    payload=payload,
                    organization_id=current.organization_id,
                    created_by=current.user_id,
                ):
                    yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
            except Exception as exc:
                code = getattr(exc, "code", "chat_generation_failed")
                message = getattr(exc, "message", "问答生成失败，请稍后重试")
                yield f"data: {json.dumps({'type': 'error', 'code': code, 'message': message}, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    return await answer_meeting_question(
        session,
        meeting_id=meeting_id,
        payload=payload,
        organization_id=current.organization_id,
        created_by=current.user_id,
    )
