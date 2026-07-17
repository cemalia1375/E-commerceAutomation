"""Optional tool invocation tracing protocol.

This module is intentionally storage-agnostic. Applications can implement
ToolInvocationStore with MySQL, Postgres, OpenTelemetry, log files, or no-op
storage without changing the SimpleClaw runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol


ToolCategory = Literal["sync_read", "sync_write", "async_task"]
ToolInvocationStatus = Literal[
    "requested",
    "running",
    "submitted",
    "succeeded",
    "failed",
    "blocked",
    "deduped",
]


@dataclass(slots=True)
class ToolInvocationRecord:
    """A model-requested tool call and its execution linkage."""

    invocation_id: str
    tenant_key: str
    session_key: str
    tool_call_id: str | None
    tool_name: str
    tool_category: str
    execution_mode: str
    status: str
    input_json: dict[str, Any] | None = None
    runtime_task_id: str | None = None
    business_ref_type: str | None = None
    business_ref_id: str | None = None
    trace_id: str | None = None
    output_summary: str | None = None
    last_error: str | None = None


@dataclass(slots=True)
class ToolInvocationCompletion:
    """Completion update for a previously recorded tool invocation."""

    status: str
    output_summary: str | None = None
    runtime_task_id: str | None = None
    business_ref_type: str | None = None
    business_ref_id: str | None = None
    trace_id: str | None = None
    last_error: str | None = None


class ToolInvocationStore(Protocol):
    """Storage protocol for tool invocation tracing."""

    async def record_started(self, record: ToolInvocationRecord) -> None: ...

    async def mark_completed(
        self,
        invocation_id: str,
        completion: ToolInvocationCompletion,
    ) -> None: ...
