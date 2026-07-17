"""Tests for subagent post-turn media propagation."""

from __future__ import annotations

import sys
import types
import unittest

try:
    import loguru  # noqa: F401
except ModuleNotFoundError:
    sys.modules.setdefault("loguru", types.SimpleNamespace(logger=types.SimpleNamespace(
        info=lambda *_, **__: None,
        debug=lambda *_, **__: None,
        warning=lambda *_, **__: None,
        error=lambda *_, **__: None,
    )))

try:
    import aiomysql  # noqa: F401
except ModuleNotFoundError:
    sys.modules.setdefault(
        "aiomysql",
        types.SimpleNamespace(
            create_pool=None,
            Pool=object,
            pool=types.SimpleNamespace(_PoolConnectionContextManager=object),
        ),
    )

from simpleclaw.context.builder import ContextBuilder
from simpleclaw.harness.hooks import TurnContext
from simpleclaw.runtime.task_protocol import TaskEnvelope
from simpleclaw.subagent.base import SubagentBase
from simpleclaw.tools.registry import ToolRegistry
from Mojing.runtime.executors import make_subagent_dispatch_executor
from Mojing.runtime.streams import MojingTaskStream
from Mojing.runtime.task_types import MojingTaskType
from Mojing.storage.subagent_store import SubagentStore


class _FakeRuntime:
    def __init__(self) -> None:
        self.tasks = []

    async def submit_task(self, task):
        self.tasks.append(task)
        return f"q-{len(self.tasks)}"


class _FakeSubagent(SubagentBase):
    name = "fake_subagent"

    def session_key_for(self, tenant_key: str) -> str:
        return f"fake:{tenant_key}"

    def matches(self, session_key: str) -> bool:
        return session_key.startswith("fake:")

    async def make_context_builder(self, tenant_key: str) -> ContextBuilder:
        return ContextBuilder([], tenant_key=tenant_key)

    def make_tool_registry(self, tenant_key: str) -> ToolRegistry:
        del tenant_key
        return ToolRegistry()

    def make_postprocess_hook(self):
        return object()

    def make_cold_path_hook(self):
        return object()


class _FakeNamedSubagent(_FakeSubagent):
    def __init__(self, *, name: str, prefix: str) -> None:
        self.name = name
        self._prefix = prefix

    def session_key_for(self, tenant_key: str) -> str:
        return f"{self._prefix}:{tenant_key}"

    def matches(self, session_key: str) -> bool:
        return session_key.startswith(f"{self._prefix}:")


class _FakeSubagentRuntimeRepo:
    def __init__(self) -> None:
        self.created = []
        self.statuses = []
        self.completed = []

    async def create_run(self, request, *, runtime_task_id=None):
        self.created.append((request, runtime_task_id))

    async def mark_run_status(self, run_id, status, **kwargs):
        self.statuses.append((run_id, status, kwargs))

    async def complete_run(self, result):
        self.completed.append(result)


class _FakeDispatchStore:
    def __init__(self, subagent=None) -> None:
        self.subagent = subagent or _FakeSubagent()
        self.calls = []

    def find_subagent(self, session_key: str):
        if self.subagent.matches(session_key):
            return self.subagent
        return None

    async def run_turn(self, **kwargs):
        self.calls.append(kwargs)
        return "ok"


