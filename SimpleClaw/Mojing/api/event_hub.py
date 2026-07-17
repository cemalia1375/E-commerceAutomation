"""In-memory SSE event hub for online admin/client sessions."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, AsyncIterator

from loguru import logger


class EventHub:
    """按 (tenant_key, session_key) 隔离的轻量事件总线。"""

    def __init__(self) -> None:
        self._queues: dict[tuple[str, str], set[asyncio.Queue[dict[str, Any] | None]]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def subscribe(
        self,
        tenant_key: str,
        session_key: str,
    ) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=128)
        key = (tenant_key, session_key)
        async with self._lock:
            self._queues[key].add(queue)
            subscriber_count = len(self._queues[key])
        logger.info(
            "EventHub: subscriber connected tenant={} session={} subscribers={}",
            tenant_key, session_key, subscriber_count,
        )
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        finally:
            async with self._lock:
                queues = self._queues.get(key)
                if queues is not None:
                    queues.discard(queue)
                    subscriber_count = len(queues)
                    if not queues:
                        self._queues.pop(key, None)
                        subscriber_count = 0
            logger.info(
                "EventHub: subscriber disconnected tenant={} session={} subscribers={}",
                tenant_key, session_key, subscriber_count,
            )

    async def publish(self, tenant_key: str, session_key: str, event: dict[str, Any]) -> int:
        """向在线订阅者发布事件；返回成功投递的订阅者数量。"""
        key = (tenant_key, session_key)
        async with self._lock:
            queues = list(self._queues.get(key) or [])

        delivered = 0
        stale: list[asyncio.Queue[dict[str, Any] | None]] = []
        for queue in queues:
            try:
                queue.put_nowait(event)
                delivered += 1
            except asyncio.QueueFull:
                stale.append(queue)

        if stale:
            async with self._lock:
                active = self._queues.get(key)
                if active is not None:
                    for queue in stale:
                        active.discard(queue)
                    if not active:
                        self._queues.pop(key, None)
        return delivered
