"""Tests for memory_extract ledger wiring."""

from __future__ import annotations

import pytest

from simpleclaw.core.messages import UserMessage
from simpleclaw.memory.base import MemoryItem
from simpleclaw.memory.ledger import MemoryLedgerRecord
from simpleclaw.memory.ledger_store import InMemoryMemoryLedgerStore
from simpleclaw.runtime.task_protocol import TaskEnvelope

from Mojing.agent import memory_extract as memory_extract_module
from Mojing.agent.memory_extract import make_memory_extract_executor, make_memory_extract_submitter
from Mojing.runtime.streams import MojingTaskStream


class _Runtime:
    def __init__(self) -> None:
        self.tasks: list[TaskEnvelope] = []

    async def submit_task(self, task: TaskEnvelope, **_) -> str:
        self.tasks.append(task)
        return "queue-1"


class _LLM:
    pass


class _Memory:
    items: list[MemoryItem] = [
        MemoryItem(key="old_topic", description="旧摘要", content="旧内容"),
    ]

    def __init__(self, *_, **__) -> None:
        pass

    async def retrieve(self, query: str = "", top_k: int = 20) -> list[MemoryItem]:
        return list(self.items[:top_k])

    async def store(self, key: str, content: str, *, description: str = "", metadata=None) -> None:
        self.items = [
            item for item in self.items
            if item.key != key
        ]
        self.items.insert(0, MemoryItem(key=key, description=description, content=content))

    async def delete(self, key: str) -> None:
        self.items = [item for item in self.items if item.key != key]


@pytest.mark.asyncio
async def test_submitter_binds_runtime_task_to_ledger() -> None:
    runtime = _Runtime()
    store = InMemoryMemoryLedgerStore()
    ledger = await store.create_ledger(MemoryLedgerRecord(
        tenant_key="tenant-1",
        session_key="main:tenant-1",
        source="main",
    ))
    submit = make_memory_extract_submitter(
        runtime=runtime,  # type: ignore[arg-type]
        source="main",
        memory_ledger_store=store,
    )

    await submit(
        "tenant-1",
        [UserMessage(content="我喜欢清爽一点")],
        session_key="main:tenant-1",
        ledger_id=ledger.ledger_id,
        last_consolidated_from=0,
        last_consolidated_to=1,
        tokens_before=3000,
        tokens_after=1000,
    )

    assert len(runtime.tasks) == 1
    task = runtime.tasks[0]
    updated = await store.get_ledger(ledger.ledger_id)
    assert updated is not None
    assert updated.runtime_task_id == task.task_id
    assert updated.trace_id == task.trace_id
    assert task.payload["session_key"] == "main:tenant-1"
    assert task.payload["last_consolidated_from"] == 0
    assert task.payload["last_consolidated_to"] == 1
    assert task.payload["source_chunk_hash"]


@pytest.mark.asyncio
async def test_executor_writes_memory_outcome_to_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    _Memory.items = [MemoryItem(key="old_topic", description="旧摘要", content="旧内容")]
    monkeypatch.setattr(memory_extract_module, "MySQLMemory", _Memory)

    async def fake_complete(*_, **__) -> str:
        return (
            '{"memory_actions":[{"action":"create","topic":"skin_pref",'
            '"description":"肤感偏好","content":"用户偏好清爽肤感"}]}'
        )

    monkeypatch.setattr(memory_extract_module, "_llm_complete", fake_complete)

    store = InMemoryMemoryLedgerStore()
    ledger = await store.create_ledger(MemoryLedgerRecord(
        tenant_key="tenant-1",
        session_key="main:tenant-1",
        source="main",
    ))
    task = TaskEnvelope(
        task_type="memory_extract",
        payload={
            "tenant_key": "tenant-1",
            "session_key": "main:tenant-1",
            "source": "main",
            "ledger_id": ledger.ledger_id,
            "dropped_messages": [{"role": "user", "content": "我喜欢清爽一点"}],
        },
        stream=MojingTaskStream.MEMORY_EXTRACT,
        tenant_key="tenant-1",
        session_key="main:tenant-1",
    )

    execute = make_memory_extract_executor(
        llm=_LLM(),  # type: ignore[arg-type]
        db=object(),  # type: ignore[arg-type]
        memory_ledger_store=store,
    )
    result = await execute(task)

    updated = await store.get_ledger(ledger.ledger_id)
    assert result.status == "succeeded"
    assert updated is not None
    assert updated.status == "applied"
    assert updated.runtime_task_id == task.task_id
    assert updated.memory_before is not None
    assert updated.memory_after is not None
    assert updated.memory_actions is not None
    assert updated.memory_actions[0]["status"] == "applied"
    assert updated.business_snapshot is not None
    assert updated.business_snapshot["message_count"] == 1