class SubagentMediaPayloadTest(unittest.IsolatedAsyncioTestCase):
    async def test_post_turn_tasks_include_original_media(self) -> None:
        runtime = _FakeRuntime()
        subagent = _FakeSubagent()
        store = SubagentStore(
            llm=None,  # type: ignore[arg-type]
            subagents=[subagent],
            session_repo=None,  # type: ignore[arg-type]
            session_store=None,  # type: ignore[arg-type]
            postprocess_runtime=runtime,  # type: ignore[arg-type]
        )

        await store._enqueue_post_turn_tasks(  # noqa: SLF001 - targeted regression test
            subagent,
            TurnContext(
                tenant_key="tenant-1",
                session_key="fake:tenant-1",
                user_message="我重拍了",
                assistant_reply="收到新照片",
                media=["image-url-1"],
            ),
        )

        self.assertEqual(len(runtime.tasks), 1)
        for task in runtime.tasks:
            self.assertEqual(task.payload["media"], ["image-url-1"])
        self.assertEqual(runtime.tasks[0].task_type, "fake_subagent_postprocess")

    async def test_run_turn_records_session_ingress_subagent_run(self) -> None:
        runtime_repo = _FakeSubagentRuntimeRepo()
        subagent = _FakeSubagent()
        store = SubagentStore(
            llm=None,  # type: ignore[arg-type]
            subagents=[subagent],
            session_repo=None,  # type: ignore[arg-type]
            session_store=None,  # type: ignore[arg-type]
            subagent_runtime_repo=runtime_repo,
        )

        async def fake_impl(**_kwargs):
            return "子 agent 回复"

        store._run_turn_impl = fake_impl  # type: ignore[method-assign]

        reply = await store.run_turn(
            session_key="fake:tenant-1",
            tenant_key="tenant-1",
            message="看看今天的肌肤日记",
            message_id="msg-1",
            ingress_id="ing-1",
        )

        self.assertEqual(reply, "子 agent 回复")
        request, runtime_task_id = runtime_repo.created[0]
        self.assertIsNone(runtime_task_id)
        self.assertEqual(request.owner_type, "session_ingress")
        self.assertEqual(request.owner_id, "ing-1")
        self.assertEqual(request.run_mode, "chat")
        self.assertEqual(request.subagent_name, "fake_subagent")
        self.assertEqual(runtime_repo.statuses[0][1], "running")
        self.assertEqual(runtime_repo.completed[0].status, "completed")
        self.assertEqual(runtime_repo.completed[0].reply_text, "子 agent 回复")

    async def test_subagent_dispatch_executor_owns_run_by_runtime_task(self) -> None:
        store = _FakeDispatchStore()
        execute = make_subagent_dispatch_executor(store)  # type: ignore[arg-type]
        task = TaskEnvelope(
            task_type=MojingTaskType.SUBAGENT_DISPATCH,
            stream=MojingTaskStream.SUBAGENT_DISPATCH,
            tenant_key="tenant-1",
            session_key="fake:tenant-1",
            task_id="task-dispatch-1",
            trace_id="trace-1",
            payload={
                "tenant_key": "tenant-1",
                "session_key": "fake:tenant-1",
                "message": "帮我看一下肌肤日记",
                "origin_session_key": "main:tenant-1",
                "handoff_contract": {"kind": "skin_diary", "intent": "handoff"},
                "source": "notify_skin_diary_chat",
            },
        )

        result = await execute(task)

        self.assertEqual(result.status, "succeeded")
        call = store.calls[0]
        request = call["subagent_run_request"]
        self.assertEqual(call["runtime_task_id"], "task-dispatch-1")
        self.assertEqual(request.owner_type, "runtime_task")
        self.assertEqual(request.owner_id, "task-dispatch-1")
        self.assertEqual(request.run_mode, "handoff")
        self.assertEqual(request.trace_id, "trace-1")
        self.assertEqual(request.input_refs["origin_session_key"], "main:tenant-1")

    async def test_deep_report_dispatch_executor_records_deep_report_run(self) -> None:
        store = _FakeDispatchStore(_FakeNamedSubagent(name="deep_report", prefix="deep_report"))
        execute = make_subagent_dispatch_executor(store)  # type: ignore[arg-type]
        task = TaskEnvelope(
            task_type=MojingTaskType.SUBAGENT_DISPATCH,
            stream=MojingTaskStream.SUBAGENT_DISPATCH,
            tenant_key="tenant-1",
            session_key="deep_report:tenant-1",
            task_id="task-deep-dispatch-1",
            trace_id="trace-deep-1",
            payload={
                "tenant_key": "tenant-1",
                "session_key": "deep_report:tenant-1",
                "message": "帮我生成深度分析报告",
                "origin_session_key": "main:tenant-1",
                "handoff_contract": {"kind": "deep_report", "intent": "handoff"},
                "source": "deep_report_chat",
            },
        )

        result = await execute(task)

        self.assertEqual(result.status, "succeeded")
        request = store.calls[0]["subagent_run_request"]
        self.assertEqual(request.subagent_name, "deep_report")
        self.assertEqual(request.owner_type, "runtime_task")
        self.assertEqual(request.owner_id, "task-deep-dispatch-1")
        self.assertEqual(request.run_mode, "handoff")
        self.assertEqual(request.payload["handoff_contract"]["kind"], "deep_report")


if __name__ == "__main__":
    unittest.main()
