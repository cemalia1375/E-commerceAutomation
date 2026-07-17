"""Tests for trigger tool results returned to the second ReAct turn."""

from __future__ import annotations

import json
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone

sys.modules.setdefault("loguru", types.SimpleNamespace(logger=types.SimpleNamespace(
    info=lambda *_, **__: None,
    debug=lambda *_, **__: None,
    warning=lambda *_, **__: None,
    error=lambda *_, **__: None,
)))

from Mojing.tools.notify_skin_diary import NotifySkinDiaryChatTool
from Mojing.tools.deep_report_chat import DeepReportChatTool
from simpleclaw.runtime.task_protocol import TaskEnvelope


class _RuntimeTaskRepo:
    async def find_latest_by_scope_key(self, *, tenant_key: str, task_type: str, scope_key: str):
        del tenant_key, task_type, scope_key
        return {"task_id": "image-task-1"}

    async def find_latest_succeeded_task_for(self, *, tenant_key: str, task_type: str):
        del tenant_key, task_type
        return {
            "task_id": "image-task-1",
            "status": "succeeded",
            "completed_at": datetime.now(timezone.utc),
            "payload": {
                "image_id": "img-1",
                "image": "https://example.com/face.jpg",
            },
        }


class _ImageRepo:
    async def find_latest_job(self, tenant_key: str):
        del tenant_key
        return {"image_id": "img-1", "image_ref": "https://example.com/face.jpg"}


class _StaleImageRepo(_ImageRepo):
    async def get_latest_time(self, tenant_key: str):
        del tenant_key
        return datetime.now(timezone.utc) - timedelta(hours=9)


class _StaleRuntimeTaskRepo(_RuntimeTaskRepo):
    async def find_latest_succeeded_task_for(self, *, tenant_key: str, task_type: str):
        del tenant_key, task_type
        return {
            "task_id": "image-task-stale",
            "status": "succeeded",
            "completed_at": datetime.now(timezone.utc) - timedelta(hours=9),
            "payload": {
                "image_id": "img-stale",
                "image": "https://example.com/stale-face.jpg",
            },
        }


class _MissingRuntimeTaskRepo:
    async def find_latest_succeeded_task_for(self, *, tenant_key: str, task_type: str):
        del tenant_key, task_type
        return None


