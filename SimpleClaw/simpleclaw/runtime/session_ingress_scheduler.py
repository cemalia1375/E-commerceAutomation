"""Session turn scheduler for ingress-driven main session execution."""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from loguru import logger

from simpleclaw.runtime.scope_lock import ScopeLockRegistry
from simpleclaw.runtime.session_ingress_protocol import (
    SessionIngressDispatchResult,
    SessionIngressItem,
)
from simpleclaw.runtime.session_ingress_queue import SessionIngressQueue

SessionTurnExecutor = Callable[
    [SessionIngressItem],
    Awaitable[SessionIngressDispatchResult | None],
]
IngressTerminalCallback = Callable[[SessionIngressItem, str], Awaitable[None]]


class SessionTurnScheduler:
    """Drain session ingress items into a real turn runner.

    规则保持极简：

      - 同一个 session_key 永远单消费者
      - 用户消息优先于系统激活消息
      - session busy 时不抢占
      - 过期 / 被更新同类 dedupe_key 覆盖的激活消息不再执行
    """

    def __init__(
        self,
        ingress_queue: SessionIngressQueue,
        execute_turn: SessionTurnExecutor,
        *,
        drain_locks: ScopeLockRegistry | None = None,
        on_terminal: IngressTerminalCallback | None = None,
    ) -> None:
        self._queue = ingress_queue
        self._execute_turn = execute_turn
        self._drain_locks = drain_locks or ScopeLockRegistry()
        self._on_terminal = on_terminal
        self._busy_sessions: set[str] = set()
        self._busy_lock = asyncio.Lock()

    async def is_idle(self, session_key: str) -> bool:
        async with self._busy_lock:
            return session_key not in self._busy_sessions

    async def is_busy(self, session_key: str) -> bool:
        return not await self.is_idle(session_key)

    async def should_drop_on_enqueue(self, item: SessionIngressItem) -> str | None:
        if item.delivery_policy != "best_effort":
            return None
        if item.preempt_policy not in {
            "drop_if_session_busy",
            "drop_if_session_busy_or_user_arrives",
        }:
            return None
        if await self.is_busy(item.session_key):
            return "best-effort ingress dropped because session was busy at submission"
        return None

    async def drain(self, session_key: str) -> int:
        delivered = 0
        logger.info("session_scheduler.drain.enter session={}", session_key)
        async with self._drain_locks.hold(f"session_ingress:{session_key}"):
            logger.info("session_scheduler.drain.locked session={}", session_key)
            while True:
                if await self.is_busy(session_key):
                    logger.info("session_scheduler.drain.busy session={} delivered={}", session_key, delivered)
                    return delivered

                item = await self._queue.get_next_queued(session_key)
                if item is None:
                    logger.info("session_scheduler.drain.empty session={} delivered={}", session_key, delivered)
                    return delivered
                logger.info(
                    "session_scheduler.drain.item session={} ingress={} type={} status={}",
                    session_key,
                    item.ingress_id,
                    item.message_type,
                    item.status,
                )

                if item.is_expired():
                    await self._queue.store.mark_expired(
                        item.ingress_id,
                        summary="session ingress expired before dispatch",
                    )
                    await self._notify_terminal(item, "expired")
                    continue

                if await self._is_superseded(item):
                    await self._queue.store.mark_superseded(
                        item.ingress_id,
                        summary="newer queued ingress with same dedupe_key exists",
                    )
                    await self._notify_terminal(item, "superseded")
                    continue

                if await self._is_preempted(item):
                    await self._queue.store.mark_dropped(
                        item.ingress_id,
                        summary="best-effort ingress dropped because a newer user message arrived",
                    )
                    await self._notify_terminal(item, "dropped")
                    continue

                await self._queue.store.mark_dispatching(
                    item.ingress_id,
                    summary="dispatching session turn",
                )
                logger.info("session_scheduler.drain.dispatching session={} ingress={}", session_key, item.ingress_id)
                await self._set_busy(session_key, True)
                try:
                    result = await self._execute_turn(item)
                except Exception as exc:
                    await self._queue.store.mark_failed(
                        item.ingress_id,
                        str(exc) or exc.__class__.__name__,
                        summary="session turn execution failed",
                    )
                else:
                    outcome = result or SessionIngressDispatchResult.delivered()
                    if outcome.status == "dropped":
                        await self._queue.store.mark_dropped(
                            item.ingress_id,
                            summary=outcome.summary or "session turn dropped by executor",
                        )
                    else:
                        await self._queue.store.mark_delivered(
                            item.ingress_id,
                            summary=outcome.summary or f"{item.turn_kind} delivered",
                        )
                        delivered += 1
                finally:
                    await self._set_busy(session_key, False)

    async def _is_superseded(self, item: SessionIngressItem) -> bool:
        if item.dedupe_key is None:
            return False
        newer = await self._queue.store.find_newer_queued(
            session_key=item.session_key,
            dedupe_key=item.dedupe_key,
            after_created_at_ms=item.created_at_ms,
            exclude_ingress_id=item.ingress_id,
        )
        return newer is not None

    async def _is_preempted(self, item: SessionIngressItem) -> bool:
        if item.delivery_policy != "best_effort":
            return False
        if item.preempt_policy not in {
            "drop_if_user_arrives",
            "drop_if_session_busy_or_user_arrives",
        }:
            return False
        newer_user_message = await self._queue.store.find_newer_user_message(
            session_key=item.session_key,
            after_created_at_ms=item.created_at_ms,
            exclude_ingress_id=item.ingress_id,
        )
        return newer_user_message is not None

    async def _set_busy(self, session_key: str, busy: bool) -> None:
        async with self._busy_lock:
            if busy:
                self._busy_sessions.add(session_key)
            else:
                self._busy_sessions.discard(session_key)

    async def _notify_terminal(self, item: SessionIngressItem, status: str) -> None:
        if self._on_terminal is None:
            return
        await self._on_terminal(item, status)
