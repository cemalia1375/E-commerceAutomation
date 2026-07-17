"""Built-in WorkItem governance tools."""

from __future__ import annotations

import json
from typing import Any

from simpleclaw.tools.base import Tool, ToolResult
from simpleclaw.workitem.protocol import (
    ActionEventRecord,
    WorkEvidenceRecord,
    WorkItemRecord,
)
from simpleclaw.workitem.store import WorkItemStore, checklist_items_from_payload


class _WorkItemTool(Tool):
    tool_category = "runtime_governance"
    read_only = False
    destructive = False
    concurrency_safe = False
    requires_approval = False
    risk_level = "medium"

    def __init__(self, store: WorkItemStore) -> None:
        self._store = store

    def _current_tenant_key(self) -> str | None:
        return _optional_str(getattr(self, "_tenant_key", None))

    def _current_session_key(self) -> str | None:
        return _optional_str(getattr(self, "_session_key", None))


class CreateWorkItemTool(_WorkItemTool):
    name = "create_work_item"
    description = (
        "Create a trackable WorkItem for a multi-step, user-visible, "
        "cross-turn, side-effecting, or evidence-requiring task. Do not use "
        "for ordinary chat, simple explanations, tool discovery, or every "
        "small checklist step."
    )
    parameters = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Short user-visible title for the work item.",
            },
            "goal": {
                "type": "string",
                "description": "What this work item should accomplish.",
            },
            "acceptance_criteria": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Concrete criteria that prove completion.",
            },
            "current_summary": {
                "type": "string",
                "description": "Initial progress summary.",
            },
            "risk_level": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "default": "low",
            },
            "priority": {
                "type": "integer",
                "description": "Lower number means higher priority.",
                "default": 100,
            },
        },
        "required": ["title", "goal"],
        "additionalProperties": False,
    }

    def cast_params(self, params: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(params)
        normalized["title"] = str(normalized.get("title") or "").strip()
        normalized["goal"] = str(normalized.get("goal") or "").strip()
        normalized["acceptance_criteria"] = _string_list(
            normalized.get("acceptance_criteria")
        )
        normalized["current_summary"] = _optional_str(normalized.get("current_summary"))
        normalized["risk_level"] = _risk_level(normalized.get("risk_level"))
        try:
            normalized["priority"] = int(normalized.get("priority") or 100)
        except Exception:
            normalized["priority"] = 100
        return normalized

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not params.get("title"):
            errors.append("title is required")
        if not params.get("goal"):
            errors.append("goal is required")
        return errors

    async def execute(
        self,
        *,
        title: str,
        goal: str,
        acceptance_criteria: list[str] | None = None,
        current_summary: str | None = None,
        risk_level: str = "low",
        priority: int = 100,
    ) -> ToolResult:
        work_item = WorkItemRecord(
            title=title,
            goal=goal,
            tenant_key=self._current_tenant_key(),
            session_key=self._current_session_key(),
            acceptance_criteria=list(acceptance_criteria or []),
            current_summary=current_summary,
            risk_level=_risk_level(risk_level),
            priority=priority,
        )
        created = await self._store.create_work_item(work_item)
        event = ActionEventRecord(
            work_item_id=created.work_item_id,
            event_type="work_item_created",
            source="tool",
            tool_name=self.name,
            summary=f"Created WorkItem: {created.title}",
            payload={"goal": created.goal},
        )
        await self._store.record_action_event(event)
        return _json_result(
            {
                "ok": True,
                "action": "work_item_created",
                "work_item": created.to_dict(),
            }
        )


class UpdateChecklistTool(_WorkItemTool):
    name = "update_checklist"
    description = (
        "Replace the current local checklist for an existing WorkItem. Use this "
        "for the executor's local steps, not for creating new WorkItems."
    )
    parameters = {
        "type": "object",
        "properties": {
            "work_item_id": {"type": "string"},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "item_id": {"type": "string"},
                        "text": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "done", "skipped", "blocked"],
                        },
                        "note": {"type": "string"},
                    },
                    "required": ["text"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["work_item_id", "items"],
        "additionalProperties": False,
    }

    def cast_params(self, params: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(params)
        normalized["work_item_id"] = str(normalized.get("work_item_id") or "").strip()
        normalized["items"] = checklist_items_from_payload(normalized.get("items"))
        return normalized

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not params.get("work_item_id"):
            errors.append("work_item_id is required")
        if not params.get("items"):
            errors.append("items must contain at least one checklist item")
        return errors

    async def execute(self, *, work_item_id: str, items: list) -> ToolResult:
        checklist = await self._store.update_checklist(work_item_id, items)
        if checklist is None:
            return ToolResult(content=f"Error: unknown work_item_id: {work_item_id}", ok=False)
        event = ActionEventRecord(
            work_item_id=work_item_id,
            event_type="checklist_updated",
            source="tool",
            tool_name=self.name,
            summary=f"Updated checklist with {len(checklist.items)} items",
        )
        await self._store.record_action_event(event)
        return _json_result(
            {
                "ok": True,
                "action": "checklist_updated",
                "checklist": checklist.to_dict(),
            }
        )


class AttachEvidenceTool(_WorkItemTool):
    name = "attach_evidence"
    description = (
        "Attach completion or progress evidence to a WorkItem. Evidence should "
        "refer to a real result, business object, runtime task, or inspected output."
    )
    parameters = {
        "type": "object",
        "properties": {
            "work_item_id": {"type": "string"},
            "evidence_type": {"type": "string"},
            "summary": {"type": "string"},
            "business_ref_type": {"type": "string"},
            "business_ref_id": {"type": "string"},
            "runtime_task_id": {"type": "string"},
            "payload": {"type": "object"},
        },
        "required": ["work_item_id", "evidence_type", "summary"],
        "additionalProperties": False,
    }

    def cast_params(self, params: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(params)
        for key in (
            "work_item_id",
            "evidence_type",
            "summary",
            "business_ref_type",
            "business_ref_id",
            "runtime_task_id",
        ):
            normalized[key] = _optional_str(normalized.get(key))
        payload = normalized.get("payload")
        normalized["payload"] = payload if isinstance(payload, dict) else None
        return normalized

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        return [
            f"{key} is required"
            for key in ("work_item_id", "evidence_type", "summary")
            if not params.get(key)
        ]

    async def execute(
        self,
        *,
        work_item_id: str,
        evidence_type: str,
        summary: str,
        business_ref_type: str | None = None,
        business_ref_id: str | None = None,
        runtime_task_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> ToolResult:
        if await self._store.get_work_item(work_item_id) is None:
            return ToolResult(content=f"Error: unknown work_item_id: {work_item_id}", ok=False)
        evidence = WorkEvidenceRecord(
            work_item_id=work_item_id,
            evidence_type=evidence_type,
            summary=summary,
            business_ref_type=business_ref_type,
            business_ref_id=business_ref_id,
            runtime_task_id=runtime_task_id,
            payload=payload,
        )
        attached = await self._store.attach_evidence(evidence)
        event = ActionEventRecord(
            work_item_id=work_item_id,
            event_type="evidence_attached",
            source="tool",
            tool_name=self.name,
            runtime_task_id=runtime_task_id,
            summary=f"Attached evidence: {evidence_type}",
            payload={"evidence_id": attached.evidence_id},
        )
        await self._store.record_action_event(event)
        return _json_result(
            {
                "ok": True,
                "action": "evidence_attached",
                "evidence": attached.to_dict(),
            }
        )


class CompleteWorkItemTool(_WorkItemTool):
    name = "complete_work_item"
    description = (
        "Mark a WorkItem as completed after enough evidence exists. Do not use "
        "this only because the model believes the task is done."
    )
    parameters = {
        "type": "object",
        "properties": {
            "work_item_id": {"type": "string"},
            "summary": {"type": "string"},
        },
        "required": ["work_item_id", "summary"],
        "additionalProperties": False,
    }

    def cast_params(self, params: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(params)
        normalized["work_item_id"] = str(normalized.get("work_item_id") or "").strip()
        normalized["summary"] = str(normalized.get("summary") or "").strip()
        return normalized

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        return [
            f"{key} is required"
            for key in ("work_item_id", "summary")
            if not params.get(key)
        ]

    async def execute(self, *, work_item_id: str, summary: str) -> ToolResult:
        if await self._store.get_work_item(work_item_id) is None:
            return ToolResult(content=f"Error: unknown work_item_id: {work_item_id}", ok=False)
        evidence = await self._store.list_evidence(work_item_id, limit=1)
        if not evidence:
            return ToolResult(
                content=(
                    "Error: cannot complete WorkItem without evidence. "
                    "Attach evidence first."
                ),
                ok=False,
            )
        updated = await self._store.update_work_item(
            work_item_id,
            status="completed",
            current_summary=summary,
        )
        if updated is None:
            return ToolResult(content=f"Error: unknown work_item_id: {work_item_id}", ok=False)
        event = ActionEventRecord(
            work_item_id=work_item_id,
            event_type="work_item_completed",
            source="tool",
            tool_name=self.name,
            summary=summary,
        )
        await self._store.record_action_event(event)
        return _json_result(
            {
                "ok": True,
                "action": "work_item_completed",
                "work_item": updated.to_dict(),
            }
        )


def _json_result(payload: dict[str, Any]) -> ToolResult:
    return ToolResult(content=json.dumps(payload, ensure_ascii=False))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            result.append(text)
    return result


def _risk_level(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"low", "medium", "high"}:
        return text
    return "low"


def _optional_str(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