class NotifySkinDiaryToolResultTest(unittest.IsolatedAsyncioTestCase):
    async def test_durable_result_contains_second_turn_guidance(self) -> None:
        tool = NotifySkinDiaryChatTool()
        tool.set_context(tenant_key="tenant-1")

        task = await tool.prepare_task(task="帮用户看看今天的肌肤日记")
        self.assertIsInstance(task, TaskEnvelope)

        result = tool.durable_result(task, "queue-1")
        payload = json.loads(result.content)

        self.assertEqual(payload["action"], "submitted")
        self.assertEqual(payload["invocation_status"], "submitted")
        self.assertEqual(payload["runtime_task_status"], "queued")
        self.assertEqual(payload["subagent"], "skin_diary_chat")
        self.assertIn("肌肤日记", payload["where"])
        self.assertIn("message_focus", payload)

    async def test_dispatch_payload_carries_upstream_main_session(self) -> None:
        tool = NotifySkinDiaryChatTool()
        tool.set_context(
            tenant_key="334",
            session_key="main:session_334_1777472228080_CknLvH",
            query="帮我看看肌肤日记",
        )

        task = await tool.prepare_task()

        self.assertIsInstance(task, TaskEnvelope)
        self.assertEqual(task.session_key, "skin_diary:334")
        self.assertEqual(task.payload["origin_session_key"], "main:session_334_1777472228080_CknLvH")
        self.assertEqual(task.payload["handoff_contract"]["intent"], "chat")
        self.assertNotIn("required_tool", task.payload["handoff_contract"])

    async def test_dispatch_payload_carries_current_image_analysis_source(self) -> None:
        tool = NotifySkinDiaryChatTool(
            runtime_task_repo=_RuntimeTaskRepo(),
            image_repo=_ImageRepo(),
        )
        tool.set_context(
            tenant_key="334",
            session_key="main:334",
            query="帮我刷新今天的肌肤日记",
        )

        task = await tool.prepare_task(intent="handoff")

        self.assertIsInstance(task, TaskEnvelope)
        self.assertEqual(task.payload["source_task_id"], "image-task-1")
        self.assertEqual(task.payload["source_image_id"], "img-1")
        self.assertEqual(task.payload["source_image_ref"], "https://example.com/face.jpg")

    async def test_handoff_intent_requires_skin_diary_generation_tool(self) -> None:
        tool = NotifySkinDiaryChatTool()
        tool.set_context(
            tenant_key="334",
            session_key="main:334",
            query="帮我刷新今天的肌肤日记",
        )

        task = await tool.prepare_task(intent="handoff")

        self.assertIsInstance(task, TaskEnvelope)
        contract = task.payload["handoff_contract"]
        self.assertEqual(contract["intent"], "handoff")
        self.assertEqual(contract["required_tool"], "generate_skin_diary")
        self.assertFalse(contract["show_existing_first"])

    async def test_handoff_blocks_stale_selfie_before_dispatch(self) -> None:
        tool = NotifySkinDiaryChatTool(runtime_task_repo=_StaleRuntimeTaskRepo())
        tool.set_context(
            tenant_key="334",
            session_key="main:334",
            query="帮我刷新今天的肌肤日记",
        )

        result = await tool.prepare_task(intent="handoff")

        self.assertNotIsInstance(result, TaskEnvelope)
        payload = json.loads(result.content)
        self.assertEqual(payload["action"], "blocked")
        self.assertEqual(payload["reason"], "stale_selfie")
        self.assertFalse(payload["runtime_task_created"])
        self.assertIn("allow_stale_selfie=true", payload["model_guidance"])

    async def test_handoff_allows_stale_selfie_when_user_confirms_reuse(self) -> None:
        tool = NotifySkinDiaryChatTool(runtime_task_repo=_StaleRuntimeTaskRepo())
        tool.set_context(
            tenant_key="334",
            session_key="main:334",
            query="就沿用之前那张，帮我刷新今天的肌肤日记",
        )

        task = await tool.prepare_task(intent="handoff", allow_stale_selfie=True)

        self.assertIsInstance(task, TaskEnvelope)
        self.assertEqual(task.session_key, "skin_diary:334")
        self.assertEqual(task.payload["handoff_contract"]["required_tool"], "generate_skin_diary")

    async def test_handoff_blocks_when_no_succeeded_selfie_analysis(self) -> None:
        tool = NotifySkinDiaryChatTool(runtime_task_repo=_MissingRuntimeTaskRepo())
        tool.set_context(
            tenant_key="334",
            session_key="main:334",
            query="帮我刷新今天的肌肤日记",
        )

        result = await tool.prepare_task(intent="handoff")

        self.assertNotIsInstance(result, TaskEnvelope)
        payload = json.loads(result.content)
        self.assertEqual(payload["action"], "blocked")
        self.assertEqual(payload["reason"], "missing_selfie")
        self.assertFalse(payload["runtime_task_created"])


class DeepReportChatToolResultTest(unittest.IsolatedAsyncioTestCase):
    async def test_durable_result_guides_user_to_deep_report_chat(self) -> None:
        tool = DeepReportChatTool()
        tool.set_context(tenant_key="tenant-1")

        task = await tool.prepare_task(task="帮用户解释深度分析报告里的屏障问题")
        self.assertIsInstance(task, TaskEnvelope)

        result = tool.durable_result(task, "queue-1")
        payload = json.loads(result.content)

        self.assertEqual(payload["action"], "submitted")
        self.assertEqual(payload["invocation_status"], "submitted")
        self.assertEqual(payload["runtime_task_status"], "queued")
        self.assertEqual(payload["subagent"], "deep_report")
        self.assertIn("深度分析报告", payload["where"])
        self.assertIn("message_focus", payload)

    async def test_dispatch_payload_carries_upstream_main_session(self) -> None:
        tool = DeepReportChatTool()
        tool.set_context(
            tenant_key="334",
            session_key="main:session_334_1777472228080_CknLvH",
            query="帮我触发深度分析报告",
        )

        task = await tool.prepare_task()

        self.assertIsInstance(task, TaskEnvelope)
        self.assertEqual(task.session_key, "deep_report:334")
        self.assertEqual(task.payload["origin_session_key"], "main:session_334_1777472228080_CknLvH")

    async def test_handoff_blocks_stale_selfie_before_deep_report_dispatch(self) -> None:
        tool = DeepReportChatTool(runtime_task_repo=_StaleRuntimeTaskRepo())
        tool.set_context(
            tenant_key="334",
            session_key="main:334",
            query="帮我重新生成深度分析报告",
        )

        result = await tool.prepare_task(intent="handoff")

        self.assertNotIsInstance(result, TaskEnvelope)
        payload = json.loads(result.content)
        self.assertEqual(payload["action"], "blocked")
        self.assertEqual(payload["reason"], "stale_selfie")
        self.assertFalse(payload["runtime_task_created"])
        self.assertIn("allow_stale_selfie=true", payload["model_guidance"])


if __name__ == "__main__":
    unittest.main()
