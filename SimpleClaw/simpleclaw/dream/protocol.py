"""Dream runtime protocol.

Dream is a low-priority background runtime that reorganizes agent assets while
the session is idle. The framework only defines the governance contract here;
applications decide what assets can be read and where artifacts are applied.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from simpleclaw.runtime.task_protocol import BACKGROUND_STREAM, TaskEnvelope, TaskStream

if TYPE_CHECKING:
    from simpleclaw.dream.signal import DreamSignal


DreamTrigger = Literal[
    "idle_session",
    "memory_threshold",
    "runtime_task_completed",
    "subagent_completed",
    "cron",
    "manual",
    "system_monitor",
]
DreamStatus = Literal[
    "candidate",
    "admitted",
    "queued",
    "running",
    "succeeded",
    "failed",
    "skipped",
    "cancelled",
    "superseded",
]
DreamArtifactType = Literal[
    "memory_summary",
    "preference_profile",
    "task_lesson",
    "tool_usage_lesson",
    "failure_lesson",
    "skill_candidate",
    "context_cleanup",
]
DreamArtifactStatus = Literal["draft", "validated", "applied", "rejected", "expired"]


def now_ms() -> int:
    return int(time.time() * 1000)


def make_dream_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def dream_scope_key(*, tenant_key: str, session_key: str | None = None, namespace: str = "default") -> str:
    tenant = str(tenant_key or "").strip() or "__default__"
    session = str(session_key or "").strip() or "__global__"
    ns = str(namespace or "default").strip() or "default"
    return f"dream:{tenant}:{session}:{ns}"


@dataclass(slots=True)
class DreamCandidate:
    """A possible dream job emitted by a trigger before admission control."""

    tenant_key: str
    trigger: DreamTrigger
    reason: str
    session_key: str | None = None
    namespace: str = "default"
    source_id: str | None = None
    input_cursor: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    status: DreamStatus = "candidate"
    candidate_id: str = field(default_factory=lambda: make_dream_id("dreamcand"))
    created_at_ms: int = field(default_factory=now_ms)
    updated_at_ms: int = field(default_factory=now_ms)

    @classmethod
    def from_signal(
        cls,
        signal: DreamSignal,
        *,
        trigger: DreamTrigger = "system_monitor",
    ) -> "DreamCandidate":
        return signal.to_candidate(trigger=trigger)

    @property
    def scope_key(self) -> str:
        return dream_scope_key(
            tenant_key=self.tenant_key,
            session_key=self.session_key,
            namespace=self.namespace,
        )

    @property
    def dedupe_key(self) -> str:
        source = self.input_cursor or self.source_id or self.reason
        return f"{self.scope_key}:{self.trigger}:{source}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DreamJob:
    """An admitted dream candidate that can be executed by runtime workers."""

    tenant_key: str
    trigger: DreamTrigger
    reason: str
    candidate_id: str
    session_key: str | None = None
    namespace: str = "default"
    source_id: str | None = None
    input_cursor: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    status: DreamStatus = "admitted"
    job_id: str = field(default_factory=lambda: make_dream_id("dreamjob"))
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    admitted_at_ms: int = field(default_factory=now_ms)
    queued_at_ms: int | None = None
    started_at_ms: int | None = None
    completed_at_ms: int | None = None
    last_error: str | None = None

    @classmethod
    def from_candidate(cls, candidate: DreamCandidate) -> "DreamJob":
        return cls(
            tenant_key=candidate.tenant_key,
            session_key=candidate.session_key,
            namespace=candidate.namespace,
            trigger=candidate.trigger,
            reason=candidate.reason,
            candidate_id=candidate.candidate_id,
            source_id=candidate.source_id,
            input_cursor=candidate.input_cursor,
            payload=dict(candidate.payload or {}),
        )

    @property
    def scope_key(self) -> str:
        return dream_scope_key(
            tenant_key=self.tenant_key,
            session_key=self.session_key,
            namespace=self.namespace,
        )

    def to_task_envelope(
        self,
        *,
        stream: TaskStream = BACKGROUND_STREAM,
        task_type: str = "dream",
        service_role: str = "simpleclaw:dream",
    ) -> TaskEnvelope:
        payload = dict(self.payload or {})
        payload.update({
            "dream_job": self.to_dict(),
            "trigger": self.trigger,
            "reason": self.reason,
            "namespace": self.namespace,
            "source_id": self.source_id,
            "input_cursor": self.input_cursor,
        })
        return TaskEnvelope(
            task_type=task_type,
            payload=payload,
            stream=stream,
            tenant_key=self.tenant_key,
            session_key=self.session_key,
            scope_key=self.scope_key,
            trace_id=self.trace_id,
            task_id=self.job_id,
            service_role=service_role,
        )

    @classmethod
    def from_task_envelope(cls, task: TaskEnvelope) -> "DreamJob":
        raw = dict(task.payload.get("dream_job") or {})
        if not raw:
            raw = {
                "tenant_key": task.tenant_key or task.payload.get("tenant_key") or "",
                "session_key": task.session_key or task.payload.get("session_key"),
                "namespace": task.payload.get("namespace") or "default",
                "trigger": task.payload.get("trigger") or "manual",
                "reason": task.payload.get("reason") or "dream task",
                "candidate_id": task.payload.get("candidate_id") or task.task_id,
                "source_id": task.payload.get("source_id"),
                "input_cursor": task.payload.get("input_cursor"),
                "payload": dict(task.payload or {}),
                "job_id": task.task_id,
                "trace_id": task.trace_id,
            }
        raw["payload"] = dict(raw.get("payload") or {})
        return cls(**raw)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DreamArtifact:
    """A durable output produced by a dream job."""

    job_id: str
    artifact_type: DreamArtifactType
    content: str
    key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    status: DreamArtifactStatus = "draft"
    artifact_id: str = field(default_factory=lambda: make_dream_id("dreamart"))
    created_at_ms: int = field(default_factory=now_ms)
    updated_at_ms: int = field(default_factory=now_ms)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class DreamResult:
    """Executor result for a dream job."""

    job_id: str
    status: DreamStatus
    summary: str = ""
    artifacts: list[DreamArtifact] = field(default_factory=list)
    last_error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    completed_at_ms: int = field(default_factory=now_ms)

    @classmethod
    def succeeded(
        cls,
        job_id: str,
        *,
        summary: str = "",
        artifacts: list[DreamArtifact] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "DreamResult":
        return cls(
            job_id=job_id,
            status="succeeded",
            summary=summary,
            artifacts=list(artifacts or []),
            metadata=dict(metadata or {}),
        )

    @classmethod
    def skipped(cls, job_id: str, *, summary: str = "") -> "DreamResult":
        return cls(job_id=job_id, status="skipped", summary=summary)

    @classmethod
    def failed(cls, job_id: str, error: str, *, summary: str = "") -> "DreamResult":
        return cls(
            job_id=job_id,
            status="failed",
            summary=summary or error,
            last_error=error,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
