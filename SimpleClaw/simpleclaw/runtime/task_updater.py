"""RuntimeTaskUpdater — runtime task 状态更新门面。

Worker、callback、event consumer、poller 都可以调用这个门面，把外部
事实标准化写入 RuntimeTaskStore。Updater 不执行业务判断，也不决定下一步
workflow，只负责记录状态事实。
"""

from __future__ import annotations

import inspect
from typing import Any

from loguru import logger

from simpleclaw.runtime.task_protocol import (
    RuntimeEvidence,
    RuntimeTaskRecord,
    TaskEnvelope,
    TaskExecutionResult,
)
from simpleclaw.runtime.task_state import RuntimeTaskStore


class RuntimeTaskUpdater:
    """Thin status updater around RuntimeTaskStore."""

    def __init__(self, store: RuntimeTaskStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> RuntimeTaskStore | None:
        return self._store

    def set_store(self, store: RuntimeTaskStore | None) -> None:
        self._store = store

    async def record_queued(
        self,
        task: TaskEnvelope,
        *,
        queue_message_id: str | None = None,
        tool_name: str | None = None,
        summary: str | None = None,
    ) -> RuntimeTaskRecord | None:
        return await self._call_store(
            "record_queued",
            task,
            queue_message_id=queue_message_id,
            tool_name=tool_name,
            summary=summary,
        )

    async def attach_queue_message_id(
        self,
        task: TaskEnvelope | str,
        queue_message_id: str,
    ) -> RuntimeTaskRecord | None:
        return await self._call_store(
            "attach_queue_message_id",
            task,
            queue_message_id,
        )

    async def record_queued_required(
        self,
        task: TaskEnvelope,
        *,
        queue_message_id: str | None = None,
        tool_name: str | None = None,
        summary: str | None = None,
    ) -> RuntimeTaskRecord | None:
        return await self._call_store_required(
            "record_queued",
            task,
            queue_message_id=queue_message_id,
            tool_name=tool_name,
            summary=summary,
        )

    async def mark_running(
        self,
        task: TaskEnvelope | str,
        *,
        claimed_by: str | None = None,
        summary: str | None = None,
    ) -> RuntimeTaskRecord | None:
        return await self._call_store(
            "mark_running",
            task,
            claimed_by=claimed_by,
            summary=summary,
        )

    async def mark_wait_external(
        self,
        task: TaskEnvelope | str,
        *,
        external_job_id: str | None = None,
        summary: str | None = None,
        evidence: RuntimeEvidence | list[RuntimeEvidence] | None = None,
    ) -> RuntimeTaskRecord | None:
        return await self._call_store(
            "mark_wait_external",
            task,
            external_job_id=external_job_id,
            summary=summary,
            evidence=evidence,
        )

    async def mark_waiting_external(
        self,
        task: TaskEnvelope | str,
        *,
        external_job_id: str | None = None,
        summary: str | None = None,
        evidence: RuntimeEvidence | list[RuntimeEvidence] | None = None,
    ) -> RuntimeTaskRecord | None:
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
    ) -> RuntimeTaskRecord | None:
        return await self._call_store(
            "mark_succeeded",
            task,
            summary=summary,
            business_ref_type=business_ref_type,
            business_ref_id=business_ref_id,
            output_json=output_json,
            evidence=evidence,
        )

    async def mark_failed(
        self,
        task: TaskEnvelope | str,
        error: str,
        *,
        claimed_by: str | None = None,
        summary: str | None = None,
        evidence: RuntimeEvidence | list[RuntimeEvidence] | None = None,
    ) -> RuntimeTaskRecord | None:
        return await self._call_store(
            "mark_failed",
            task,
            error,
            claimed_by=claimed_by,
            summary=summary,
            evidence=evidence,
        )

    async def mark_finished(
        self,
        task: TaskEnvelope,
        result: TaskExecutionResult,
    ) -> RuntimeTaskRecord | None:
        return await self._call_store("mark_finished", task, result)

    async def _call_store(
        self,
        method_name: str,
        *args: Any,
        **kwargs: Any,
    ) -> RuntimeTaskRecord | None:
        if self._store is None:
            return None
        method = getattr(self._store, method_name, None)
        if method is None:
            return None
        compatible_kwargs = _compatible_kwargs(method, kwargs)
        try:
            return await method(*args, **compatible_kwargs)
        except Exception as exc:
            logger.warning("RuntimeTaskUpdater.{} failed: {}", method_name, exc)
            return None

    async def _call_store_required(
        self,
        method_name: str,
        *args: Any,
        **kwargs: Any,
    ) -> RuntimeTaskRecord | None:
        if self._store is None:
            return None
        method = getattr(self._store, method_name, None)
        if method is None:
            raise AttributeError(f"RuntimeTaskStore missing {method_name}")
        compatible_kwargs = _compatible_kwargs(method, kwargs)
        return await method(*args, **compatible_kwargs)


def _compatible_kwargs(method: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    if not kwargs:
        return kwargs
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return kwargs
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
        return kwargs
    allowed = {
        name
        for name, param in signature.parameters.items()
        if param.kind in {
            inspect.Parameter.KEYWORD_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        }
    }
    return {key: value for key, value in kwargs.items() if key in allowed}
