"""Health check 与 admin 重定向"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

router = APIRouter()


@router.get("/admin")
async def admin_redirect():
    return RedirectResponse(url="/admin/editor")


@router.get("/health")
async def health(request: Request):
    c = request.app.state.container
    return {
        "ok": True,
        "active_sessions": c.sessions.active_sessions if c.sessions else [],
    }
