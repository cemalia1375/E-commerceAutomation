"""Session ingress 存储协议与默认内存实现。"""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from typing import Protocol

from simpleclaw.runtime.session_ingress_protocol import (
    SessionIngressItem,
    SessionIngressStatus,
)


class SessionIngressStore(Protocol):
    """Storage protocol for queued session ingress items."""

    async def enqueue(self, item: SessionIngressItem) -> SessionIngressItem: ...

    async def get(self, ingress_id: str) -> SessionIngressItem | None: ...

    async def get_next_queued(self, session_key: str) -> SessionIngressItem | None: ...

    async def list_pending(
        self,
        *,
        session_key: str | None = None,
        limit: int = 50,
    ) -> list[SessionIngressItem]: ...

    async def find_newer_queued(
        self,
        *,
        session_key: str,
        dedupe_key: str,
        after_created_at_ms: int,
        exclude_ingress_id: str | None = None,
    ) -> SessionIngressItem | None: ...

    async def find_newer_user_message(
        self,
        *,
        session_key: str,
        after_created_at_ms: int,
        exclude_ingress_id: str | None = None,
    ) -> SessionIngressItem | None: ...

    async def mark_dispatching(
        self,
        ingress_id: str,
        *,
        summary: str | None = None,
    ) -> SessionIngressItem | None: ...

    async def mark_delivered(
        self,
        ingress_id: str,
        *,
        summary: str | None = None,
    ) -> SessionIngressItem | None: ...

    async def mark_dropped(
        self,
        ingress_id: str,
        *,
        summary: str | None = None,
    ) -> SessionIngressItem | None: ...

    async def mark_superseded(
        self,
        ingress_id: str,
        *,
        summary: str | None = None,
    ) -> SessionIngressItem | None: ...

    async def mark_expired(
        self,
        ingress_id: str,
        *,
        summary: str | None = None,
    ) -> SessionIngressItem | None: ...

    async def mark_failed(
        self,
        ingress_id: str,
        error: str,
        *,
        summary: str | None = None,
    ) -> SessionIngressItem | None: ...


class InMemorySessionIngressStore:
    """Process-local ingress queue store for development and tests."""

    _PENDING_STATUSES = {"queued", "dispatching"}

    def __init__(self) -> None:
        self._items: dict[str, SessionIngressItem] = {}
        self._lock = asyncio.Lock()

    async def enqueue(self, item: SessionIngressItem) -> SessionIngressItem:
        async with self._lock:
            stored = replace(item, status="queued", updated_at_ms=_now_ms())
            self._items[stored.ingress_id] = stored
            return stored

    async def get(self, ingress_id: str) -> SessionIngressItem | None:
        async with self._lock:
            return self._items.get(ingress_id)

    async def get_next_queued(self, session_key: str) -> SessionIngressItem | None:
        async with self._lock:
            queued = [
                item
                for item in self._items.values()
                if item.session_key == session_key and item.status == "queued"
            ]
            if not queued:
                return None
            queued.sort(key=_pending_sort_key)
            return queued[0]

    async def list_pending(
        self,
        *,
        session_key: str | None = None,
        limit: int = 50,
    ) -> list[SessionIngressItem]:
        async with self._lock:
            items = [
                item
                for item in self._items.values()
                if item.status in self._PENDING_STATUSES
                and (session_key is None or item.session_key == session_key)
            ]
            items.sort(key=_pending_sort_key)
            return items[:limit]

    async def find_newer_queued(
        self,
        *,
        session_key: str,
        dedupe_key: str,
        after_created_at_ms: int,
        exclude_ingress_id: str | None = None,
    ) -> SessionIngressItem | None:
        async with self._lock:
            candidates = [
                item
                for item in self._items.values()
                if item.session_key == session_key
                and item.status == "queued"
                and item.dedupe_key == dedupe_key
                and item.created_at_ms > after_created_at_ms
                and item.ingress_id != exclude_ingress_id
            ]
            if not candidates:
                return None
            candidates.sort(key=lambda item: (item.created_at_ms, item.ingress_id), reverse=True)
            return candidates[0]

    async def find_newer_user_message(
        self,
        *,
        session_key: str,
        after_created_at_ms: int,
        exclude_ingress_id: str | None = None,
    ) -> SessionIngressItem | None:
        async with self._lock:
            candidates = [
                item
                for item in self._items.values()
                if item.session_key == session_key
                and item.message_type == "user_message"
                and item.created_at_ms > after_created_at_ms
                and item.ingress_id != exclude_ingress_id
                and item.status in {"queued", "dispatching", "delivered"}
            ]
            if not candidates:
                return None
            candidates.sort(key=lambda item: (item.created_at_ms, item.ingress_id), reverse=True)
            return candidates[0]

    async def mark_dispatching(
        self,
        ingress_id: str,
        *,
        summary: str | None = None,
    ) -> SessionIngressItem | None:
        return await self._mark(ingress_id, "dispatching", summary=summary, error=None)

    async def mark_delivered(
        self,
        ingress_id: str,
        *,
        summary: str | None = None,
    ) -> SessionIngressItem | None:
        return await self._mark(ingress_id, "delivered", summary=summary, error=None)

    async def mark_dropped(
        self,
        ingress_id: str,
        *,
        summary: str | None = None,
    ) -> SessionIngressItem | None:
        return await self._mark(ingress_id, "dropped", summary=summary, error=None)

    async def mark_superseded(
        self,
        ingress_id: str,
        *,
        summary: str | None = None,
    ) -> SessionIngressItem | None:
        return await self._mark(ingress_id, "superseded", summary=summary, error=None)

    async def mark_expired(
        self,
        ingress_id: str,
        *,
        summary: str | None = None,
    ) -> SessionIngressItem | None:
        return await self._mark(ingress_id, "expired", summary=summary, error=None)

    async def mark_failed(
        self,
        ingress_id: str,
        error: str,
        *,
        summary: str | None = None,
    ) -> SessionIngressItem | None:
        return await self._mark(ingress_id, "failed", summary=summary, error=error)

    async def _mark(
        self,
        ingress_id: str,
        status: SessionIngressStatus,
        *,
        summary: str | None,
        error: str | None,
    ) -> SessionIngressItem | None:
        async with self._lock:
            item = self._items.get(ingress_id)
            if item is None:
                return None
            updated = replace(
                item,
                status=status,
                summary=summary if summary is not None else item.summary,
                error=error,
                updated_at_ms=_now_ms(),
            )
            self._items[ingress_id] = updated
            return updated


def _now_ms() -> int:
    return int(time.time() * 1000)


def _priority_rank(item: SessionIngressItem) -> int:
    return 0 if item.priority == "high" else 1


def _pending_sort_key(item: SessionIngressItem) -> tuple[int, int, str]:
    return (_priority_rank(item), item.created_at_ms, item.ingress_id)
