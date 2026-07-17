"""Sessions 路由：CRUD 会话 + 消息历史（支持前端聊天记录恢复）。"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from Flowcut.api.deps import require_tenant

from Flowcut.api.container import AppContainer

router = APIRouter(prefix="/sessions", tags=["sessions"])


class CreateSessionRequest(BaseModel):
    session_key: str | None = None   # 客户端生成的 session_key（可选，不传则服务端生成）
    title: str | None = None


def _get_container(request: Request) -> AppContainer:
    return request.app.state.container


@router.post("")
async def create_session(
    body: CreateSessionRequest,
    request: Request,
    tenant_key: str = Depends(require_tenant),
):
    """创建新会话并返回 session_key。

    session_key 可由客户端预生成并传入，也可由服务端生成。
    预生成的好处：前端在 localStorage 丢失后仍可通过 GET /sessions 恢复。
    """
    c = _get_container(request)
    session_key = body.session_key or uuid.uuid4().hex[:12]
    row = await c.session_repo.create_session(
        tenant_key=tenant_key,
        session_key=session_key,
        title=body.title,
    )
    return row


@router.get("")
async def list_sessions(
    request: Request,
    tenant_key: str = Depends(require_tenant),
    limit: int = 50,
    offset: int = 0,
):
    """列出租户下的所有会话（按更新时间倒序），含消息数。

    前端挂载时调用此接口恢复最近的会话。
    """
    c = _get_container(request)
    return await c.session_repo.list_by_tenant(
        tenant_key, limit=limit, offset=offset,
    )


@router.delete("/{session_key}")
async def delete_session(
    session_key: str,
    request: Request,
    tenant_key: str = Depends(require_tenant),
):
    """删除指定会话及其全部消息。"""
    c = _get_container(request)
    # 同时清除内存缓存（避免残留 ReactLoop）
    c.sessions.evict(session_key)
    await c.session_repo.delete_session(tenant_key, session_key)
    return {"ok": True}


@router.patch("/{session_key}")
async def update_session(
    session_key: str,
    body: CreateSessionRequest,
    request: Request,
    tenant_key: str = Depends(require_tenant),
):
    """更新会话元数据（标题等）。"""
    c = _get_container(request)
    if body.title is not None:
        await c.session_repo.update_title(tenant_key, session_key, body.title)
    return {"ok": True}


@router.get("/{session_key}/messages")
async def get_session_messages(
    session_key: str,
    request: Request,
    tenant_key: str = Depends(require_tenant),
    offset: int = 0,
    limit: Optional[int] = None,
):
    """返回某个会话的消息历史（供前端恢复对话面板）。

    返回格式：{ session_key, messages, last_consolidated }
    messages 中每条是 OpenAI 字典格式 {role, content, tool_calls?, ...}

    支持分页：offset/limit 用于按需加载历史消息。
    """
    c = _get_container(request)
    messages, last_consolidated = await c.session_repo.load_messages(
        tenant_key, session_key,
        offset=offset,
        limit=limit,
    )
    return {
        "session_key": session_key,
        "messages": messages,
        "last_consolidated": last_consolidated,
    }
