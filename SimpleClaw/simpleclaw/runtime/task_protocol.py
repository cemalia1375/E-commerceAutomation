"""后台任务协议 — 队列信封、运行态任务与通用任务结果定义。

设计原则：
  - 通用层，不依赖任何业务逻辑
  - 可在 SimpleClaw 所有业务方（Mojing 等）中复用
  - 与旧框架 nanobot.runtime.task_protocol 兼容

Stream 分工：
  - simpleclaw 只定义通用协议，不枚举业务方 stream 名。
  - 业务方应在自己的命名空间集中定义 stream 常量 / 枚举。
  - background / dead-letter 是通用保留流名。
"""

from __future__ import annotations

import json
import socket
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal


BaseTaskStream = Literal["background", "dead-letter"]
BACKGROUND_STREAM: BaseTaskStream = "background"
DEAD_LETTER_STREAM: BaseTaskStream = "dead-letter"
TaskStream = str
TaskStatus = Literal["succeeded", "failed", "noop", "wait_external"]
RuntimeTaskStatus = Literal[
    "queued",
    "running",
    "wait_external",
    "succeeded",
    "failed",
]


def _now_ms() -> int:
    return int(time.time() * 1000)


def make_trace_id() -> str:
    return uuid.uuid4().hex


def make_consumer_name(role: str) -> str:
    return f"{role}:{socket.gethostname()}:{uuid.uuid4().hex[:8]}"


@dataclass(slots=True)
class TaskEnvelope:
    """跨服务传递的任务信封。

    scope_key 用于细粒度保序：相同 scope_key 的任务在同一进程内串行执行。
    """

    task_type: str
    payload: dict[str, Any]
    stream: TaskStream
    tenant_key: str | None = None
    session_key: str | None = None
    scope_key: str | None = None
    trace_id: str = field(default_factory=make_trace_id)
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    attempt: int = 0
    max_attempts: int = 3
    created_at_ms: int = field(default_factory=_now_ms)
    service_role: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TaskEnvelope":
        return cls(
            task_type=str(d["task_type"]),
            payload=dict(d.get("payload") or {}),
            stream=str(d["stream"]),
            tenant_key=d.get("tenant_key"),
            session_key=d.get("session_key"),
            scope_key=d.get("scope_key"),
            trace_id=str(d.get("trace_id") or make_trace_id()),
            task_id=str(d.get("task_id") or uuid.uuid4().hex),
            attempt=int(d.get("attempt") or 0),
            max_attempts=int(d.get("max_attempts") or 3),
            created_at_ms=int(d.get("created_at_ms") or _now_ms()),
            service_role=d.get("service_role"),
        )

    @classmethod
    def from_json(cls, s: str) -> "TaskEnvelope":
        return cls.from_dict(json.loads(s))


@dataclass(slots=True)
class RuntimeEvidence:
    """RuntimeTask 完成或推进时产生的轻量证明。

    Evidence 是通用框架层的事实摘要，不是业务表。业务层可以把它映射
    到 MySQL/Postgres/Redis/日志，框架默认只要求它能被 store 记录和读取。
    """

    evidence_type: str
    task_id: str | None = None
    trace_id: str | None = None
    tenant_key: str | None = None
    session_key: str | None = None
    business_ref_type: str | None = None
    business_ref_id: str | None = None
    summary: str | None = None
    payload: dict[str, Any] | None = None
    evidence_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at_ms: int = field(default_factory=_now_ms)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RuntimeTaskRecord:
    """后台或可追踪执行单元的事实状态。

    RuntimeTask 不控制执行流程，只描述当前已确认的事实状态。同步读工具
    通常只需要 ToolInvocation，不需要 RuntimeTask；同步写和 durable 工具
    可以用 RuntimeTask 表达可追踪结果。
    """

    task_id: str
    task_type: str
    status: RuntimeTaskStatus
    tenant_key: str | None = None
    session_key: str | None = None
    trace_id: str | None = None
    tool_name: str | None = None
    queue_message_id: str | None = None
    external_job_id: str | None = None
    business_ref_type: str | None = None
    business_ref_id: str | None = None
    summary: str | None = None
    error: str | None = None
    input_json: dict[str, Any] | None = None
    output_json: dict[str, Any] | None = None
    created_at_ms: int = field(default_factory=_now_ms)
    updated_at_ms: int = field(default_factory=_now_ms)

    @classmethod
    def from_envelope(
        cls,
        task: TaskEnvelope,
        *,
        status: RuntimeTaskStatus = "queued",
        tool_name: str | None = None,
        queue_message_id: str | None = None,
        summary: str | None = None,
    ) -> "RuntimeTaskRecord":
        now = _now_ms()
        return cls(
            task_id=task.task_id,
            task_type=task.task_type,
            status=status,
            tenant_key=task.tenant_key,
            session_key=task.session_key,
            trace_id=task.trace_id,
            tool_name=tool_name,
            queue_message_id=queue_message_id,
            summary=summary,
            input_json=dict(task.payload or {}),
            created_at_ms=task.created_at_ms or now,
            updated_at_ms=now,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TaskExecutionResult:
    """Executor 返回给 Worker 的结构化结果。

    四种状态：
      succeeded — 任务执行并产生了实际变更
      noop      — 任务执行但没有状态变更（例如 postprocess 没有变更 docs）
      wait_external
                — 已调用外部系统，但业务结果尚未确认完成
      failed    — 任务执行失败，worker 据此决定是否重试
    """

    status: TaskStatus
    summary: str | None = None
    error: str | None = None
    details: dict[str, Any] | None = None
    evidence: RuntimeEvidence | list[RuntimeEvidence] | None = None

    @classmethod
    def succeeded(
        cls,
        summary: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        evidence: RuntimeEvidence | list[RuntimeEvidence] | None = None,
    ) -> "TaskExecutionResult":
        return cls(status="succeeded", summary=summary, details=details, evidence=evidence)

    @classmethod
    def noop(
        cls,
        summary: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        evidence: RuntimeEvidence | list[RuntimeEvidence] | None = None,
    ) -> "TaskExecutionResult":
        return cls(status="noop", summary=summary, details=details, evidence=evidence)

    @classmethod
    def triggered(
        cls,
        summary: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        evidence: RuntimeEvidence | list[RuntimeEvidence] | None = None,
    ) -> "TaskExecutionResult":
        return cls(status="wait_external", summary=summary, details=details, evidence=evidence)

    @classmethod
    def wait_external(
        cls,
        summary: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        evidence: RuntimeEvidence | list[RuntimeEvidence] | None = None,
    ) -> "TaskExecutionResult":
        return cls(status="wait_external", summary=summary, details=details, evidence=evidence)

    @classmethod
    def waiting_external(
        cls,
        summary: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        evidence: RuntimeEvidence | list[RuntimeEvidence] | None = None,
    ) -> "TaskExecutionResult":
        return cls(
            status="wait_external",
            summary=summary,
            details=details,
            evidence=evidence,
        )

    @classmethod
    def failed(
        cls,
        error: str,
        summary: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        evidence: RuntimeEvidence | list[RuntimeEvidence] | None = None,
    ) -> "TaskExecutionResult":
        return cls(
            status="failed",
            summary=summary,
            error=error,
            details=details,
            evidence=evidence,
        )
