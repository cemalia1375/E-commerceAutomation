"""TaskWorker — 后台任务消费循环。

每个 Worker 绑定一个 stream，循环消费 + 分发 + ack。
失败时最多重试 max_attempts 次，超限写入 dead-letter stream。

用法（在 server.py 启动时）：
    postprocess_worker = TaskWorker(
        task_queue, "postprocess",
        consumer_group="mojing",
        executors={
            "postprocess":        execute_postprocess,
            "structured_memory":  execute_structured_memory,
        },
    )
    background_worker = TaskWorker(
        task_queue, "background",
        consumer_group="mojing",
        executors={
            "deep_research":   execute_deep_research,
            "subagent_dispatch": execute_subagent_dispatch,
        },
    )
    asyncio.create_task(postprocess_worker.run())
    asyncio.create_task(background_worker.run())
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from loguru import logger

from simpleclaw.runtime.task_protocol import (
    DEAD_LETTER_STREAM,
    TaskEnvelope,
    TaskExecutionResult,
    TaskStream,
    make_consumer_name,
)
from simpleclaw.runtime.task_queue import InMemoryTaskQueue, RedisTaskQueue
from simpleclaw.runtime.scope_lock import ScopeLockRegistry
from simpleclaw.runtime.task_state import TaskStateStore

TaskExecutor = Callable[[TaskEnvelope], Awaitable[TaskExecutionResult | None]]
FinalFailureHandler = Callable[[TaskEnvelope, str], Awaitable[None]]


class TaskWorker:
    """单 stream 消费 worker。"""

    def __init__(
        self,
        task_queue: InMemoryTaskQueue | RedisTaskQueue,
        stream: TaskStream,
        *,
        consumer_group: str = "mojing",
        executors: dict[str, TaskExecutor],
        batch_size: int = 4,
        task_state_store: TaskStateStore | None = None,
        scope_locks: ScopeLockRegistry | None = None,
        action_usage_store: object | None = None,
        final_failure_handler: FinalFailureHandler | None = None,
    ) -> None:
        self._queue = task_queue
        self._stream = stream
        self._group = consumer_group
        self._name = make_consumer_name(stream)
        self._executors = executors
        self._batch_size = batch_size
        self._task_state_store = task_state_store
        self._scope_locks = scope_locks
        self._action_usage_store = action_usage_store
        self._final_failure_handler = final_failure_handler
        self._running = False

    @property
    def stream(self) -> TaskStream:
        return self._stream

    @property
    def consumer_name(self) -> str:
        return self._name

    async def run(self) -> None:
        """主循环：持续消费直到 stop() 被调用。"""
        self._running = True
        logger.info(
            "TaskWorker started: stream={} consumer={} executors={}",
            self._stream, self._name, list(self._executors),
        )
        while self._running:
            try:
                messages = await self._queue.consume(
                    self._stream,
                    consumer_group=self._group,
                    consumer_name=self._name,
                    count=self._batch_size,
                )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("TaskWorker.consume error: stream={} err={}", self._stream, exc)
                await asyncio.sleep(1)
                continue

            for msg in messages:
                await self._handle(msg)

    def stop(self) -> None:
        self._running = False

    async def _handle(self, msg) -> None:
        task = msg.task
        executor = self._executors.get(task.task_type)

        if executor is None:
            logger.warning(
                "TaskWorker: no executor for task_type={}, acking and skipping",
                task.task_type,
            )
            await self._mark_failed(task, f"missing executor for task_type={task.task_type}")
            await self._ack(msg)
            return

        if self._scope_locks is not None and task.scope_key:
            logger.debug(
                "TaskWorker waiting scope lock: stream={} type={} task_id={} scope_key={}",
                self._stream, task.task_type, task.task_id, task.scope_key,
            )
            async with self._scope_locks.hold(task.scope_key):
                await self._run_task(msg, task, executor)
            return

        await self._run_task(msg, task, executor)

    async def _run_task(self, msg, task: TaskEnvelope, executor: TaskExecutor) -> None:
        await self._mark_running(task)

        try:
            result = await executor(task)
            if result is None:
                result = TaskExecutionResult.succeeded(summary="completed")
            if result.status == "failed":
                await self._handle_failure(msg, RuntimeError(result.error or result.summary))
                return

            await self._mark_finished(task, result)
            await self._record_action_succeeded(task, result)
            await self._ack(msg)
            logger.info(
                "TaskWorker {}: type={} task_id={} tenant={} session_key={} scope_key={} summary={}",
                result.status,
                task.task_type,
                task.task_id,
                task.tenant_key,
                task.session_key,
                task.scope_key or "(none)",
                result.summary or "(none)",
            )
        except Exception as exc:
            await self._handle_failure(msg, exc)

    async def _ack(self, msg) -> None:
        try:
            await self._queue.ack(msg, consumer_group=self._group)
        except Exception as exc:
            logger.warning("TaskWorker.ack failed: {}", exc)

    async def _handle_failure(self, msg, exc: Exception) -> None:
        task = msg.task
        next_attempt = task.attempt + 1
        error_text = str(exc) or exc.__class__.__name__
        logger.warning(
            "TaskWorker failed: type={} task_id={} tenant={} session_key={} attempt={}/{} error={}",
            task.task_type, task.task_id, task.tenant_key, task.session_key, next_attempt, task.max_attempts, error_text,
        )

        await self._mark_failed(task, error_text)
        await self._ack(msg)   # 先 ack，避免永久卡住

        if next_attempt < task.max_attempts:
            # 重新入队，attempt +1
            retry = TaskEnvelope(
                task_type=task.task_type,
                payload=task.payload,
                stream=task.stream,
                tenant_key=task.tenant_key,
                session_key=task.session_key,
                scope_key=task.scope_key,
                trace_id=task.trace_id,
                task_id=task.task_id,
                attempt=next_attempt,
                max_attempts=task.max_attempts,
                service_role=task.service_role,
            )
            try:
                queue_id = await self._queue.enqueue(retry)
                if self._task_state_store is not None:
                    await self._task_state_store.record_queued(
                        retry,
                        queue_message_id=queue_id,
                    )
                logger.info(
                    "TaskWorker retry enqueued: type={} task_id={} attempt={}",
                    task.task_type, task.task_id, next_attempt,
                )
            except Exception as enq_exc:
                logger.error("TaskWorker retry enqueue failed: {}", enq_exc)
        else:
            await self._record_action_failed(task)
            await self._mark_failed(task, error_text, summary="final_failure")
            await self._notify_final_failure(task, error_text)
            # 超限，写 dead-letter
            dead = TaskEnvelope(
                task_type="dead_letter",
                payload={"original_task": task.to_dict(), "error": str(exc)},
                stream=DEAD_LETTER_STREAM,
                tenant_key=task.tenant_key,
                session_key=task.session_key,
                scope_key=task.scope_key,
                trace_id=task.trace_id,
                task_id=task.task_id,
                attempt=next_attempt,
                max_attempts=task.max_attempts,
                service_role=task.service_role,
            )
            try:
                await self._queue.enqueue(dead)
                logger.error(
                    "TaskWorker dead-letter: type={} task_id={} error={}",
                    task.task_type, task.task_id, exc,
                )
            except Exception as dl_exc:
                logger.error("TaskWorker dead-letter enqueue failed: {}", dl_exc)

    async def _mark_running(self, task: TaskEnvelope) -> None:
        if self._task_state_store is None:
            return
        try:
            await self._task_state_store.mark_running(task, claimed_by=self._name)
        except Exception as exc:
            logger.warning("TaskWorker.mark_running failed: {}", exc)

    async def _mark_finished(self, task: TaskEnvelope, result: TaskExecutionResult) -> None:
        if self._task_state_store is None:
            return
        try:
            await self._task_state_store.mark_finished(task, result)
        except Exception as exc:
            logger.warning("TaskWorker.mark_finished failed: {}", exc)

    async def _mark_failed(self, task: TaskEnvelope, error: str, *, summary: str | None = None) -> None:
        if self._task_state_store is None:
            return
        try:
            await self._task_state_store.mark_failed(
                task,
                error,
                claimed_by=self._name,
                summary=summary,
            )
        except Exception as exc:
            logger.warning("TaskWorker.mark_failed failed: {}", exc)

    async def _record_action_succeeded(self, task: TaskEnvelope, result: TaskExecutionResult) -> None:
        if result.status != "succeeded":
            return
        store = self._action_usage_store
        if store is None:
            return
        tenant_key = str(task.tenant_key or task.payload.get("tenant_key") or "").strip()
        action_key = str(task.payload.get("action_key") or "").strip()
        if not tenant_key or not action_key:
            return
        try:
            await store.incr_succeeded(tenant_key, action_key)
        except Exception as exc:
            logger.warning(
                "TaskWorker.record_action_succeeded failed: task_id={} tenant={} action={} err={}",
                task.task_id,
                tenant_key,
                action_key,
                exc,
            )

    async def _record_action_failed(self, task: TaskEnvelope) -> None:
        store = self._action_usage_store
        if store is None:
            return
        tenant_key = str(task.tenant_key or task.payload.get("tenant_key") or "").strip()
        action_key = str(task.payload.get("action_key") or "").strip()
        if not tenant_key or not action_key:
            return
        try:
            await store.incr_failed(tenant_key, action_key)
        except Exception as exc:
            logger.warning(
                "TaskWorker.record_action_failed failed: task_id={} tenant={} action={} err={}",
                task.task_id,
                tenant_key,
                action_key,
                exc,
            )

    async def _notify_final_failure(self, task: TaskEnvelope, error: str) -> None:
        if self._final_failure_handler is None:
            return
        try:
            await self._final_failure_handler(task, error)
        except Exception as exc:
            logger.warning(
                "TaskWorker.final_failure_handler failed: type={} task_id={} err={}",
                task.task_type,
                task.task_id,
                exc,
            )
