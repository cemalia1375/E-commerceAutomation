"""Runtime observation helpers."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCallRecord:
    tool_name: str
    ok: bool
    duration_ms: int
    tool_call_id: str | None = None
    arguments: dict[str, Any] | None = None
    action: str | None = None
    status: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


@dataclass
class TurnCapture:
    started_at: float = field(default_factory=time.perf_counter)
    first_token_at: float | None = None
    done_at: float | None = None
    tools: list[ToolCallRecord] = field(default_factory=list)

    def mark_first_token(self) -> None:
        if self.first_token_at is None:
            self.first_token_at = time.perf_counter()

    def mark_done(self) -> None:
        self.done_at = time.perf_counter()

    @property
    def ttft_ms(self) -> int | None:
        if self.first_token_at is None:
            return None
        return int((self.first_token_at - self.started_at) * 1000)

    @property
    def total_ms(self) -> int | None:
        if self.done_at is None:
            return None
        return int((self.done_at - self.started_at) * 1000)

    @property
    def tools_called(self) -> list[str]:
        return [item.tool_name for item in self.tools]


def wrap_tool_registry(registry: Any, capture: TurnCapture):
    """Wrap ToolRegistry.execute for one turn and return a restore callback."""
    original = registry.execute

    async def wrapped(call):
        started = time.perf_counter()
        try:
            result = await original(call)
            summary = _tool_result_summary(result)
            capture.tools.append(
                ToolCallRecord(
                    tool_name=call.name,
                    ok=bool(getattr(result, "ok", False)),
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    tool_call_id=getattr(call, "id", None),
                    arguments=getattr(call, "arguments", None),
                    action=summary.get("action"),
                    status=summary.get("status"),
                    result=summary,
                )
            )
            return result
        except Exception as exc:
            capture.tools.append(
                ToolCallRecord(
                    tool_name=call.name,
                    ok=False,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    tool_call_id=getattr(call, "id", None),
                    arguments=getattr(call, "arguments", None),
                    error=str(exc),
                )
            )
            raise

    registry.execute = wrapped

    def restore() -> None:
        registry.execute = original

    return restore


def wrap_all_tool_registries(capture: TurnCapture):
    """Wrap ToolRegistry.execute at class level for one turn.

    Direct sub-agent turns create a fresh registry inside SubagentStore, so an
    instance-level wrapper cannot see those calls. Scenario runs are isolated
    and single-turn at this point, making a temporary class wrapper acceptable.
    """
    from simpleclaw.tools.registry import ToolRegistry

    original = ToolRegistry.execute

    async def wrapped(self, call):
        started = time.perf_counter()
        try:
            result = await original(self, call)
            summary = _tool_result_summary(result)
            capture.tools.append(
                ToolCallRecord(
                    tool_name=call.name,
                    ok=bool(getattr(result, "ok", False)),
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    tool_call_id=getattr(call, "id", None),
                    arguments=getattr(call, "arguments", None),
                    action=summary.get("action"),
                    status=summary.get("status"),
                    result=summary,
                )
            )
            return result
        except Exception as exc:
            capture.tools.append(
                ToolCallRecord(
                    tool_name=call.name,
                    ok=False,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    tool_call_id=getattr(call, "id", None),
                    arguments=getattr(call, "arguments", None),
                    error=str(exc),
                )
            )
            raise

    ToolRegistry.execute = wrapped

    def restore() -> None:
        ToolRegistry.execute = original

    return restore


def _tool_result_summary(result: Any) -> dict[str, Any]:
    raw = getattr(result, "content", None)
    if raw is None:
        return {}
    if isinstance(raw, dict):
        parsed = raw
    else:
        try:
            parsed = json.loads(str(raw))
        except Exception:
            return {"content_preview": str(raw)[:500]}
    if not isinstance(parsed, dict):
        return {"content_preview": str(parsed)[:500]}
    keys = (
        "ok", "action", "status", "reason", "phase", "task_id", "queue_id",
        "route", "evidence_type", "message_focus", "image_status", "source", "subagent", "session_key",
        "where", "capability", "runtime_task_id", "business_ref_type", "business_ref_id",
        "model_guidance", "user_visible_summary", "facts",
    )
    return {key: parsed.get(key) for key in keys if key in parsed}
