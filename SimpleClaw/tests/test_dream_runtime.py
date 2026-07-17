from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace

from simpleclaw.dream import (
    DreamAdmissionContext,
    DreamArtifact,
    DreamCandidate,
    DreamExecutor,
    DreamResult,
    DreamScheduler,
    InMemoryDreamStore,
)
from Mojing.runtime.dream_monitor import MemoryLedgerDreamMonitor


class _Runtime:
    def __init__(self) -> None:
        self.tasks = []
        self.summaries = []

    async def submit_task(self, task, *, summary=None):
        self.tasks.append(task)
        self.summaries.append(summary)
        return "queue-1"


class DreamRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def test_denies_when_session_busy(self) -> None:
        store = InMemoryDreamStore()
        scheduler = DreamScheduler(store=store)
        candidate = DreamCandidate(
            tenant_key="tenant-1",
            session_key="main:tenant-1",
            trigger="memory_threshold",
            reason="memory has enough deltas",
        )

        result = await scheduler.schedule(
            candidate,
            context=DreamAdmissionContext(session_busy=True),
        )

        self.assertFalse(result.decision.allowed)
        self.assertEqual(result.decision.reason, "session_busy")
        self.assertEqual(store.candidates[candidate.candidate_id].status, "skipped")
        self.assertEqual(store.jobs, {})

    async def test_admits_and_enqueues_when_idle(self) -> None:
        store = InMemoryDreamStore()
        runtime = _Runtime()
        scheduler = DreamScheduler(store=store, runtime=runtime)
        candidate = DreamCandidate(
            tenant_key="tenant-1",
            session_key="main:tenant-1",
            trigger="idle_session",
            reason="session is idle",
            input_cursor="msg:42",
        )

        result = await scheduler.schedule(candidate)

        self.assertTrue(result.admitted)
        self.assertEqual(result.queue_message_id, "queue-1")
        self.assertEqual(len(runtime.tasks), 1)
        self.assertEqual(runtime.tasks[0].task_type, "dream")
        self.assertEqual(runtime.tasks[0].scope_key, "dream:tenant-1:main:tenant-1:default")
        self.assertEqual(runtime.tasks[0].payload["input_cursor"], "msg:42")
        self.assertEqual(store.candidates[candidate.candidate_id].status, "superseded")
        self.assertEqual(store.jobs[result.job.job_id].status, "queued")  # type: ignore[union-attr]

    async def test_executor_persists_artifacts_and_marks_succeeded(self) -> None:
        store = InMemoryDreamStore()
        candidate = DreamCandidate(
            tenant_key="tenant-1",
            session_key="main:tenant-1",
            trigger="manual",
            reason="manual dream",
        )
        scheduled = await DreamScheduler(store=store).schedule(
            candidate,
            context=DreamAdmissionContext(force=True),
        )
        job = scheduled.job
        assert job is not None

        async def runner(job):
            artifact = DreamArtifact(
                job_id=job.job_id,
                artifact_type="memory_summary",
                content="User prefers lightweight routines.",
                status="validated",
            )
            return DreamResult.succeeded(
                job.job_id,
                summary="dream wrote memory summary",
                artifacts=[artifact],
            )

        result = await DreamExecutor(store=store, runner=runner).execute(job.to_task_envelope())

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(store.jobs[job.job_id].status, "succeeded")
        self.assertEqual(len(store.artifacts), 1)
        artifact = next(iter(store.artifacts.values()))
        self.assertEqual(artifact.artifact_type, "memory_summary")
        self.assertEqual(artifact.status, "validated")


class _FakeLedgerRepo:
    """最小化 MemoryLedgerRepository：返回固定的 pending 列表并记录 update。"""

    def __init__(self, ledgers) -> None:
        self._ledgers = ledgers
        self.updates: list[tuple[str, dict]] = []

    async def list_dream_pending(self, limit: int = 10):
        return list(self._ledgers)[:limit]

    async def update_ledger(self, ledger_id: str, **kwargs):
        self.updates.append((ledger_id, kwargs))
        return None


class _FakeSessionRepo:
    def __init__(self, last_user_at) -> None:
        self._last_user_at = last_user_at
        self.calls: list[tuple[str, str]] = []

    async def get_last_user_message_at(self, tenant_key: str, session_key: str):
        self.calls.append((tenant_key, session_key))
        return self._last_user_at


def _ledger(**overrides):
    base = dict(
        ledger_id="led-1",
        tenant_key="tenant-1",
        session_key="main:tenant-1",
        source="main",
        status="applied",
        input_cursor="msg:42",
        message_seq_start=1,
        message_seq_end=9,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class DreamIdleGateTest(unittest.IsolatedAsyncioTestCase):
    """MemoryLedgerDreamMonitor 的「session 静默后才触发」闸门。"""

    def _monitor(self, ledger_repo, session_repo, *, runtime=None):
        store = InMemoryDreamStore()
        scheduler = DreamScheduler(store=store, runtime=runtime)
        monitor = MemoryLedgerDreamMonitor(
            memory_ledger_repo=ledger_repo,
            scheduler=scheduler,
            session_repo=session_repo,
            idle_threshold_s=3600.0,
        )
        return monitor, store

    async def test_recent_user_message_skips_scheduling(self) -> None:
        ledger_repo = _FakeLedgerRepo([_ledger()])
        session_repo = _FakeSessionRepo(datetime.utcnow() - timedelta(minutes=5))
        monitor, store = self._monitor(ledger_repo, session_repo)

        scheduled = await monitor.check_once()

        self.assertEqual(scheduled, 0)
        self.assertEqual(store.jobs, {})
        self.assertEqual(ledger_repo.updates, [])  # 仍保留 pending，不改状态

    async def test_idle_long_enough_schedules_with_idle_trigger(self) -> None:
        ledger_repo = _FakeLedgerRepo([_ledger()])
        session_repo = _FakeSessionRepo(datetime.utcnow() - timedelta(hours=2))
        runtime = _Runtime()
        monitor, store = self._monitor(ledger_repo, session_repo, runtime=runtime)

        scheduled = await monitor.check_once()

        self.assertEqual(scheduled, 1)
        self.assertEqual(len(store.candidates), 1)
        candidate = next(iter(store.candidates.values()))
        self.assertEqual(candidate.trigger, "idle_session")
        self.assertEqual(len(ledger_repo.updates), 1)
        _, kwargs = ledger_repo.updates[0]
        self.assertEqual(kwargs.get("dream_status"), "candidate")

    async def test_no_user_message_falls_back_to_eligible(self) -> None:
        ledger_repo = _FakeLedgerRepo([_ledger()])
        session_repo = _FakeSessionRepo(None)
        runtime = _Runtime()
        monitor, _ = self._monitor(ledger_repo, session_repo, runtime=runtime)

        scheduled = await monitor.check_once()

        self.assertEqual(scheduled, 1)


if __name__ == "__main__":
    unittest.main()
