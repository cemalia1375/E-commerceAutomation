"""Subagent runtime governance protocol.

This module does not execute subagents. It defines the framework-level facts
for treating one subagent run as a governed runtime event: why it ran, who owns
it, what it may access, and what durable artifacts it produced.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from simpleclaw.runtime.session_ingress_protocol import SessionIngressDeliveryPolicy
from simpleclaw.runtime.task_protocol import make_trace_id


SubagentRunMode = Literal["chat", "handoff", "background", "dream"]
SubagentRunStatus = Literal[
    "candidate",
    "admitted",
    "running",
    "completed",
    "failed",
    "skipped",
    "cancelled",
    "superseded",
]
SubagentRunOwnerType = Literal[
    "runtime_task",
    "memory_ledger",
    "dream_job",
    "system_activation",
    "session_ingress",
    "manual",
    "external",
]
SubagentPermission = Literal[
    "read_memory",
    "read_memory_ledger",
    "read_runtime_task",
    "read_tool_invocation",
    "read_document_version",
    "read_session_messages",
    "write_artifact",
    "validate_artifact",
    "apply_memory",
    "apply_document",
    "notify_user",
    "call_business_tool",
]
SubagentArtifactStatus = Literal["draft", "validated", "applied", "rejected", "expired"]


def now_ms() -> int:
    return int(time.time() * 1000)


def make_subagent_run_id() -> str:
    return f"subrun_{uuid.uuid4().hex}"


def make_subagent_artifact_id() -> str:
    return f"subart_{uuid.uuid4().hex}"


def subagent_run_scope_key(
    *,
    tenant_key: str,
    subagent_name: str,
    session_key: str | None = None,
    owner_type: str | None = None,
    owner_id: str | None = None,
) -> str:
    """Build a stable scope key for dedupe and per-scope serialization."""

    tenant = str(tenant_key or "").strip() or "__default__"
    agent = str(subagent_name or "").strip() or "__subagent__"
    session = str(session_key or "").strip() or "__global__"
    owner = f"{str(owner_type or '').strip()}:{str(owner_id or '').strip()}".strip(":")
    owner = owner or "__unowned__"
    return f"subagent:{tenant}:{session}:{agent}:{owner}"


@dataclass(slots=True)
class SubagentRunRequest:
    """A normalized request to run a subagent under runtime governance."""

    tenant_key: str
    session_key: str
    subagent_name: str
    objective: str
    run_mode: SubagentRunMode = "handoff"
    owner_type: SubagentRunOwnerType = "manual"
    owner_id: str | None = None
    trace_id: str = field(default_factory=make_trace_id)
    run_id: str = field(default_factory=make_subagent_run_id)
    status: SubagentRunStatus = "candidate"
    input_refs: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)
    permission_profile: list[SubagentPermission] = field(default_factory=list)
    expected_artifacts: list[str] = field(default_factory=list)
    delivery_policy: SessionIngressDeliveryPolicy = "best_effort"
    dedupe_key: str | None = None
    created_at_ms: int = field(default_factory=now_ms)

    @property
    def scope_key(self) -> str:
        return subagent_run_scope_key(
            tenant_key=self.tenant_key,
            session_key=self.session_key,
            subagent_name=self.subagent_name,
            owner_type=self.owner_type,
            owner_id=self.owner_id,
        )

    @property
    def effective_dedupe_key(self) -> str:
        if self.dedupe_key:
            return self.dedupe_key
        cursor = self.input_refs.get("cursor") or self.input_refs.get("input_cursor")
        source = cursor or self.owner_id or self.objective
        return f"{self.scope_key}:{self.run_mode}:{source}"

    def allows(self, permission: SubagentPermission) -> bool:
        return permission in set(self.permission_profile)

    def admitted(self) -> "SubagentRunRequest":
        return self.with_status("admitted")

    def with_status(self, status: SubagentRunStatus) -> "SubagentRunRequest":
        data = self.to_dict()
        data["status"] = status
        return SubagentRunRequest(**data)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SubagentArtifact:
    """A durable structured output produced by a governed subagent run."""

    run_id: str
    artifact_type: str
    content: str
    owner_type: SubagentRunOwnerType = "manual"
    owner_id: str | None = None
    key: str | None = None
    status: SubagentArtifactStatus = "draft"
    metadata: dict[str, Any] = field(default_factory=dict)
    source_refs: dict[str, Any] = field(default_factory=dict)
    artifact_id: str = field(default_factory=make_subagent_artifact_id)
    created_at_ms: int = field(default_factory=now_ms)
    updated_at_ms: int = field(default_factory=now_ms)

    def with_status(self, status: SubagentArtifactStatus) -> "SubagentArtifact":
        data = self.to_dict()
        data["status"] = status
        data["updated_at_ms"] = now_ms()
        return SubagentArtifact(**data)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SubagentRunResult:
    """Result wrapper for a governed subagent run.

    Chat-style subagents may only populate reply_text. Background subagents
    should prefer artifacts and metadata over user-facing text.
    """

    run_id: str
    status: SubagentRunStatus
    summary: str = ""
    reply_text: str = ""
    artifacts: list[SubagentArtifact] = field(default_factory=list)
    read_refs: dict[str, Any] = field(default_factory=dict)
    write_refs: dict[str, Any] = field(default_factory=dict)
    tool_invocations: list[dict[str, Any]] = field(default_factory=list)
    side_effects: list[dict[str, Any]] = field(default_factory=list)
    last_error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    completed_at_ms: int = field(default_factory=now_ms)

    @property
    def ok(self) -> bool:
        return self.status in {"completed", "skipped"}

    @classmethod
    def completed(
        cls,
        run_id: str,
        *,
        summary: str = "",
        reply_text: str = "",
        artifacts: list[SubagentArtifact] | None = None,
        read_refs: dict[str, Any] | None = None,
        write_refs: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "SubagentRunResult":
        return cls(
            run_id=run_id,
            status="completed",
            summary=summary,
            reply_text=reply_text,
            artifacts=list(artifacts or []),
            read_refs=dict(read_refs or {}),
            write_refs=dict(write_refs or {}),
            metadata=dict(metadata or {}),
        )

    @classmethod
    def skipped(cls, run_id: str, *, summary: str = "") -> "SubagentRunResult":
        return cls(run_id=run_id, status="skipped", summary=summary)

    @classmethod
    def failed(cls, run_id: str, error: str, *, summary: str = "") -> "SubagentRunResult":
        return cls(
            run_id=run_id,
            status="failed",
            summary=summary or error,
            last_error=error,
        )

    @classmethod
    def cancelled(cls, run_id: str, *, summary: str = "") -> "SubagentRunResult":
        return cls(run_id=run_id, status="cancelled", summary=summary)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
