"""Tests for unified tool execution runtime."""

from __future__ import annotations

import json
import unittest

from simpleclaw.core.messages import ToolCall
from simpleclaw.harness.lifecycle import ToolGateDecision, ToolInvocationContext, ToolLifecycle
from simpleclaw.runtime.task_protocol import TaskEnvelope
from simpleclaw.tools.base import Tool, ToolResult
from simpleclaw.tools.registry import ToolRegistry


class _FakeRuntimeServices:
    def __init__(self) -> None:
        self.submitted: list[TaskEnvelope] = []

    async def submit_task(self, task: TaskEnvelope) -> str:
        self.submitted.append(task)
        return "queue-1"


class _InlineJsonTool(Tool):
    name = "inline_json"
    description = "return structured json"
    parameters = {"type": "object", "properties": {}, "required": []}

    async def execute(self, **_) -> ToolResult:
        return ToolResult(content=json.dumps({"action": "deduped"}))


class _ValidatedTool(Tool):
    name = "validated"
    description = "validate params"
    parameters = {"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]}

    def validate_params(self, params: dict) -> list[str]:
        return [] if params.get("value") else ["value is required"]

    async def execute(self, **_) -> ToolResult:
        return ToolResult(content="ok")


class _DurableTool(Tool):
    name = "durable"
    description = "submit a durable task"
    parameters = {"type": "object", "properties": {}, "required": []}
    execution_mode = "durable"

    async def prepare_task(self, **_) -> TaskEnvelope:
        return TaskEnvelope(
            task_type="durable",
            payload={"x": 1},
            stream="test_stream",
            tenant_key="tenant-1",
        )


class _DenyHook:
    def __init__(self) -> None:
        self.seen: list[ToolInvocationContext] = []

    async def before_tool(self, ctx: ToolInvocationContext) -> ToolGateDecision:
        self.seen.append(ctx)
        return ToolGateDecision.deny(
            action="deferred",
            reason="need_context",
            phase="prerequisite_missing",
            message_focus="ask for context first",
            facts={"tenant_key": ctx.tenant_key},
        )


class ToolExecutionRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def test_structured_success_action_is_ok(self) -> None:
        registry = ToolRegistry()
        registry.register(_InlineJsonTool())

        result = await registry.execute(ToolCall(id="1", name="inline_json", arguments={}))

        self.assertTrue(result.ok)
        self.assertEqual(json.loads(result.content)["action"], "deduped")

    async def test_validation_error_returns_hint(self) -> None:
        registry = ToolRegistry()
        registry.register(_ValidatedTool())

        result = await registry.execute(ToolCall(id="1", name="validated", arguments={}))

        self.assertFalse(result.ok)
        self.assertIn("Invalid parameters", result.content)
        self.assertIn("try a different approach", result.content)

    async def test_durable_tool_submits_task_once(self) -> None:
        runtime = _FakeRuntimeServices()
        registry = ToolRegistry(runtime_services=runtime)  # type: ignore[arg-type]
        registry.register(_DurableTool())

        result = await registry.execute(ToolCall(id="1", name="durable", arguments={}))

        self.assertTrue(result.ok)
        self.assertEqual(len(runtime.submitted), 1)
        payload = json.loads(result.content)
        self.assertEqual(payload["action"], "queued")
        self.assertEqual(payload["queue_id"], "queue-1")

    async def test_before_tool_gate_short_circuits_before_durable_submit(self) -> None:
        runtime = _FakeRuntimeServices()
        hook = _DenyHook()
        registry = ToolRegistry(
            runtime_services=runtime,  # type: ignore[arg-type]
            tool_lifecycle=ToolLifecycle(before_tool_hooks=[hook]),
        )
        tool = _DurableTool()
        tool._tenant_key = "tenant-1"  # type: ignore[attr-defined]
        registry.register(tool)

        result = await registry.execute(ToolCall(id="1", name="durable", arguments={}))

        self.assertTrue(result.ok)
        self.assertEqual(len(runtime.submitted), 0)
        self.assertEqual(hook.seen[0].tenant_key, "tenant-1")
        payload = json.loads(result.content)
        self.assertEqual(payload["action"], "deferred")
        self.assertEqual(payload["reason"], "need_context")
        self.assertEqual(payload["message_focus"], "ask for context first")

    async def test_unknown_tool_is_failure(self) -> None:
        result = await ToolRegistry().execute(ToolCall(id="1", name="missing", arguments={}))

        self.assertFalse(result.ok)
        self.assertIn("Unknown tool", result.content)


if __name__ == "__main__":
    unittest.main()
