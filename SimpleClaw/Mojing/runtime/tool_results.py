"""Canonical model-visible tool result payloads for Mojing.

These helpers describe the outcome of the tool invocation itself. They do not
claim the durable business task has completed.
"""

from __future__ import annotations

import json
from typing import Any

from simpleclaw.tools.base import ToolResult

_DEFAULT_SUBMITTED_GUIDANCE = (
    "工具调用已经提交异步任务。请告诉用户任务已开始处理；不要说业务结果已经完成。"
)
_DEFAULT_DEDUPED_GUIDANCE = (
    "同类任务已经在处理中或本轮已经提交过。请基于已有任务状态回复用户，"
    "不要重复触发，也不要说业务结果已经完成。"
)
_DEFAULT_NO_CHANGE_GUIDANCE = (
    "工具调用成功，但没有产生新的业务动作。请如实说明当前无需重复处理，"
    "不要说生成了新的结果。"
)


def json_tool_result(payload: dict[str, Any], *, ok: bool | None = None) -> ToolResult:
    """Return a compact JSON ToolResult with None values removed."""
    clean = {key: value for key, value in payload.items() if value is not None}
    result_ok = bool(clean.get("ok", True)) if ok is None else bool(ok)
    clean["ok"] = result_ok
    return ToolResult(content=json.dumps(clean, ensure_ascii=False, default=str), ok=result_ok)


def tool_submitted(
    *,
    task_id: str,
    queue_id: str,
    tool: str | None = None,
    message_focus: str,
    model_guidance: str | None = None,
    include_model_guidance: bool = True,
    runtime_task_status: str = "queued",
    **extra: Any,
) -> ToolResult:
    """A durable tool passed gate and submitted a RuntimeTask."""
    payload: dict[str, Any] = {
        "ok": True,
        "action": "submitted",
        "invocation_status": "submitted",
        "runtime_task_created": True,
        "runtime_task_status": runtime_task_status,
        "tool": tool,
        "task_id": task_id,
        "queue_id": queue_id,
        "message_focus": message_focus,
    }
    if include_model_guidance:
        payload["model_guidance"] = model_guidance or _DEFAULT_SUBMITTED_GUIDANCE
    payload.update(extra)
    return json_tool_result(payload, ok=True)


def tool_deduped(
    *,
    reason: str,
    message_focus: str,
    phase: str = "in_progress",
    source: str | None = None,
    runtime_task_status: str | None = None,
    model_guidance: str | None = None,
    **extra: Any,
) -> ToolResult:
    """A tool invocation was intentionally short-circuited as duplicate."""
    payload: dict[str, Any] = {
        "ok": True,
        "action": "deduped",
        "invocation_status": "deduped",
        "runtime_task_created": False,
        "reason": reason,
        "phase": phase,
        "source": source,
        "runtime_task_status": runtime_task_status,
        "message_focus": message_focus,
        "model_guidance": model_guidance or _DEFAULT_DEDUPED_GUIDANCE,
    }
    payload.update(extra)
    return json_tool_result(payload, ok=True)


def tool_no_change(
    *,
    reason: str,
    message_focus: str,
    model_guidance: str | None = None,
    **extra: Any,
) -> ToolResult:
    """A synchronous tool succeeded but no new business action was needed."""
    payload: dict[str, Any] = {
        "ok": True,
        "action": "no_change",
        "invocation_status": "no_change",
        "outcome": "no_change",
        "runtime_task_created": False,
        "reason": reason,
        "message_focus": message_focus,
        "model_guidance": model_guidance or _DEFAULT_NO_CHANGE_GUIDANCE,
    }
    payload.update(extra)
    return json_tool_result(payload, ok=True)
