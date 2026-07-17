"""WorkItem store protocol and in-memory implementation."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any, Protocol

from simpleclaw.workitem.protocol import (
    ActionEventRecord,
    ChecklistItem,
    ChecklistItemStatus,
    ChecklistRecord,
    UserIntentRecord,
    WorkEvidenceRecord,
    WorkItemRecord,
    WorkItemRiskLevel,
    WorkItemStatus,
    make_id,
    now_ms,
)


class WorkItemStore(Protocol):
    """Storage protocol for task-governance facts."""

    async def create_user_intent(self, intent: UserIntentRecord) -> UserIntentRecord: ...

    async def create_work_item(self, work_item: WorkItemRecord) -> WorkItemRecord: ...

    async def get_work_item(self, work_item_id: str) -> WorkItemRecord | None: ...

    async def update_work_item(
        self,
        work_item_id: str,
        *,
        status: WorkItemStatus | None = None,
        current_summary: str | None = None,
        acceptance_criteria: list[str] | None = None,
        risk_level: WorkItemRiskLevel | None = None,
        priority: int | None = None,
    ) -> WorkItemRecord | None: ...

    async def update_checklist(
        self,
        work_item_id: str,
        items: list[ChecklistItem],
    ) -> ChecklistRecord | None: ...

    async def get_checklist(self, work_item_id: str) -> ChecklistRecord | None: ...

    async def record_action_event(
        self,
        event: ActionEventRecord,
    ) -> ActionEventRecord: ...

    async def list_action_events(
        self,
        work_item_id: str,
        *,
        limit: int = 10,
    ) -> list[ActionEventRecord]: ...

    async def attach_evidence(
        self,
        evidence: WorkEvidenceRecord,
    ) -> WorkEvidenceRecord: ...

    async def list_evidence(
        self,
        work_item_id: str,
        *,
        limit: int = 10,
    ) -> list[WorkEvidenceRecord]: ...

    async def list_active_work_items(
        self,
        *,
        tenant_key: str | None = None,
        session_key: str | None = None,
        limit: int = 5,
    ) -> list[WorkItemRecord]: ...


class InMemoryWorkItemStore:
    """Process-local WorkItem store for development and tests."""

    def __init__(self) -> None:
        self._intents: dict[str, UserIntentRecord] = {}
        self._work_items: dict[str, WorkItemRecord] = {}
        self._checklists: dict[str, ChecklistRecord] = {}
        self._events: dict[str, list[ActionEventRecord]] = {}
        self._evidence: dict[str, list[WorkEvidenceRecord]] = {}
        self._lock = asyncio.Lock()

    async def create_user_intent(self, intent: UserIntentRecord) -> UserIntentRecord:
        async with self._lock:
            self._intents[intent.intent_id] = intent
            return intent

    async def create_work_item(self, work_item: WorkItemRecord) -> WorkItemRecord:
        async with self._lock:
            self._work_items[work_item.work_item_id] = work_item
            self._checklists.setdefault(
                work_item.work_item_id,
                ChecklistRecord(work_item_id=work_item.work_item_id),
            )
            return work_item

    async def get_work_item(self, work_item_id: str) -> WorkItemRecord | None:
        async with self._lock:
            return self._work_items.get(_clean_id(work_item_id))

    async def update_work_item(
        self,
        work_item_id: str,
        *,
        status: WorkItemStatus | None = None,
        current_summary: str | None = None,
        acceptance_criteria: list[str] | None = None,
        risk_level: WorkItemRiskLevel | None = None,
        priority: int | None = None,
    ) -> WorkItemRecord | None:
        async with self._lock:
            current = self._work_items.get(_clean_id(work_item_id))
            if current is None:
                return None
            completed_at = current.completed_at_ms
            if status in {"completed", "failed", "cancelled"} and completed_at is None:
                completed_at = now_ms()
            if status not in {"completed", "failed", "cancelled"}:
                completed_at = None
            updated = replace(
                current,
                status=status or current.status,
                current_summary=(
                    current_summary
                    if current_summary is not None
                    else current.current_summary
                ),
                acceptance_criteria=(
                    list(acceptance_criteria)
                    if acceptance_criteria is not None
                    else current.acceptance_criteria
                ),
                risk_level=risk_level or current.risk_level,
                priority=priority if priority is not None else current.priority,
                updated_at_ms=now_ms(),
                completed_at_ms=completed_at,
            )
            self._work_items[updated.work_item_id] = updated
            return updated

    async def update_checklist(
        self,
        work_item_id: str,
        items: list[ChecklistItem],
    ) -> ChecklistRecord | None:
        async with self._lock:
            work_item_id = _clean_id(work_item_id)
            if work_item_id not in self._work_items:
                return None
            current = self._checklists.get(work_item_id)
            checklist = ChecklistRecord(
                work_item_id=work_item_id,
                items=items,
                checklist_id=(
                    current.checklist_id
                    if current is not None
                    else ChecklistRecord(work_item_id=work_item_id).checklist_id
                ),
                updated_at_ms=now_ms(),
            )
            self._checklists[work_item_id] = checklist
            self._work_items[work_item_id] = replace(
                self._work_items[work_item_id],
                status=_next_status_for_checklist(self._work_items[work_item_id].status),
                updated_at_ms=now_ms(),
            )
            return checklist

    async def get_checklist(self, work_item_id: str) -> ChecklistRecord | None:
        async with self._lock:
            return self._checklists.get(_clean_id(work_item_id))

    async def record_action_event(self, event: ActionEventRecord) -> ActionEventRecord:
        async with self._lock:
            self._events.setdefault(event.work_item_id, []).append(event)
            return event

    async def list_action_events(
        self,
        work_item_id: str,
        *,
        limit: int = 10,
    ) -> list[ActionEventRecord]:
        async with self._lock:
            events = list(self._events.get(_clean_id(work_item_id), []))
        events.sort(key=lambda event: event.created_at_ms, reverse=True)
        return events[: max(1, int(limit or 10))]

    async def attach_evidence(self, evidence: WorkEvidenceRecord) -> WorkEvidenceRecord:
        async with self._lock:
            self._evidence.setdefault(evidence.work_item_id, []).append(evidence)
            return evidence

    async def list_evidence(
        self,
        work_item_id: str,
        *,
        limit: int = 10,
    ) -> list[WorkEvidenceRecord]:
        async with self._lock:
            evidence = list(self._evidence.get(_clean_id(work_item_id), []))
        evidence.sort(key=lambda item: item.created_at_ms, reverse=True)
        return evidence[: max(1, int(limit or 10))]

    async def list_active_work_items(
        self,
        *,
        tenant_key: str | None = None,
        session_key: str | None = None,
        limit: int = 5,
    ) -> list[WorkItemRecord]:
        async with self._lock:
            items = list(self._work_items.values())
        items = [
            item for item in items
            if item.status not in {"completed", "failed", "cancelled"}
        ]
        if tenant_key is not None:
            items = [item for item in items if item.tenant_key == tenant_key]
        if session_key is not None:
            items = [item for item in items if item.session_key == session_key]
        items.sort(key=lambda item: (item.priority, -item.updated_at_ms))
        return items[: max(1, int(limit or 5))]


def _clean_id(value: str) -> str:
    return str(value or "").strip()


def _next_status_for_checklist(status: WorkItemStatus) -> WorkItemStatus:
    if status == "open":
        return "in_progress"
    return status


def checklist_items_from_payload(raw_items: Any) -> list[ChecklistItem]:
    """Normalize tool JSON payload into ChecklistItem objects."""
    if not isinstance(raw_items, list):
        return []
    items: list[ChecklistItem] = []
    for raw in raw_items:
        if isinstance(raw, str):
            text = raw.strip()
            if text:
                items.append(ChecklistItem(text=text))
            continue
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        items.append(
            ChecklistItem(
                text=text,
                status=_checklist_status(raw.get("status")),
                note=_optional_str(raw.get("note")),
                item_id=_optional_str(raw.get("item_id")) or make_id("check"),
            )
        )
    return items


def _checklist_status(value: Any) -> ChecklistItemStatus:
    text = str(value or "").strip().lower()
    if text in {"pending", "in_progress", "done", "skipped", "blocked"}:
        return text  # type: ignore[return-value]
    return "pending"


def _optional_str(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
