"""WorkItem governance protocol objects.

These dataclasses describe task-governance facts. They are not workflow nodes
and do not decide what the model must do next.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal


WorkItemStatus = Literal[
    "open",
    "in_progress",
    "blocked",
    "completed",
    "failed",
    "cancelled",
]
ChecklistItemStatus = Literal[
    "pending",
    "in_progress",
    "done",
    "skipped",
    "blocked",
]
WorkItemRiskLevel = Literal["low", "medium", "high"]


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(slots=True)
class UserIntentRecord:
    """Structured summary of what the user is trying to achieve."""

    goal: str
    tenant_key: str | None = None
    session_key: str | None = None
    source_message_id: str | None = None
    constraints: list[str] = field(default_factory=list)
    expected_output: str | None = None
    risk_notes: str | None = None
    intent_id: str = field(default_factory=lambda: make_id("intent"))
    created_at_ms: int = field(default_factory=now_ms)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ChecklistItem:
    """One local execution step under a WorkItem."""

    text: str
    status: ChecklistItemStatus = "pending"
    note: str | None = None
    item_id: str = field(default_factory=lambda: make_id("check"))
    updated_at_ms: int = field(default_factory=now_ms)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ChecklistRecord:
    """Current local checklist for one WorkItem."""

    work_item_id: str
    items: list[ChecklistItem] = field(default_factory=list)
    checklist_id: str = field(default_factory=lambda: make_id("checklist"))
    updated_at_ms: int = field(default_factory=now_ms)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class WorkItemRecord:
    """User-visible task unit tracked by the runtime harness."""

    title: str
    goal: str
    tenant_key: str | None = None
    session_key: str | None = None
    intent_id: str | None = None
    status: WorkItemStatus = "open"
    priority: int = 100
    acceptance_criteria: list[str] = field(default_factory=list)
    current_summary: str | None = None
    risk_level: WorkItemRiskLevel = "low"
    work_item_id: str = field(default_factory=lambda: make_id("wi"))
    created_at_ms: int = field(default_factory=now_ms)
    updated_at_ms: int = field(default_factory=now_ms)
    completed_at_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ActionEventRecord:
    """Fact that an action happened while working on a WorkItem."""

    work_item_id: str
    event_type: str
    summary: str
    source: str = "runtime"
    tool_name: str | None = None
    runtime_task_id: str | None = None
    payload: dict[str, Any] | None = None
    event_id: str = field(default_factory=lambda: make_id("event"))
    created_at_ms: int = field(default_factory=now_ms)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class WorkEvidenceRecord:
    """Evidence that proves progress or completion of a WorkItem."""

    work_item_id: str
    evidence_type: str
    summary: str
    business_ref_type: str | None = None
    business_ref_id: str | None = None
    runtime_task_id: str | None = None
    payload: dict[str, Any] | None = None
    evidence_id: str = field(default_factory=lambda: make_id("evidence"))
    created_at_ms: int = field(default_factory=now_ms)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
