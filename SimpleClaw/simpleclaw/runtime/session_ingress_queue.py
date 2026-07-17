"""Session ingress queue facade."""

from __future__ import annotations

from simpleclaw.runtime.session_ingress_protocol import SessionIngressItem
from simpleclaw.runtime.session_ingress_state import SessionIngressStore


class SessionIngressQueue:
    """Thin queue facade over the configured ingress store.

    这层保持轻量：负责入队，以及为 scheduler 提供队列读写入口。
    真正的 idle/busy 判断和 turn dispatch 由 SessionTurnScheduler 负责。
    """

    def __init__(self, store: SessionIngressStore) -> None:
        self._store = store

    @property
    def store(self) -> SessionIngressStore:
        return self._store

    async def enqueue(self, item: SessionIngressItem) -> SessionIngressItem:
        return await self._store.enqueue(item)

    async def get_next_queued(self, session_key: str) -> SessionIngressItem | None:
        return await self._store.get_next_queued(session_key)
