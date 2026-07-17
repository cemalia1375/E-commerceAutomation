"""ScaffoldClient — 封装对 Mojing API + Admin API 的 HTTP 调用。

所有方法返回结构化结果，SSE 流自动解析为 (chunks, full_reply, done, error)。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class ChatResult:
    full_reply: str
    chunks: list[str] = field(default_factory=list)
    done: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.done and not self.error and bool(self.full_reply)


class ScaffoldClient:
    def __init__(self, base_url: str = "http://localhost:8000", timeout: float = 60.0):
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    # ------------------------------------------------------------------
    # /agent/chat  (SSE)
    # ------------------------------------------------------------------

    async def chat(
        self,
        user_id: str,
        message: str,
        session_id: str | None = None,
    ) -> ChatResult:
        """向 /agent/chat 发送消息，解析 SSE 流，返回 ChatResult。"""
        payload: dict[str, Any] = {"user_id": user_id, "message": message}
        if session_id:
            payload["session_id"] = session_id

        chunks: list[str] = []
        done = False
        error: str | None = None

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream(
                "POST",
                f"{self._base}/agent/chat",
                json=payload,
                headers={"Accept": "text/event-stream"},
            ) as resp:
                resp.raise_for_status()
                buf = ""
                async for raw in resp.aiter_text():
                    buf += raw
                    while "\n\n" in buf:
                        block, buf = buf.split("\n\n", 1)
                        for line in block.splitlines():
                            if not line.startswith("data: "):
                                continue
                            try:
                                evt = json.loads(line[6:])
                            except Exception:
                                continue
                            t = evt.get("type", "")
                            if t == "chunk":
                                text = (evt.get("data") or {}).get("text") or ""
                                if text:
                                    chunks.append(text)
                            elif t == "done":
                                done = True
                            elif t == "error":
                                error = (evt.get("data") or {}).get("error") or "unknown"

        return ChatResult(
            full_reply="".join(chunks),
            chunks=chunks,
            done=done,
            error=error,
        )

    # ------------------------------------------------------------------
    # Admin API
    # ------------------------------------------------------------------

    async def get_session_history(
        self,
        tenant_key: str,
        session_key: str,
        limit: int = 20,
    ) -> list[dict]:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{self._base}/admin/session/history",
                params={"tenant_key": tenant_key, "session_key": session_key, "limit": limit},
            )
            r.raise_for_status()
            return r.json().get("messages", [])

    async def get_tenant_docs(self, tenant_key: str) -> dict:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{self._base}/admin/tenant/docs",
                params={"tenant_key": tenant_key},
            )
            r.raise_for_status()
            return r.json()

    async def get_dynamic_state(self, tenant_key: str) -> dict:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{self._base}/admin/tenant/dynamic_state",
                params={"tenant_key": tenant_key},
            )
            r.raise_for_status()
            return r.json()

    async def get_prompt(self, file_key: str) -> dict:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{self._base}/admin/prompt",
                params={"file": file_key},
            )
            r.raise_for_status()
            return r.json()

    async def put_prompt(self, file_key: str, content: str) -> bool:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.put(
                f"{self._base}/admin/prompt",
                params={"file": file_key},
                content=content.encode("utf-8"),
                headers={"Content-Type": "text/plain; charset=utf-8"},
            )
            return r.status_code == 204

    async def journey_event(self, tenant_key: str, event: str) -> dict:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{self._base}/journey/event",
                json={"tenant_key": tenant_key, "event": event},
            )
            r.raise_for_status()
            return r.json()

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{self._base}/health")
                return r.status_code == 200
        except Exception:
            return False
