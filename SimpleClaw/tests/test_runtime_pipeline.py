"""Unit tests for runtime submission and worker state tracking."""

from __future__ import annotations

import unittest

from simpleclaw.runtime.services import RuntimeServices
from simpleclaw.runtime.task_protocol import TaskEnvelope, TaskExecutionResult
from simpleclaw.runtime.task_queue import TaskMessage
from Mojing.runtime.worker import TaskWorker


class _FakeQueue:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, TaskEnvelope]] = []
        self.acked: list[tuple[str, str]] = []

    async def enqueue(self, task: TaskEnvelope) -> str:
        queue_id = f"q-{len(self.enqueued) + 1}"
        self.enqueued.append((queue_id, task))
        return queue_id

    async def ack(self, message: TaskMessage, *, consumer_group: str) -> None:
        self.acked.append((message.task.task_id, consumer_group))


class _FakeTaskStateStore:
    def __init__(self) -> None:
        self.queued: list[tuple[str, str | None, int]] = []
        self.running: list[tuple[str, str]] = []
        self.finished: list[tuple[str, str]] = []
        self.failed: list[tuple[str, str, str | None]] = []

    async def record_queued(
        self,
        task: TaskEnvelope,
        *,
        queue_message_id: str | None = None,
    ) -> None:
        self.queued.append((task.task_id, queue_message_id, task.attempt))

    async def attach_queue_message_id(
        self,
        task: TaskEnvelope,
        queue_message_id: str,
    ) -> None:
        for index in range(len(self.queued) - 1, -1, -1):
            task_id, _, attempt = self.queued[index]
            if task_id == task.task_id:
                self.queued[index] = (task_id, queue_message_id, attempt)
                return

    async def mark_running(
        self,
        task: TaskEnvelope,
        *,
        claimed_by: str,
    ) -> None:
        self.running.append((task.task_id, claimed_by))

    async def mark_finished(
        self,
        task: TaskEnvelope,
        result: TaskExecutionResult,
    ) -> None:
        self.finished.append((task.task_id, result.status))

    async def mark_failed(
        self,
        task: TaskEnvelope,
        error: str,
        *,
        claimed_by: str | None = None,
        summary: str | None = None,
    ) -> None:
        del summary
        self.failed.append((task.task_id, error, claimed_by))


class RuntimePipelineTest(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_services_records_queued_submission(self) -> None:
        queue = _FakeQueue()
        store = _FakeTaskStateStore()
        services = RuntimeServices(task_queue=queue, task_state_store=store)
        task = TaskEnvelope(
            task_type="postprocess",
            payload={"x": 1},
            stream="test_stream",
            tenant_key="tenant-1",
            session_key="session-1",
        )

        queue_id = await services.submit_task(task)

        self.assertEqual(queue_id, "q-1")
        self.assertEqual(len(queue.enqueued), 1)
        self.assertEqual(store.queued, [(task.task_id, "q-1", 0)])

    async def test_worker_marks_noop_and_acks(self) -> None:
        queue = _FakeQueue()
        store = _FakeTaskStateStore()

        async def _executor(task: TaskEnvelope) -> TaskExecutionResult:
            self.assertEqual(task.task_type, "postprocess")
            return TaskExecutionResult.noop("no document changes")

        worker = TaskWorker(
            queue,
            "test_stream",
            executors={"postprocess": _executor},
            task_state_store=store,
        )
        task = TaskEnvelope(
            task_type="postprocess",
            payload={},
            stream="test_stream",
            tenant_key="tenant-1",
            session_key="session-1",
        )
        msg = TaskMessage(stream="test_stream", queue_id="q-1", task=task)

        await worker._handle(msg)

        self.assertEqual(len(store.running), 1)
        self.assertEqual(store.finished, [(task.task_id, "noop")])
        self.assertEqual(queue.acked, [(task.task_id, "mojing")])

    async def test_worker_requeues_failed_result(self) -> None:
        queue = _FakeQueue()
        store = _FakeTaskStateStore()

        async def _executor(_task: TaskEnvelope) -> TaskExecutionResult:
            return TaskExecutionResult.failed("invalid json", summary="cold path parse failed")

        worker = TaskWorker(
            queue,
            "test_stream",
            executors={"structured_memory": _executor},
            task_state_store=store,
        )
        task = TaskEnvelope(
            task_type="structured_memory",
            payload={},
            stream="test_stream",
            tenant_key="tenant-1",
            session_key="session-1",
            max_attempts=2,
        )
        msg = TaskMessage(stream="test_stream", queue_id="q-1", task=task)

        await worker._handle(msg)

        self.assertEqual(len(store.failed), 1)
        self.assertEqual(queue.acked, [(task.task_id, "mojing")])
        self.assertEqual(len(queue.enqueued), 1)
        retry_queue_id, retry_task = queue.enqueued[0]
        self.assertEqual(retry_queue_id, "q-1")
        self.assertEqual(retry_task.task_id, task.task_id)
        self.assertEqual(retry_task.attempt, 1)
        self.assertEqual(store.queued[-1], (task.task_id, "q-1", 1))


if __name__ == "__main__":
    unittest.main()
