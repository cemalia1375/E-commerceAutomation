"""Unit tests for runtime task submission and worker status flow."""

from __future__ import annotations

import sys
import types
import unittest

sys.modules.setdefault("loguru", types.SimpleNamespace(logger=types.SimpleNamespace(
    info=lambda *_, **__: None,
    debug=lambda *_, **__: None,
    warning=lambda *_, **__: None,
    error=lambda *_, **__: None,
)))

from Mojing.runtime.worker import TaskWorker
from simpleclaw.runtime.services import RuntimeServices
from simpleclaw.runtime.task_protocol import TaskEnvelope, TaskExecutionResult
from simpleclaw.runtime.task_queue import InMemoryTaskQueue, RedisTaskQueue


class _RecordingTaskStateStore:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def record_queued(
        self,
        task: TaskEnvelope,
        *,
        queue_message_id: str | None = None,
    ) -> None:
        self.events.append({
            "kind": "queued",
            "task_id": task.task_id,
            "attempt": task.attempt,
            "queue_message_id": queue_message_id,
        })

    async def attach_queue_message_id(
        self,
        task: TaskEnvelope,
        queue_message_id: str,
    ) -> None:
        for event in reversed(self.events):
            if event["kind"] == "queued" and event["task_id"] == task.task_id:
                event["queue_message_id"] = queue_message_id
                return

    async def mark_running(
        self,
        task: TaskEnvelope,
        *,
        claimed_by: str,
    ) -> None:
        self.events.append({
            "kind": "running",
            "task_id": task.task_id,
            "attempt": task.attempt,
            "claimed_by": claimed_by,
        })

    async def mark_finished(
        self,
        task: TaskEnvelope,
        result: TaskExecutionResult,
    ) -> None:
        self.events.append({
            "kind": result.status,
            "task_id": task.task_id,
            "attempt": task.attempt,
            "summary": result.summary,
        })

    async def mark_failed(
        self,
        task: TaskEnvelope,
        error: str,
        *,
        claimed_by: str | None = None,
        summary: str | None = None,
    ) -> None:
        self.events.append({
            "kind": "failed",
            "task_id": task.task_id,
            "attempt": task.attempt,
            "claimed_by": claimed_by,
            "summary": summary,
            "error": error,
        })


class _RecordingActionUsageStore:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, str]] = []

    async def incr_submitted(self, tenant_key: str, action_key: str) -> None:
        self.events.append(("submitted", tenant_key, action_key))

    async def incr_succeeded(self, tenant_key: str, action_key: str) -> None:
        self.events.append(("succeeded", tenant_key, action_key))

    async def incr_failed(self, tenant_key: str, action_key: str) -> None:
        self.events.append(("failed", tenant_key, action_key))


class _FailingQueue(InMemoryTaskQueue):
    async def enqueue(self, task: TaskEnvelope) -> str:
        del task
        raise RuntimeError("redis unavailable")


class _FakeRedisClient:
    def __init__(self, *, autoclaim_response=None, readgroup_response=None) -> None:
        self.autoclaim_response = autoclaim_response
        self.readgroup_response = readgroup_response
        self.group_calls: list[dict] = []
        self.autoclaim_calls: list[dict] = []
        self.readgroup_calls: list[dict] = []

    async def xgroup_create(self, **kwargs):
        self.group_calls.append(kwargs)
        return True

    async def xautoclaim(self, **kwargs):
        self.autoclaim_calls.append(kwargs)
        return self.autoclaim_response

    async def xreadgroup(self, **kwargs):
        self.readgroup_calls.append(kwargs)
        return self.readgroup_response


class RuntimeServicesTest(unittest.IsolatedAsyncioTestCase):
    async def test_submit_task_records_queued_state(self) -> None:
        queue = InMemoryTaskQueue()
        state_store = _RecordingTaskStateStore()
        action_store = _RecordingActionUsageStore()
        runtime = RuntimeServices(
            task_queue=queue,
            task_state_store=state_store,
            action_usage_store=action_store,
        )
        task = TaskEnvelope(
            task_type="postprocess",
            payload={"tenant_key": "tenant-1", "action_key": "skin_diary.handoff"},
            stream="test_stream",
            tenant_key="tenant-1",
            session_key="session-1",
            service_role="test",
        )

        queue_id = await runtime.submit_task(task)
        consumed = await queue.consume(
            "test_stream",
            consumer_group="test",
            consumer_name="tester",
        )

        self.assertEqual(queue_id, "mem-1")
        self.assertEqual(len(consumed), 1)
        self.assertEqual(consumed[0].task.task_id, task.task_id)
        self.assertEqual(state_store.events, [{
            "kind": "queued",
            "task_id": task.task_id,
            "attempt": 0,
            "queue_message_id": "mem-1",
        }])
        self.assertEqual(action_store.events, [("submitted", "tenant-1", "skin_diary.handoff")])

    async def test_submit_task_marks_failed_when_enqueue_fails(self) -> None:
        state_store = _RecordingTaskStateStore()
        runtime = RuntimeServices(
            task_queue=_FailingQueue(),
            task_state_store=state_store,
        )
        task = TaskEnvelope(
            task_type="postprocess",
            payload={"tenant_key": "tenant-1"},
            stream="test_stream",
            tenant_key="tenant-1",
            session_key="session-1",
            service_role="test",
        )

        with self.assertRaises(RuntimeError):
            await runtime.submit_task(task)

        self.assertEqual([event["kind"] for event in state_store.events], ["queued", "failed"])
        self.assertEqual(state_store.events[-1]["summary"], "runtime task enqueue failed")


