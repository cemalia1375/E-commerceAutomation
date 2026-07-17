"""RuntimeServices — 任务入队的薄封装。

用法：
    runtime = RuntimeServices(task_queue=queue, task_state_store=task_repo)
    queue_id = await runtime.submit_task(task_envelope)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from simpleclaw.runtime.task_protocol import TaskEnvelope
from simpleclaw.runtime.task_updater import RuntimeTaskUpdater

if TYPE_CHECKING:
    from simpleclaw.runtime.task_queue import InMemoryTaskQueue, RedisTaskQueue
    from simpleclaw.runtime.task_state import RuntimeTaskStore


class RuntimeServices:
    def __init__(
        self,
        *,
        task_queue: "InMemoryTaskQueue | RedisTaskQueue",
        task_state_store: "RuntimeTaskStore | None" = None,
        action_usage_store: object | None = None,
    ) -> None:
        self._task_queue = task_queue
        self._task_state_store = task_state_store
        self._task_updater = RuntimeTaskUpdater(task_state_store)
        self._action_usage_store = action_usage_store

    @property
    def task_state_store(self) -> "RuntimeTaskStore | None":
        return self._task_state_store

    @property
    def task_updater(self) -> RuntimeTaskUpdater:
        return self._task_updater

    async def submit_task(
        self,
        task: TaskEnvelope,
        *,
        tool_name: str | None = None,
        summary: str | None = None,
    ) -> str:
        """Persist the runtime task first, then enqueue it."""
        if self._task_state_store is not None:
            try:
                await self._task_updater.record_queued_required(
                    task,
                    tool_name=tool_name,
                    summary=summary,
                )
            except Exception as exc:
                logger.error(
                    "RuntimeServices.record_queued failed before enqueue: task_id={} error={}",
                    task.task_id,
                    exc,
                )
                raise

        try:
            queue_message_id = await self._task_queue.enqueue(task)
        except Exception as exc:
            if self._task_state_store is not None:
                await self._task_updater.mark_failed(
                    task,
                    f"enqueue failed: {exc}",
                    summary="runtime task enqueue failed",
                )
            raise

        if self._task_state_store is not None:
            await self._task_updater.attach_queue_message_id(task, queue_message_id)
        await self._record_action_submitted(task)
        return queue_message_id

    async def _record_action_submitted(self, task: TaskEnvelope) -> None:
        store = self._action_usage_store
        if store is None:
            return
        tenant_key = str(task.tenant_key or task.payload.get("tenant_key") or "").strip()
        action_key = str(task.payload.get("action_key") or "").strip()
        if not tenant_key or not action_key:
            return
        try:
            await store.incr_submitted(tenant_key, action_key)
        except Exception as exc:
            logger.warning(
                "RuntimeServices.record_action_submitted failed: task_id={} tenant={} action={} err={}",
                task.task_id,
                tenant_key,
                action_key,
                exc,
            )
