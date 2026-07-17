from __future__ import annotations

import unittest

from simpleclaw.harness.hooks import TurnContext
from simpleclaw.runtime.task_protocol import TaskEnvelope, TaskExecutionResult
from Mojing.runtime.executors import make_obligation_extract_executor


class _Hook:
    def __init__(self) -> None:
        self.contexts: list[TurnContext] = []

    async def on_turn_end(self, ctx: TurnContext) -> TaskExecutionResult:
        self.contexts.append(ctx)
        return TaskExecutionResult.succeeded("obligation extracted")


class _RuntimeTaskRepo:
    async def find_latest_task_for(self, *, tenant_key: str, task_type: str):
        self.latest_lookup = {"tenant_key": tenant_key, "task_type": task_type}
        return {
            "task_id": "image-task-1",
            "task_type": "image_analysis",
            "tenant_key": tenant_key,
            "session_key": f"main:{tenant_key}",
            "status": "succeeded",
        }


class _SkinProfileRepo:
    async def get_latest(self, tenant_key: str):
        return {"tenant_key": tenant_key, "profile_id": 123}


class _ObligationRepo:
    def __init__(self) -> None:
        self.dispatched: list[tuple[str, str]] = []

    async def list_pending_for_dependency(
        self,
        *,
        tenant_key: str,
        dependency_type: str,
        action_type: str | None = None,
        limit: int = 20,
    ):
        del tenant_key, dependency_type, action_type, limit
        return [
            {
                "obligation_id": "obligation-1",
                "action_type": "generate_skin_diary",
                "payload": {"session_key": "skin_diary:tenant-1", "generation_input": {}},
                "evidence": {"user_request": "图片分析完给我生成今日肌肤日记"},
            }
        ]

    async def mark_dispatched_if_pending(
        self,
        *,
        obligation_id: str,
        dispatched_task_id: str,
    ) -> bool:
        self.dispatched.append((obligation_id, dispatched_task_id))
        return True

    async def revert_dispatched_to_pending(
        self,
        *,
        obligation_id: str,
        dispatched_task_id: str,
    ) -> bool:
        raise AssertionError(f"unexpected revert {obligation_id} {dispatched_task_id}")


class _Runtime:
    def __init__(self) -> None:
        self.tasks: list[TaskEnvelope] = []

    async def submit_task(self, task: TaskEnvelope, *, summary: str | None = None) -> str:
        del summary
        self.tasks.append(task)
        return "queue-1"


class ObligationExtractExecutorTest(unittest.IsolatedAsyncioTestCase):
    async def test_calls_hook_with_turn_context_when_runtime_repo_is_present(self) -> None:
        hook = _Hook()
        executor = make_obligation_extract_executor(
            hook,  # type: ignore[arg-type]
            runtime_task_repo=object(),
        )
        task = TaskEnvelope(
            task_type="obligation_extract",
            payload={
                "tenant_key": "tenant-1",
                "session_key": "main:tenant-1",
                "user_message": "图片分析完给我生成今日肌肤日记",
                "assistant_reply": "结果出来后我会帮你生成今天的肌肤日记。",
                "first_token_reply": "收到啦",
                "main_assistant_reply": "结果出来后我会帮你生成今天的肌肤日记。",
                "media": ["https://example.test/face.png"],
                "tool_calls": [{"tool_name": "analyze_image"}],
                "tool_results": [{"tool_name": "analyze_image", "result": {"ok": True}}],
                "tool_invocations": [{"tool_name": "generate_skin_diary", "status": "submitted"}],
                "runtime_tasks": [{"task_type": "skin_diary_generation", "status": "queued"}],
            },
            stream="obligation_extract",
            tenant_key="tenant-1",
            session_key="main:tenant-1",
        )

        result = await executor(task)

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(len(hook.contexts), 1)
        ctx = hook.contexts[0]
        self.assertEqual(ctx.tenant_key, "tenant-1")
        self.assertEqual(ctx.session_key, "main:tenant-1")
        self.assertEqual(ctx.user_message, "图片分析完给我生成今日肌肤日记")
        self.assertEqual(ctx.first_token_reply, "收到啦")
        self.assertEqual(ctx.media, ["https://example.test/face.png"])
        self.assertEqual(ctx.tool_calls, [{"tool_name": "analyze_image"}])
        self.assertEqual(ctx.tool_results, [{"tool_name": "analyze_image", "result": {"ok": True}}])
        self.assertEqual(ctx.tool_invocations, [{"tool_name": "generate_skin_diary", "status": "submitted"}])
        self.assertEqual(ctx.runtime_tasks, [{"task_type": "skin_diary_generation", "status": "queued"}])

    async def test_dispatches_pending_obligation_when_image_dependency_already_succeeded(self) -> None:
        hook = _Hook()
        runtime = _Runtime()
        obligation_repo = _ObligationRepo()
        executor = make_obligation_extract_executor(
            hook,  # type: ignore[arg-type]
            runtime_task_repo=_RuntimeTaskRepo(),
            obligation_repo=obligation_repo,
            runtime=runtime,  # type: ignore[arg-type]
            skin_profile_repo=_SkinProfileRepo(),
        )
        task = TaskEnvelope(
            task_type="obligation_extract",
            payload={
                "tenant_key": "tenant-1",
                "session_key": "main:tenant-1",
                "user_message": "图片分析完给我生成今日肌肤日记",
                "assistant_reply": "结果出来后我会帮你生成今天的肌肤日记。",
                "media": [],
            },
            stream="obligation_extract",
            tenant_key="tenant-1",
            session_key="main:tenant-1",
        )

        result = await executor(task)

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(obligation_repo.dispatched, [("obligation-1", "obl_obligation-1")])
        self.assertEqual(len(runtime.tasks), 1)
        dispatched_task = runtime.tasks[0]
        self.assertEqual(dispatched_task.task_type, "skin_diary_generation")
        self.assertEqual(dispatched_task.payload["source"], "obligation")
        self.assertEqual(dispatched_task.payload["profile_id"], 123)
        self.assertEqual(dispatched_task.payload["source_task_id"], "image-task-1")