class RedisTaskQueueReclaimTest(unittest.IsolatedAsyncioTestCase):
    async def test_socket_timeout_exceeds_block_wait_to_avoid_empty_poll_errors(self) -> None:
        queue = RedisTaskQueue(url="redis://example/0", stream_prefix="test", block_ms=5000)

        self.assertGreater(queue._socket_timeout_s * 1000, queue._block_ms)
        self.assertGreaterEqual(queue._socket_timeout_s, 10.0)

    async def test_consume_reclaims_stale_pending_before_reading_new_messages(self) -> None:
        task = TaskEnvelope(
            task_type="postprocess",
            payload={"tenant_key": "tenant-1"},
            stream="test_stream",
            tenant_key="tenant-1",
            session_key="session-1",
        )
        client = _FakeRedisClient(
            autoclaim_response=(
                "0-0",
                [("1700000000000-0", {"payload": task.to_json()})],
                [],
            ),
            readgroup_response=[],
        )
        queue = RedisTaskQueue(
            url="redis://example/0",
            stream_prefix="test",
            claim_min_idle_ms=1234,
        )
        queue._client = client

        messages = await queue.consume(
            "test_stream",
            consumer_group="test",
            consumer_name="tester",
        )

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].task.task_id, task.task_id)
        self.assertEqual(len(client.autoclaim_calls), 1)
        self.assertEqual(client.autoclaim_calls[0]["min_idle_time"], 1234)
        self.assertEqual(client.readgroup_calls, [])

    async def test_consume_reads_new_messages_when_no_stale_pending_found(self) -> None:
        task = TaskEnvelope(
            task_type="postprocess",
            payload={"tenant_key": "tenant-1"},
            stream="test_stream",
            tenant_key="tenant-1",
            session_key="session-1",
        )
        client = _FakeRedisClient(
            autoclaim_response=("0-0", [], []),
            readgroup_response=[
                ("test:test_stream", [("1700000000001-0", {"payload": task.to_json()})]),
            ],
        )
        queue = RedisTaskQueue(url="redis://example/0", stream_prefix="test")
        queue._client = client

        messages = await queue.consume(
            "test_stream",
            consumer_group="test",
            consumer_name="tester",
        )

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].task.task_id, task.task_id)
        self.assertEqual(len(client.autoclaim_calls), 1)
        self.assertEqual(len(client.readgroup_calls), 1)


