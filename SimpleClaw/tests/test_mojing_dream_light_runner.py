import json

import pytest

from simpleclaw.dream import DreamAdmissionContext, DreamJob
from simpleclaw.memory.ledger import MemoryLedgerRecord, MemorySnapshot
from Mojing.dream import MojingLightDreamRunner
from Mojing.runtime.dream_monitor import MemoryLedgerDreamMonitor


class _LedgerRepo:
    def __init__(self, ledger: MemoryLedgerRecord | None) -> None:
        self.ledger = ledger
        self.updates = []

    async def get_ledger(self, ledger_id: str):
        if self.ledger and self.ledger.ledger_id == ledger_id:
            return self.ledger
        return None

    async def update_ledger(self, ledger_id: str, **kwargs):
        self.updates.append((ledger_id, kwargs))
        if self.ledger and self.ledger.ledger_id == ledger_id:
            if "dream_status" in kwargs:
                self.ledger.dream_status = kwargs["dream_status"]
        return self.ledger

    async def list_dream_pending(self, *, limit: int = 20, **kwargs):
        if self.ledger and self.ledger.dream_status == "pending":
            return [self.ledger]
        return []


class _SessionRepo:
    """返回 None 表示无法判断静默时长 → 视为可调度（不阻断这些调度/admission 测试）。"""

    def __init__(self, last_user_at=None) -> None:
        self._last_user_at = last_user_at

    async def get_last_user_message_at(self, tenant_key: str, session_key: str):
        return self._last_user_at


class _Scheduler:
    def __init__(self) -> None:
        self.candidates = []
        self.contexts = []

    async def schedule(self, candidate, *, context=None):
        self.candidates.append(candidate)
        self.contexts.append(context)

        class _Result:
            admitted = True
            queue_message_id = "queue-1"

            class _Job:
                job_id = "dreamjob_1"

            job = _Job()

        return _Result()


def _ledger() -> MemoryLedgerRecord:
    return MemoryLedgerRecord(
        ledger_id="memledger_1",
        tenant_key="tenant-1",
        session_key="main:tenant-1",
        source="main",
        status="applied",
        message_seq_start=0,
        message_seq_end=39,
        last_consolidated_from=0,
        last_consolidated_to=40,
        source_chunk=[{"role": "user", "content": "最近熬夜后黑眼圈明显。"}],
        memory_before=MemorySnapshot(items=[]),
        memory_actions=[{"action": "create", "topic": "熬夜黑眼圈"}],
        memory_after=MemorySnapshot(items=[{"topic": "熬夜黑眼圈"}]),
        business_snapshot={"runtime_facts": [{"task_type": "image_analysis", "status": "succeeded"}]},
    )


@pytest.mark.asyncio
async def test_light_dream_runner_writes_draft_artifact_without_llm():
    repo = _LedgerRepo(_ledger())
    job = DreamJob(
        tenant_key="tenant-1",
        session_key="main:tenant-1",
        trigger="memory_threshold",
        reason="review ledger",
        candidate_id="dreamcand_1",
        source_id="memledger_1",
        input_cursor="0:40",
        job_id="dreamjob_1",
        payload={
            "signal": {
                "signal_type": "memory_ledger_applied",
                "subject_type": "memory_ledger",
                "subject_id": "memledger_1",
            },
            "read_assets": ["memory_ledger"],
            "write_assets": ["dream_artifact"],
            "forbidden_assets": [],
        },
    )

    result = await MojingLightDreamRunner(memory_ledger_repo=repo)(job)

    assert result.status == "succeeded"
    assert result.artifacts[0].status == "draft"
    assert result.artifacts[0].artifact_type == "memory_summary"
    assert result.artifacts[0].metadata["source_refs"]["memory_ledger_ids"] == ["memledger_1"]
    content = json.loads(result.artifacts[0].content)
    assert content["memory_actions"][0]["action"] == "noop"
    assert content["ledger_packet"]["dream_signal"]["signal_type"] == "memory_ledger_applied"
    assert content["ledger_packet"]["asset_policy"]["read_assets"] == ["memory_ledger"]
    assert repo.ledger.dream_status == "reviewed"


@pytest.mark.asyncio
async def test_dream_monitor_schedules_pending_applied_ledger():
    repo = _LedgerRepo(_ledger())
    scheduler = _Scheduler()
    monitor = MemoryLedgerDreamMonitor(
        memory_ledger_repo=repo,
        scheduler=scheduler,
        session_repo=_SessionRepo(),
        interval_s=60,
    )

    scheduled = await monitor.check_once()

    assert scheduled == 1
    assert scheduler.candidates[0].source_id == "memledger_1"
    assert scheduler.candidates[0].input_cursor == "0:40"
    assert scheduler.candidates[0].payload["signal"]["signal_type"] == "memory_ledger_applied"
    assert scheduler.candidates[0].payload["read_assets"][0] == "memory_ledger"
    assert repo.ledger.dream_status == "candidate"


@pytest.mark.asyncio
async def test_dream_monitor_passes_admission_context():
    repo = _LedgerRepo(_ledger())
    scheduler = _Scheduler()

    async def _context_factory(candidate):
        assert candidate.source_id == "memledger_1"
        return DreamAdmissionContext(session_busy=True)

    monitor = MemoryLedgerDreamMonitor(
        memory_ledger_repo=repo,
        scheduler=scheduler,
        session_repo=_SessionRepo(),
        interval_s=60,
        admission_context_factory=_context_factory,
    )

    await monitor.check_once()

    assert scheduler.contexts[0].session_busy is True
