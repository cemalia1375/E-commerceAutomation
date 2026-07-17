"""账号体系路由：/auth/login、/auth/logout、/auth/me。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from Flowcut.api.deps import require_user
from Flowcut.auth.security import new_session_token, verify_password
from Flowcut.config import make_auth_config

router = APIRouter(prefix="/auth", tags=["auth"])

_AUTH_CFG = make_auth_config()


class LoginRequest(BaseModel):
    username: str
    password: str


def _public(user: dict[str, Any]) -> dict[str, Any]:
    """对外暴露的用户字段（不含 password_hash）。"""
    return {
        "username": user["username"],
        "tenant_key": user["tenant_key"],
        "display_name": user.get("display_name") or user["username"],
    }


@router.post("/login")
async def login(request: Request, body: LoginRequest, response: Response) -> dict[str, Any]:
    container = request.app.state.container
    user = await container.user_repo.get_by_username(body.username.strip())
    if (
        user is None
        or int(user.get("disabled", 0)) == 1
        or not verify_password(body.password, user["password_hash"])
    ):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    raw_token, token_hash = new_session_token()
    ttl = int(_AUTH_CFG["session_ttl_seconds"])
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl)
    await container.login_session_repo.create(
        session_id_hash=token_hash,
        user_id=int(user["id"]),
        tenant_key=user["tenant_key"],
        expires_at=expires_at,
    )
    response.set_cookie(
        key=_AUTH_CFG["cookie_name"],
        value=raw_token,
        max_age=ttl,
        httponly=True,
        secure=bool(_AUTH_CFG["cookie_secure"]),
        samesite=_AUTH_CFG["cookie_samesite"],
        path="/",
    )
    return {"ok": True, **_public(user)}


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict[str, Any]:
    raw = request.cookies.get(_AUTH_CFG["cookie_name"])
    if raw:
        from Flowcut.auth.security import hash_token

        await request.app.state.container.login_session_repo.delete(hash_token(raw))
    response.delete_cookie(key=_AUTH_CFG["cookie_name"], path="/")
    return {"ok": True}


@router.get("/me")
async def me(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    return {"ok": True, **_public(user)}
