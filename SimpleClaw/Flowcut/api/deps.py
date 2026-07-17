"""FastAPI 鉴权依赖：从登录会话 cookie 解析当前租户/用户。"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from Flowcut.auth.security import hash_token
from Flowcut.config import make_auth_config

_COOKIE_NAME = make_auth_config()["cookie_name"]


async def _resolve_session(request: Request) -> dict[str, Any]:
    """读 cookie → 查未过期登录会话，失败抛 401。"""
    raw = request.cookies.get(_COOKIE_NAME)
    if not raw:
        raise HTTPException(status_code=401, detail="未登录")
    container = request.app.state.container
    sess = await container.login_session_repo.get_valid(hash_token(raw))
    if sess is None:
        raise HTTPException(status_code=401, detail="会话已过期，请重新登录")
    return sess


async def require_tenant(request: Request) -> str:
    """返回当前登录会话的 tenant_key。"""
    sess = await _resolve_session(request)
    return str(sess["tenant_key"])


async def require_user(request: Request) -> dict[str, Any]:
    """返回当前登录用户（含 tenant_key）。用户被禁用或不存在视为未授权。"""
    sess = await _resolve_session(request)
    container = request.app.state.container
    user = await container.user_repo.get_by_id(int(sess["user_id"]))
    if user is None or int(user.get("disabled", 0)) == 1:
        raise HTTPException(status_code=401, detail="账号不可用")
    return user
