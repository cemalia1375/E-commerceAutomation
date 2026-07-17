"""RuntimeTask 状态存储协议与默认内存实现。

框架层只定义状态事实协议，不规定业务表。生产业务可以用 MySQL、
Postgres、Redis、日志系统等实现 RuntimeTaskStore；默认内存实现仅用于
本地开发和 smoke test，进程退出即丢失。
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any, Protocol

from simpleclaw.runtime.task_protocol import (
    RuntimeEvidence,
    RuntimeTaskRecord,
    RuntimeTaskStatus,
    TaskEnvelope,
    TaskExecutionResult,
)


class RuntimeTaskStore(Protocol):
    """Storage protocol for runtime task facts."""

    async def record_queued(
        self,
        task: TaskEnvelope,
        *,
        queue_message_id: str | None = None,
        tool_name: str | None = None,
        summary: str | None = None,
    ) -> RuntimeTaskRecord | None: ...

    async def attach_queue_message_id(
        self,
        task: TaskEnvelope | str,
        queue_message_id: str,
    ) -> RuntimeTaskRecord | None: ...

    async def mark_running(
        self,
        task: TaskEnvelope | str,
        *,
        claimed_by: str | None = None,
        summary: str | None = None,
    ) -> RuntimeTaskRecord | None: ...

    async def mark_wait_external(
        self,
        task: TaskEnvelope | str,
        *,
        external_job_id: str | None = None,
        summary: str | None = None,
        evidence: RuntimeEvidence | list[RuntimeEvidence] | None = None,
    ) -> RuntimeTaskRecord | None: ...

    async def mark_waiting_external(
        self,
        task: TaskEnvelope | str,
        *,
        external_job_id: str | None = None,
        summary: str | None = None,
        evidence: RuntimeEvidence | list[RuntimeEvidence] | None = None,
    ) -> RuntimeTaskRecord | None: ...

    async def mark_succeeded(
        self,
        task: TaskEnvelope | str,
        *,
        summary: str | None = None,
        business_ref_type: str | None = None,
        business_ref_id: str | None = None,
        output_json: dict[str, Any] | None = None,
        evidence: RuntimeEvidence | list[RuntimeEvidence] | None = None,
    ) -> RuntimeTaskRecord | None: ...

    async def mark_failed(
        self,
        task: TaskEnvelope | str,
        error: str,
        *,
        claimed_by: str | None = None,
        summary: str | None = None,
        evidence: RuntimeEvidence | list[RuntimeEvidence] | None = None,
    ) -> RuntimeTaskRecord | None: ...

    async def mark_finished(
        self,
        task: TaskEnvelope,
        result: TaskExecutionResult,
    ) -> RuntimeTaskRecord | None: ...

    async def get(self, task_id: str) -> RuntimeTaskRecord | None: ...

    async def list_recent_updates(
        self,
        *,
        tenant_key: str | None = None,
        session_key: str | None = None,
        since_ms: int | None = None,
        limit: int = 20,
    ) -> list[RuntimeTaskRecord]: ...

    async def record_evidence(
        self,
        task_id: str,
        evidence: RuntimeEvidence | list[RuntimeEvidence],
    ) -> list[RuntimeEvidence]: ...

    async def list_evidence(
        self,
        task_id: str,
    ) -> list[RuntimeEvidence]: ...


class InMemoryRuntimeTaskStore:
    """Process-local RuntimeTask store for development and tests."""

    def __init__(self) -> None:
        self._tasks: dict[str, RuntimeTaskRecord] = {}
        self._evidence: dict[str, list[RuntimeEvidence]] = {}
        self._lock = asyncio.Lock()

    async def record_queued(
        self,
        task: TaskEnvelope,
        *,
        queue_message_id: str | None = None,
        tool_name: str | None = None,
        summary: str | None = None,
    ) -> RuntimeTaskRecord:
        async with self._lock:
            existing = self._tasks.get(task.task_id)
            if existing is None:
                record = RuntimeTaskRecord.from_envelope(
                    task,
                    status="queued",
                    tool_name=tool_name,
                    queue_message_id=queue_message_id,
                    summary=summary,
                )
            else:
                record = _update_record(
                    existing,
                    status="queued",
                    queue_message_id=queue_message_id,
                    tool_name=tool_name,
                    summary=summary,
                    error=None,
                )
            self._tasks[record.task_id] = record
            return record

    async def attach_queue_message_id(
        self,
        task: TaskEnvelope | str,
        queue_message_id: str,
    ) -> RuntimeTaskRecord:
        async with self._lock:
            record = self._ensure_record(task)
            record = _update_record(
                record,
                status=record.status,
                queue_message_id=queue_message_id,
            )
            self._tasks[record.task_id] = record
            return record

    async def mark_running(
        self,
        task: TaskEnvelope | str,
        *,
        claimed_by: str | None = None,
        summary: str | None = None,
    ) -> RuntimeTaskRecord:
        del claimed_by
        async with self._lock:
            record = self._ensure_record(task)
            record = _update_record(
                record,
                status="running",
                summary=summary,
                error=None,
            )
            self._tasks[record.task_id] = record
            return record

    async def mark_wait_external(
        self,
        task: TaskEnvelope | str,
        *,
        external_job_id: str | None = None,
        summary: str | None = None,
        evidence: RuntimeEvidence | list[RuntimeEvidence] | None = None,
    ) -> RuntimeTaskRecord:
        async with self._lock:
            record = self._ensure_record(task)
            record = _update_record(
                record,
                status="wait_external",
                external_job_id=external_job_id,
                summary=summary,
                error=None,
            )
            self._tasks[record.task_id] = record
            self._record_evidence_locked(record.task_id, evidence, record)
            return record

    async def mark_waiting_external(
        self,
        task: TaskEnvelope | str,
        *,
        external_job_id: str | None = None,
        summary: str | None = None,
        evidence: RuntimeEvidence | list[RuntimeEvidence] | None = None,
    ) -> RuntimeTaskRecord:
        return await self.mark_wait_external(
            task,
            external_job_id=external_job_id,
            summary=summary,
            evidence=evidence,
        )

    async def mark_succeeded(
        self,
        task: TaskEnvelope | str,
        *,
        summary: str | None = None,
        business_ref_type: str | None = None,
        business_ref_id: str | None = None,
        output_json: dict[str, Any] | None = None,
        evidence: RuntimeEvidence | list[RuntimeEvidence] | None = None,
    ) -> RuntimeTaskRecord:
        async with self._lock:
            record = self._ensure_record(task)
            inferred = _last_evidence(evidence)
            record = _update_record(
                record,
                status="succeeded",
                summary=summary,
                business_ref_type=business_ref_type or _evidence_ref_type(inferred),
                business_ref_id=business_ref_id or _evidence_ref_id(inferred),
                output_json=output_json,
                error=None,
            )
            self._tasks[record.task_id] = record
            self._record_evidence_locked(record.task_id, evidence, record)
            return record

    async def mark_failed(
        self,
        task: TaskEnvelope | str,
        error: str,
        *,
        claimed_by: str | None = None,
        summary: str | None = None,
        evidence: RuntimeEvidence | list[RuntimeEvidence] | None = None,
    ) -> RuntimeTaskRecord:
        del claimed_by
        async with self._lock:
            record = self._ensure_record(task)
            record = _update_record(
                record,
                status="failed",
                summary=summary,
                error=error,
            )
            self._tasks[record.task_id] = record
            self._record_evidence_locked(record.task_id, evidence, record)
            return record

    async def mark_finished(
        self,
        task: TaskEnvelope,
        result: TaskExecutionResult,
    ) -> RuntimeTaskRecord:
        status = str(result.status or "").strip().lower()
        details = dict(result.details or {})
        if status == "failed":
            return await self.mark_failed(
                task,
                result.error or "runtime task failed",
                summary=result.summary,
                evidence=result.evidence,
            )
        if status in {"wait_external", "triggered", "waiting_external", "external"}:
            return await self.mark_wait_external(
                task,
                external_job_id=_optional_str(details.get("external_job_id")),
                summary=result.summary,
                evidence=result.evidence,
            )
        return await self.mark_succeeded(
            task,
            summary=result.summary,
            business_ref_type=_optional_str(details.get("business_ref_type")),
            business_ref_id=_optional_str(details.get("business_ref_id")),
            output_json=details or None,
            evidence=result.evidence,
        )

    async def get(self, task_id: str) -> RuntimeTaskRecord | None:
        async with self._lock:
            return self._tasks.get(str(task_id or "").strip())

    async def list_recent_updates(
        self,
        *,
        tenant_key: str | None = None,
        session_key: str | None = None,
        since_ms: int | None = None,
        limit: int = 20,
    ) -> list[RuntimeTaskRecord]:
        async with self._lock:
            records = list(self._tasks.values())
        if tenant_key is not None:
            records = [r for r in records if r.tenant_key == tenant_key]
        if session_key is not None:
            records = [r for r in records if r.session_key == session_key]
        if since_ms is not None:
            records = [r for r in records if r.updated_at_ms >= since_ms]
        records.sort(key=lambda r: r.updated_at_ms, reverse=True)
        return records[: max(1, int(limit or 20))]

    async def record_evidence(
        self,
        task_id: str,
        evidence: RuntimeEvidence | list[RuntimeEvidence],
    ) -> list[RuntimeEvidence]:
        async with self._lock:
            record = self._tasks.get(task_id)
            return self._record_evidence_locked(task_id, evidence, record)

    async def list_evidence(self, task_id: str) -> list[RuntimeEvidence]:
        async with self._lock:
            return list(self._evidence.get(str(task_id or "").strip(), []))

    def _ensure_record(self, task: TaskEnvelope | str) -> RuntimeTaskRecord:
        if isinstance(task, TaskEnvelope):
            existing = self._tasks.get(task.task_id)
            if existing is not None:
                return existing
            record = RuntimeTaskRecord.from_envelope(task, status="queued")
            self._tasks[record.task_id] = record
            return record

        task_id = str(task or "").strip()
        if not task_id:
            raise ValueError("task_id is required")
        existing = self._tasks.get(task_id)
        if existing is not None:
            return existing
        record = RuntimeTaskRecord(
            task_id=task_id,
            task_type="unknown",
            status="queued",
        )
        self._tasks[task_id] = record
        return record

    def _record_evidence_locked(
        self,
        task_id: str,
        evidence: RuntimeEvidence | list[RuntimeEvidence] | None,
        record: RuntimeTaskRecord | None,
    ) -> list[RuntimeEvidence]:
        normalized = _normalize_evidence(evidence, record)
        if not normalized:
            return []
        bucket = self._evidence.setdefault(task_id, [])
        bucket.extend(normalized)
        return normalized


TaskStateStore = RuntimeTaskStore


def _update_record(
    record: RuntimeTaskRecord,
    *,
    status: RuntimeTaskStatus,
    queue_message_id: str | None = None,
    tool_name: str | None = None,
    external_job_id: str | None = None,
    business_ref_type: str | None = None,
    business_ref_id: str | None = None,
    summary: str | None = None,
    error: str | None = None,
    output_json: dict[str, Any] | None = None,
) -> RuntimeTaskRecord:
    return replace(
        record,
        status=status,
        queue_message_id=queue_message_id or record.queue_message_id,
        tool_name=tool_name or record.tool_name,
        external_job_id=external_job_id or record.external_job_id,
        business_ref_type=business_ref_type or record.business_ref_type,
        business_ref_id=business_ref_id or record.business_ref_id,
        summary=summary if summary is not None else record.summary,
        error=error,
        output_json=output_json if output_json is not None else record.output_json,
        updated_at_ms=_now_ms(),
    )


def _normalize_evidence(
    evidence: RuntimeEvidence | list[RuntimeEvidence] | None,
    record: RuntimeTaskRecord | None,
) -> list[RuntimeEvidence]:
    if evidence is None:
        return []
    items = evidence if isinstance(evidence, list) else [evidence]
    normalized: list[RuntimeEvidence] = []
    for item in items:
        if not isinstance(item, RuntimeEvidence):
            continue
        normalized.append(
            replace(
                item,
                task_id=item.task_id or (record.task_id if record else None),
                trace_id=item.trace_id or (record.trace_id if record else None),
                tenant_key=item.tenant_key or (record.tenant_key if record else None),
                session_key=item.session_key or (record.session_key if record else None),
                business_ref_type=item.business_ref_type
                or (record.business_ref_type if record else None),
                business_ref_id=item.business_ref_id
                or (record.business_ref_id if record else None),
            )
        )
    return normalized


def _last_evidence(
    evidence: RuntimeEvidence | list[RuntimeEvidence] | None,
) -> RuntimeEvidence | None:
    if isinstance(evidence, list):
        return evidence[-1] if evidence else None
    return evidence


def _evidence_ref_type(evidence: RuntimeEvidence | None) -> str | None:
    return evidence.business_ref_type if evidence else None


def _evidence_ref_id(evidence: RuntimeEvidence | None) -> str | None:
    return evidence.business_ref_id if evidence else None


def _optional_str(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)
