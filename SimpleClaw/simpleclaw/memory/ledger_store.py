"""Memory ledger store contracts and in-memory implementation."""

from __future__ import annotations

from typing import Any, Protocol

from simpleclaw.memory.ledger import (
    MemoryLedgerDreamStatus,
    MemoryLedgerRecord,
    MemoryLedgerStatus,
    MemorySnapshot,
    now_ms,
)


class MemoryLedgerStore(Protocol):
    """Persistence contract for memory governance records."""

    async def create_ledger(self, record: MemoryLedgerRecord) -> MemoryLedgerRecord:
        ...

    async def get_ledger(self, ledger_id: str) -> MemoryLedgerRecord | None:
        ...

    async def update_ledger(
        self,
        ledger_id: str,
        *,
        status: MemoryLedgerStatus | None = None,
        runtime_task_id: str | None = None,
        trace_id: str | None = None,
        memory_before: MemorySnapshot | None = None,
        memory_actions: list[dict[str, Any]] | None = None,
        memory_after: MemorySnapshot | None = None,
        business_snapshot: dict[str, Any] | None = None,
        dream_status: MemoryLedgerDreamStatus | None = None,
        last_error: str | None = None,
        metadata: dict[str, Any] | None = None,
        completed: bool = False,
    ) -> MemoryLedgerRecord | None:
        ...

    async def list_dream_pending(
        self,
        *,
        tenant_key: str | None = None,
        source: str | None = None,
        limit: int = 20,
    ) -> list[MemoryLedgerRecord]:
        ...


class InMemoryMemoryLedgerStore:
    """Small in-memory ledger store for tests and local wiring."""

    def __init__(self) -> None:
        self.records: dict[str, MemoryLedgerRecord] = {}

    async def create_ledger(self, record: MemoryLedgerRecord) -> MemoryLedgerRecord:
        record.updated_at_ms = now_ms()
        self.records[record.ledger_id] = record
        return record

    async def get_ledger(self, ledger_id: str) -> MemoryLedgerRecord | None:
        return self.records.get(ledger_id)

    async def update_ledger(
        self,
        ledger_id: str,
        *,
        status: MemoryLedgerStatus | None = None,
        runtime_task_id: str | None = None,
        trace_id: str | None = None,
        memory_before: MemorySnapshot | None = None,
        memory_actions: list[dict[str, Any]] | None = None,
        memory_after: MemorySnapshot | None = None,
        business_snapshot: dict[str, Any] | None = None,
        dream_status: MemoryLedgerDreamStatus | None = None,
        last_error: str | None = None,
        metadata: dict[str, Any] | None = None,
        completed: bool = False,
    ) -> MemoryLedgerRecord | None:
        record = self.records.get(ledger_id)
        if record is None:
            return None
        if status is not None:
            record.status = status
        if runtime_task_id is not None:
            record.runtime_task_id = runtime_task_id
        if trace_id is not None:
            record.trace_id = trace_id
        if memory_before is not None:
            record.memory_before = memory_before
        if memory_actions is not None:
            record.memory_actions = memory_actions
        if memory_after is not None:
            record.memory_after = memory_after
        if business_snapshot is not None:
            record.business_snapshot = business_snapshot
        if dream_status is not None:
            record.dream_status = dream_status
        if last_error is not None:
            record.last_error = last_error
        if metadata is not None:
            record.metadata.update(metadata)
        if completed:
            record.completed_at_ms = now_ms()
        record.updated_at_ms = now_ms()
        return record

    async def list_dream_pending(
        self,
        *,
        tenant_key: str | None = None,
        source: str | None = None,
        limit: int = 20,
    ) -> list[MemoryLedgerRecord]:
        rows = [
            record
            for record in self.records.values()
            if record.dream_status == "pending"
            and (tenant_key is None or record.tenant_key == tenant_key)
            and (source is None or record.source == source)
        ]
        rows.sort(key=lambda r: r.created_at_ms)
        return rows[:max(0, limit)]

