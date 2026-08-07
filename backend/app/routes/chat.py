"""路由模块: chat"""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..auth import get_profile
from ..config import get_config, save_config, PROVIDER_PRESETS
from ..deps import get_current_user, require_admin

router = APIRouter()

from .. import chat as chat_service


@router.post("/api/chat/session")
def new_chat(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    sid = chat_service.create_session(user["id"])
    return {"session_id": sid}



@router.post("/api/chat/stream")
def chat_stream(body: dict[str, Any], user: dict[str, Any] = Depends(get_current_user)) -> StreamingResponse:
    """流式对话（SSE）：实时推送工具调用工作流事件。"""
    message = str(body.get("message", "")).strip()
    if not message:
        raise HTTPException(400, "消息不能为空")
    session_id = body.get("session_id")
    if session_id is None:
        session_id = chat_service.create_session(user["id"])
    session_id = int(session_id)
    return StreamingResponse(
        chat_service.stream_chat(session_id, user["id"], message),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )



@router.get("/api/chat/sessions")
def chat_sessions(user: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    return chat_service.list_sessions(user["id"])



@router.delete("/api/chat/{session_id}")
def delete_chat(session_id: int, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    ok = chat_service.delete_session(session_id, user["id"])
    if not ok:
        raise HTTPException(404, "会话不存在或无权限")
    return {"deleted": session_id}



@router.get("/api/chat/search")
def chat_search(q: str, user: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    if not q.strip():
        return []
    return chat_service.search_messages(user["id"], q.strip())



@router.get("/api/chat/{session_id}/messages")
def chat_messages(session_id: int, user: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    return chat_service.get_messages(session_id, user["id"])



@router.post("/api/chat")
def chat(body: dict[str, Any], user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "消息不能为空")
    session_id = body.get("session_id")
    if not session_id:
        session_id = chat_service.create_session(user["id"])
    # 校验会话归属
    sessions = {s["id"] for s in chat_service.list_sessions(user["id"], limit=100)}
    if int(session_id) not in sessions:
        raise HTTPException(403, "会话不存在或无权限")
    return chat_service.chat(int(session_id), user["id"], message)


# ---------- 管理员 API ----------