class TaskWorkerStatusFlowTest(unittest.IsolatedAsyncioTestCase):
    async def test_worker_marks_noop_as_terminal_status(self) -> None:
        queue = InMemoryTaskQueue()
        state_store = _RecordingTaskStateStore()
        action_store = _RecordingActionUsageStore()
        runtime = RuntimeServices(
            task_queue=queue,
            task_state_store=state_store,
            action_usage_store=action_store,
        )
        task = TaskEnvelope(
            task_type="structured_memory",
            payload={"tenant_key": "tenant-1", "action_key": "skin_diary.handoff"},
            stream="test_stream",
            tenant_key="tenant-1",
            session_key="session-1",
        )
        await runtime.submit_task(task)
        [message] = await queue.consume(
            "test_stream",
            consumer_group="test",
            consumer_name="tester",
        )

        async def _executor(_: TaskEnvelope) -> TaskExecutionResult:
            return TaskExecutionResult.noop("no state changes")

        worker = TaskWorker(
            queue,
            "test_stream",
            consumer_group="test",
            executors={"structured_memory": _executor},
            task_state_store=state_store,
            action_usage_store=action_store,
        )
        await worker._handle(message)

        self.assertEqual(
            [event["kind"] for event in state_store.events],
            ["queued", "running", "noop"],
        )
        self.assertEqual(state_store.events[-1]["summary"], "no state changes")
        self.assertEqual(action_store.events, [("submitted", "tenant-1", "skin_diary.handoff")])

    async def test_worker_marks_wait_external_for_async_business_task(self) -> None:
        queue = InMemoryTaskQueue()
        state_store = _RecordingTaskStateStore()
        action_store = _RecordingActionUsageStore()
        runtime = RuntimeServices(
            task_queue=queue,
            task_state_store=state_store,
            action_usage_store=action_store,
        )
        task = TaskEnvelope(
            task_type="image_analysis",
            payload={"tenant_key": "tenant-1", "action_key": "skin_diary.handoff"},
            stream="test_stream",
            tenant_key="tenant-1",
            session_key="session-1",
        )
        await runtime.submit_task(task)
        [message] = await queue.consume(
            "test_stream",
            consumer_group="test",
            consumer_name="tester",
        )

        async def _executor(_: TaskEnvelope) -> TaskExecutionResult:
            return TaskExecutionResult.wait_external("accepted by upstream")

        worker = TaskWorker(
            queue,
            "test_stream",
            consumer_group="test",
            executors={"image_analysis": _executor},
            task_state_store=state_store,
            action_usage_store=action_store,
        )
        await worker._handle(message)

        self.assertEqual(
            [event["kind"] for event in state_store.events],
            ["queued", "running", "wait_external"],
        )
        self.assertEqual(state_store.events[-1]["summary"], "accepted by upstream")
        self.assertEqual(action_store.events, [("submitted", "tenant-1", "skin_diary.handoff")])

    async def test_worker_requeues_failed_result_with_same_task_id(self) -> None:
        queue = InMemoryTaskQueue()
        state_store = _RecordingTaskStateStore()
        action_store = _RecordingActionUsageStore()
        runtime = RuntimeServices(
            task_queue=queue,
            task_state_store=state_store,
            action_usage_store=action_store,
        )
        task = TaskEnvelope(
            task_type="postprocess",
            payload={"tenant_key": "tenant-1", "action_key": "skin_diary.handoff"},
            stream="test_stream",
            tenant_key="tenant-1",
            session_key="session-1",
            max_attempts=2,
        )
        await runtime.submit_task(task)
        [message] = await queue.consume(
            "test_stream",
            consumer_group="test",
            consumer_name="tester",
        )

        async def _executor(_: TaskEnvelope) -> TaskExecutionResult:
            return TaskExecutionResult.failed("boom", summary="hook failed")

        worker = TaskWorker(
            queue,
            "test_stream",
            consumer_group="test",
            executors={"postprocess": _executor},
            task_state_store=state_store,
            action_usage_store=action_store,
        )
        await worker._handle(message)
        [retried] = await queue.consume(
            "test_stream",
            consumer_group="test",
            consumer_name="tester",
        )

        self.assertEqual(retried.task.task_id, task.task_id)
        self.assertEqual(retried.task.attempt, 1)
        self.assertEqual(
            [event["kind"] for event in state_store.events],
            ["queued", "running", "failed", "queued"],
        )
        self.assertEqual(action_store.events, [("submitted", "tenant-1", "skin_diary.handoff")])

    async def test_worker_records_terminal_success_for_action_key(self) -> None:
        queue = InMemoryTaskQueue()
        state_store = _RecordingTaskStateStore()
        action_store = _RecordingActionUsageStore()
        runtime = RuntimeServices(
            task_queue=queue,
            task_state_store=state_store,
            action_usage_store=action_store,
        )
        task = TaskEnvelope(
            task_type="subagent_dispatch",
            payload={"tenant_key": "tenant-1", "action_key": "skin_diary.handoff"},
            stream="test_stream",
            tenant_key="tenant-1",
            session_key="session-1",
        )
        await runtime.submit_task(task)
        [message] = await queue.consume(
            "test_stream",
            consumer_group="test",
            consumer_name="tester",
        )

        async def _executor(_: TaskEnvelope) -> TaskExecutionResult:
            return TaskExecutionResult.succeeded(summary="handoff completed")

        worker = TaskWorker(
            queue,
            "test_stream",
            consumer_group="test",
            executors={"subagent_dispatch": _executor},
            task_state_store=state_store,
            action_usage_store=action_store,
        )
        await worker._handle(message)

        self.assertEqual(
            action_store.events,
            [
                ("submitted", "tenant-1", "skin_diary.handoff"),
                ("succeeded", "tenant-1", "skin_diary.handoff"),
            ],
        )

    async def test_worker_records_terminal_failure_for_action_key(self) -> None:
        queue = InMemoryTaskQueue()
        state_store = _RecordingTaskStateStore()
        action_store = _RecordingActionUsageStore()
        runtime = RuntimeServices(
            task_queue=queue,
            task_state_store=state_store,
            action_usage_store=action_store,
        )
        task = TaskEnvelope(
            task_type="subagent_dispatch",
            payload={"tenant_key": "tenant-1", "action_key": "skin_diary.handoff"},
            stream="test_stream",
            tenant_key="tenant-1",
            session_key="session-1",
            max_attempts=1,
        )
        await runtime.submit_task(task)
        [message] = await queue.consume(
            "test_stream",
            consumer_group="test",
            consumer_name="tester",
        )

        async def _executor(_: TaskEnvelope) -> TaskExecutionResult:
            return TaskExecutionResult.failed("boom", summary="handoff failed")

        worker = TaskWorker(
            queue,
            "test_stream",
            consumer_group="test",
            executors={"subagent_dispatch": _executor},
            task_state_store=state_store,
            action_usage_store=action_store,
        )
        await worker._handle(message)

        self.assertEqual(
            action_store.events,
            [
                ("submitted", "tenant-1", "skin_diary.handoff"),
                ("failed", "tenant-1", "skin_diary.handoff"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
