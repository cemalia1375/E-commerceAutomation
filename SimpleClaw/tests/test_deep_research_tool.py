"""Tests for DeepResearchTool idempotency behavior."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta

from Mojing.runtime.task_types import MojingTaskType
from Mojing.tools.deep_research import DeepResearchTool
from simpleclaw.runtime.task_protocol import TaskEnvelope
from simpleclaw.tools.base import ToolResult


class _DocRepo:
    async def get(self, tenant_key: str, name: str) -> str:
        return "用户画像"


class _RuntimeTaskRepo:
    def __init__(self, latest=None) -> None:
        self.latest = latest

    async def find_latest_task_for(self, *, tenant_key: str, task_type: str):
        return self.latest


class _ImageRepo:
    def __init__(self, latest=None) -> None:
        self.latest = latest

    async def find_latest_job(self, tenant_key: str):
        return self.latest


class DeepResearchToolTest(unittest.IsolatedAsyncioTestCase):
    def _tool(self, latest=None, image_latest=None) -> DeepResearchTool:
        tool = DeepResearchTool(
            endpoint_url="http://example.invalid/deep",
            document_repo=_DocRepo(),  # type: ignore[arg-type]
            image_repo=_ImageRepo(image_latest),  # type: ignore[arg-type]
            runtime_task_repo=_RuntimeTaskRepo(latest),  # type: ignore[arg-type]
        )
        tool.set_context(tenant_key="tenant-1", session_key="main:tenant-1", query="帮我生成报告")
        return tool

    async def test_active_task_dedupes_with_runtime_status(self) -> None:
        latest = {
            "status": "wait_external",
            "created_at": (datetime.utcnow() - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S"),
        }

        result = await self._tool(latest).prepare_task()

        self.assertIsInstance(result, ToolResult)
        payload = json.loads(result.content)
        self.assertEqual(payload["action"], "deduped")
        self.assertEqual(payload["invocation_status"], "deduped")
        self.assertFalse(payload["runtime_task_created"])
        self.assertEqual(payload["source"], "runtime_task_status")
        self.assertEqual(payload["phase"], "in_progress")
        self.assertIn("model_guidance", payload)

    async def test_failed_latest_allows_new_task(self) -> None:
        latest = {
            "status": "failed",
            "created_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        }

        result = await self._tool(latest).prepare_task()

        self.assertIsInstance(result, TaskEnvelope)
        self.assertEqual(result.task_type, MojingTaskType.DEEP_RESEARCH)
        self.assertEqual(result.payload["trace_id"], result.trace_id)

    async def test_payload_uses_origin_session_from_main_agent_dispatch(self) -> None:
        tool = DeepResearchTool(
            endpoint_url="http://example.invalid/deep",
            document_repo=_DocRepo(),  # type: ignore[arg-type]
            image_repo=_ImageRepo(),  # type: ignore[arg-type]
            runtime_task_repo=_RuntimeTaskRepo({"status": "failed"}),  # type: ignore[arg-type]
        )
        tool.set_context(
            tenant_key="334",
            session_key="deep_report:334",
            origin_session_key="main:session_334_1777472228080_CknLvH",
            query="帮我生成报告",
        )

        result = await tool.prepare_task()

        self.assertIsInstance(result, TaskEnvelope)
        self.assertEqual(result.session_key, "deep_report:334")
        self.assertEqual(result.payload["session_id"], "main:session_334_1777472228080_CknLvH")

    async def test_payload_uses_image_analysis_session_before_origin_session(self) -> None:
        tool = DeepResearchTool(
            endpoint_url="http://example.invalid/deep",
            document_repo=_DocRepo(),  # type: ignore[arg-type]
            image_repo=_ImageRepo({
                "session_key": "main:job-session-fallback",
                "request_payload": {
                    "payload": {
                        "session_id": "main:image-analysis-session",
                    },
                },
            }),  # type: ignore[arg-type]
            runtime_task_repo=_RuntimeTaskRepo({"status": "failed"}),  # type: ignore[arg-type]
        )
        tool.set_context(
            tenant_key="334",
            session_key="deep_report:334",
            origin_session_key="main:origin-session",
            query="帮我生成报告",
        )

        result = await tool.prepare_task()

        self.assertIsInstance(result, TaskEnvelope)
        self.assertEqual(result.session_key, "deep_report:334")
        self.assertEqual(result.payload["session_id"], "main:image-analysis-session")

    async def test_payload_uses_image_job_session_when_request_payload_missing(self) -> None:
        task = await self._tool(
            latest={"status": "failed"},
            image_latest={"session_key": "main:image-job-session", "request_payload": {}},
        ).prepare_task()

        self.assertIsInstance(task, TaskEnvelope)
        self.assertEqual(task.payload["session_id"], "main:image-job-session")

    async def test_external_payload_ids_are_not_compacted(self) -> None:
        tool = DeepResearchTool(
            endpoint_url="http://example.invalid/deep",
            document_repo=_DocRepo(),  # type: ignore[arg-type]
            image_repo=_ImageRepo(),  # type: ignore[arg-type]
            runtime_task_repo=_RuntimeTaskRepo({"status": "failed"}),  # type: ignore[arg-type]
        )
        long_tenant = "test_I08_full_skin_journey_fetch_cron_report_status_20260510105619"
        long_session = f"main:{long_tenant}"
        tool.set_context(
            tenant_key=long_tenant,
            session_key=f"deep_report:{long_tenant}",
            origin_session_key=long_session,
            query="帮我生成报告",
        )

        result = await tool.prepare_task()

        self.assertIsInstance(result, TaskEnvelope)
        self.assertEqual(result.tenant_key, long_tenant)
        self.assertEqual(result.session_key, f"deep_report:{long_tenant}")
        self.assertEqual(result.payload["user_id"], long_tenant)
        self.assertEqual(result.payload["session_id"], long_session)

    async def test_durable_result_guides_second_react_turn(self) -> None:
        task = await self._tool().prepare_task()
        self.assertIsInstance(task, TaskEnvelope)

        result = self._tool().durable_result(task, "queue-1")
        payload = json.loads(result.content)

        self.assertEqual(payload["action"], "submitted")
        self.assertEqual(payload["invocation_status"], "submitted")
        self.assertTrue(payload["runtime_task_created"])
        self.assertEqual(payload["runtime_task_status"], "queued")
        self.assertEqual(payload["estimated_minutes"], 10)
        self.assertIn("我的报告", payload["where"])
        self.assertIn("message_focus", payload)

if __name__ == "__main__":
    unittest.main()
