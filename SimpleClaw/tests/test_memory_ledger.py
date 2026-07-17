import pytest

from simpleclaw.memory import (
    InMemoryMemoryLedgerStore,
    MemoryLedgerRecord,
    MemorySnapshot,
)


@pytest.mark.asyncio
async def test_memory_ledger_input_cursor_prefers_consolidated_range():
    record = MemoryLedgerRecord(
        tenant_key="t1",
        session_key="main:t1",
        last_consolidated_from=10,
        last_consolidated_to=18,
        message_seq_start=10,
        message_seq_end=17,
    )

    assert record.input_cursor == "10:18"


@pytest.mark.asyncio
async def test_memory_ledger_store_updates_extraction_state():
    store = InMemoryMemoryLedgerStore()
    record = await store.create_ledger(
        MemoryLedgerRecord(
            tenant_key="t1",
            session_key="main:t1",
            source="main",
            dropped_count=3,
        )
    )

    updated = await store.update_ledger(
        record.ledger_id,
        status="applied",
        runtime_task_id="task_1",
        memory_before=MemorySnapshot(items=[{"topic": "old"}]),
        memory_actions=[{"action": "create", "topic": "new"}],
        memory_after=MemorySnapshot(items=[{"topic": "new"}]),
        completed=True,
    )

    assert updated is not None
    assert updated.status == "applied"
    assert updated.runtime_task_id == "task_1"
    assert updated.memory_before is not None
    assert updated.memory_before.items[0]["topic"] == "old"
    assert updated.memory_actions == [{"action": "create", "topic": "new"}]
    assert updated.memory_after is not None
    assert updated.completed_at_ms is not None


@pytest.mark.asyncio
async def test_memory_ledger_store_lists_dream_pending_by_scope():
    store = InMemoryMemoryLedgerStore()
    pending = await store.create_ledger(
        MemoryLedgerRecord(tenant_key="t1", session_key="main:t1", source="main")
    )
    reviewed = await store.create_ledger(
        MemoryLedgerRecord(tenant_key="t1", session_key="main:t1", source="main")
    )
    other_source = await store.create_ledger(
        MemoryLedgerRecord(tenant_key="t1", session_key="main:t1", source="skin_diary")
    )
    await store.update_ledger(reviewed.ledger_id, dream_status="reviewed")

    rows = await store.list_dream_pending(tenant_key="t1", source="main")

    assert [row.ledger_id for row in rows] == [pending.ledger_id]
    assert other_source.ledger_id not in [row.ledger_id for row in rows]
