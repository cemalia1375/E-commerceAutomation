"""Schedule dream jobs for pending memory ledgers."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import TYPE_CHECKING

from loguru import logger

from simpleclaw.dream import DreamAdmissionContext, DreamCandidate, DreamScheduler
from Mojing.dream.signals import memory_ledger_applied_signal

if TYPE_CHECKING:
    from Mojing.storage.memory_ledger_repo import MemoryLedgerRepository
    from Mojing.storage.session_repo import SessionRepository

DreamAdmissionContextFactory = Callable[
    [DreamCandidate],
    DreamAdmissionContext | Awaitable[DreamAdmissionContext],
]


class MemoryLedgerDreamMonitor:
    """Poll pending memory ledgers and schedule low-priority dream jobs."""

    def __init__(
        self,
        *,
        memory_ledger_repo: "MemoryLedgerRepository",
        scheduler: DreamScheduler,
        session_repo: "SessionRepository",
        interval_s: float = 60.0,
        batch_size: int = 10,
        idle_threshold_s: float = 3600.0,
        admission_context_factory: DreamAdmissionContextFactory | None = None,
    ) -> None:
        self._memory_ledger_repo = memory_ledger_repo
        self._scheduler = scheduler
        self._session_repo = session_repo
        self._interval_s = max(5.0, float(interval_s))
        self._batch_size = max(1, min(int(batch_size or 10), 50))
        self._idle_threshold_s = max(0.0, float(idle_threshold_s))
        self._admission_context_factory = admission_context_factory
        self._running = False

    async def run(self) -> None:
        self._running = True
        logger.info("MemoryLedgerDreamMonitor started interval_s={} batch_size={}", self._interval_s, self._batch_size)
        while self._running:
            try:
                await self.check_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("MemoryLedgerDreamMonitor check failed: {}", exc)
            await asyncio.sleep(self._interval_s)

    def stop(self) -> None:
        self._running = False

    async def check_once(self) -> int:
        ledgers = await self._memory_ledger_repo.list_dream_pending(limit=self._batch_size)
        scheduled = 0
        for ledger in ledgers:
            if ledger.status != "applied":
                await self._memory_ledger_repo.update_ledger(
                    ledger.ledger_id,
                    dream_status="not_needed",
                    metadata={"dream_skip_reason": f"ledger status is {ledger.status}"},
                )
                continue
            # 仅在该 session 静默满阈值后才触发 dream（保留 pending，下轮再判）。
            if not await self._session_idle_enough(ledger):
                continue
            signal = memory_ledger_applied_signal(ledger)
            candidate = DreamCandidate.from_signal(
                signal,
                trigger="idle_session",
            )
            context = await self._build_admission_context(candidate)
            result = await self._scheduler.schedule(candidate, context=context)
            if result.admitted:
                scheduled += 1
                await self._memory_ledger_repo.update_ledger(
                    ledger.ledger_id,
                    dream_status="candidate",
                    metadata={
                        "dream_candidate_id": candidate.candidate_id,
                        "dream_job_id": result.job.job_id if result.job else None,
                        "dream_queue_message_id": result.queue_message_id,
                    },
                )
            else:
                logger.debug(
                    "dream candidate denied ledger={} reason={}",
                    ledger.ledger_id,
                    result.decision.reason,
                )
        return scheduled

    async def _session_idle_enough(self, ledger) -> bool:
        """判断 ledger 所属 session 是否已静默满阈值（以最后一条用户消息时间为准）。"""
        last_user_at = await self._session_repo.get_last_user_message_at(
            ledger.tenant_key,
            ledger.session_key,
        )
        # 无法确定静默时长（session_key 为空或无用户消息）→ 视为可调度，避免 ledger 永久卡住。
        if last_user_at is None:
            logger.debug(
                "dream idle gate: no user message for ledger={} session={}, treating as eligible",
                ledger.ledger_id,
                ledger.session_key,
            )
            return True
        idle_s = (datetime.utcnow() - last_user_at).total_seconds()
        if idle_s < self._idle_threshold_s:
            logger.debug(
                "dream idle gate: ledger={} session idle {:.0f}s < {:.0f}s, defer",
                ledger.ledger_id,
                idle_s,
                self._idle_threshold_s,
            )
            return False
        return True

    async def _build_admission_context(self, candidate: DreamCandidate) -> DreamAdmissionContext | None:
        if self._admission_context_factory is None:
            return None
        context = self._admission_context_factory(candidate)
        if inspect.isawaitable(context):
            return await context
        return context
