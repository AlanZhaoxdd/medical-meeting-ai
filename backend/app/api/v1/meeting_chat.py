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
        "轻量 Agent 问答：LLM 先判断问题类型并路由——会议/知识库相关问题走"
        "混合检索并生成带引用来源的答案（材料不足返回 INSUFFICIENT_CONTEXT），"
        "与会议/知识库无关的通用问题直接由 LLM 回答，"
        "隐私、违法或代替诊疗等请求明确拒绝。"
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
                error_payload = {
                    "type": "error",
                    "code": code,
                    "message": message,
                }
                yield f"data: {json.dumps(error_payload, ensure_ascii=False)}\n\n"

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
