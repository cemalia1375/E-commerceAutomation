"""Memory ledger protocol.

The ledger records the causal chain behind memory changes. It is intentionally
separate from Memory, which stores the final durable memory entries.
Applications provide persistence; the framework defines the governance shape.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal


MemoryLedgerStatus = Literal[
    "queued",
    "extracting",
    "applied",
    "failed",
    "skipped",
]
MemoryLedgerDreamStatus = Literal[
    "pending",
    "not_needed",
    "candidate",
    "reviewed",
    "applied",
    "failed",
]
MemoryLedgerTrigger = Literal[
    "context_compression",
    "in_turn_compression",
    "manual",
    "dream",
    "external",
]


def now_ms() -> int:
    return int(time.time() * 1000)


def make_memory_ledger_id() -> str:
    return f"memledger_{uuid.uuid4().hex}"


@dataclass(slots=True)
class MemorySnapshot:
    """A compact view of memory state at a point in time."""

    items: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MemoryLedgerRecord:
    """A causal record for one memory extraction or consolidation pass."""

    tenant_key: str
    session_key: str
    source: str = "main"
    trigger_type: MemoryLedgerTrigger = "context_compression"
    status: MemoryLedgerStatus = "queued"
    ledger_id: str = field(default_factory=make_memory_ledger_id)
    runtime_task_id: str | None = None
    trace_id: str | None = None

    message_seq_start: int | None = None
    message_seq_end: int | None = None
    last_consolidated_from: int | None = None
    last_consolidated_to: int | None = None
    dropped_count: int = 0
    tokens_before: int | None = None
    tokens_after: int | None = None
    source_chunk_hash: str | None = None

    source_chunk: list[dict[str, Any]] | None = None
    memory_before: MemorySnapshot | None = None
    memory_actions: list[dict[str, Any]] | None = None
    memory_after: MemorySnapshot | None = None
    business_snapshot: dict[str, Any] | None = None

    dream_status: MemoryLedgerDreamStatus = "pending"
    last_error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at_ms: int = field(default_factory=now_ms)
    updated_at_ms: int = field(default_factory=now_ms)
    completed_at_ms: int | None = None

    @property
    def input_cursor(self) -> str:
        if self.last_consolidated_from is not None and self.last_consolidated_to is not None:
            return f"{self.last_consolidated_from}:{self.last_consolidated_to}"
        if self.message_seq_start is not None and self.message_seq_end is not None:
            return f"{self.message_seq_start}:{self.message_seq_end}"
        return self.ledger_id

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

