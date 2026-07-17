"""Tests for image analysis task payload contract."""

from __future__ import annotations

import unittest
import json
from datetime import UTC, datetime, timedelta

from Mojing.tools.image_tools import build_image_analysis_envelope
from Mojing.tools.image_tools import AnalyzeImageTool


class ImageAnalysisPayloadTest(unittest.TestCase):
    def test_payload_uses_image_field_for_external_tool_contract(self) -> None:
        task = build_image_analysis_envelope(
            tenant_key="tenant-1",
            session_key="main:tenant-1",
            origin_session_key="main:session_123_1777472228080",
            image_ref="https://example.com/face.png",
            job_id="job-123",
            image_id="image-123",
            query="",
            source="upload",
        )

        self.assertEqual(task.payload["job_id"], "job-123")
        self.assertEqual(task.payload["image_id"], "image-123")
        self.assertEqual(task.payload["image"], "https://example.com/face.png")
        self.assertEqual(task.payload["session_id"], "main:session_123_1777472228080")
        self.assertEqual(task.payload["session_key"], "main:tenant-1")
        self.assertEqual(task.payload["origin_session_key"], "main:session_123_1777472228080")
        self.assertNotIn("image_ref", task.payload)


class _FakeImageRepo:
    def __init__(self) -> None:
        self.queued = []
        self.created = []
        self.latest_job = None

    async def create_job(
        self,
        *,
        tenant_key: str,
        session_key: str,
        image_ref: str,
        message_id: str | None = None,
        status: str = "uploaded",
    ) -> dict:
        job = {
            "job_id": "job-1",
            "tenant_key": tenant_key,
            "session_key": session_key,
            "message_id": message_id,
            "image_id": "image-1",
            "image_ref": image_ref,
            "status": status,
            "created_at": _utc_naive_now(),
            "updated_at": _utc_naive_now(),
        }
        self.created.append(job)
        return job

    async def get_latest(self, tenant_key: str) -> str | None:
        return "https://example.com/latest.png"

    async def find_latest_job(self, tenant_key: str) -> dict | None:
        del tenant_key
        return self.latest_job

    async def mark_queued(self, job_id: str, *, task_id: str, queue_id: str, payload: dict) -> None:
        self.queued.append((job_id, task_id, queue_id, payload))


class _FakeRuntimeTaskRepo:
    def __init__(self, latest: dict | None = None, *, has_succeeded: bool | None = None) -> None:
        self.latest = latest
        self.has_succeeded = has_succeeded

    async def find_latest_task_for(self, *, tenant_key: str, task_type: str) -> dict | None:
        self.last_query = (tenant_key, task_type)
        return self.latest

    async def has_succeeded_task_for(self, *, tenant_key: str, task_type: str) -> bool:
        self.last_succeeded_query = (tenant_key, task_type)
        if self.has_succeeded is not None:
            return self.has_succeeded
        return str((self.latest or {}).get("status") or "").strip().lower() == "succeeded"


class AnalyzeImageToolPayloadTest(unittest.IsolatedAsyncioTestCase):
    async def test_tool_followup_ack_is_model_visible_and_human_facing(self) -> None:
        repo = _FakeImageRepo()
        tool = AnalyzeImageTool(image_repo=repo)  # type: ignore[arg-type]
        task = build_image_analysis_envelope(
            tenant_key="tenant-1",
            session_key="main:tenant-1",
            image_ref="https://example.com/face.png",
            job_id="job-1",
            image_id="image-1",
        )

        result = tool.durable_result(task, "queue-1")
        payload = json.loads(result.content)

        self.assertTrue(tool.needs_followup)
        self.assertEqual(payload["action"], "submitted")
        self.assertIn("图片分析已提交", payload["message_focus"])
        self.assertIn("反馈回来会告诉用户", payload["message_focus"])
        self.assertNotIn("我已经把这张照片交给图片分析啦", payload["message_focus"])
        self.assertIn("不要说分析或肌肤日记已经完成", payload["model_guidance"])

    async def test_tool_followup_ack_allows_first_successful_analysis_intro(self) -> None:
        repo = _FakeImageRepo()
        runtime_repo = _FakeRuntimeTaskRepo(latest=None)
        tool = AnalyzeImageTool(  # type: ignore[arg-type]
            image_repo=repo,
            runtime_task_repo=runtime_repo,
        )
        tool.set_context(
            tenant_key="tenant-1",
            session_key="main:tenant-1",
            media=["https://example.com/face.png"],
        )

        task = await tool.prepare_task()
        result = tool.durable_result(task, "queue-1")
        payload = json.loads(result.content)

        self.assertIn("selfie_context=first_selfie", payload["message_focus"])
        self.assertIn("首次自拍", payload["message_focus"])
        self.assertIn("继续整理护肤安排", payload["message_focus"])
        self.assertNotIn("继续陪你把今天的护理计划接上", payload["message_focus"])

    async def test_tool_followup_ack_offers_plan_after_existing_successful_analysis(self) -> None:
        repo = _FakeImageRepo()
        runtime_repo = _FakeRuntimeTaskRepo(latest={"status": "succeeded"})
        tool = AnalyzeImageTool(  # type: ignore[arg-type]
            image_repo=repo,
            runtime_task_repo=runtime_repo,
        )
        tool.set_context(
            tenant_key="tenant-1",
            session_key="main:tenant-1",
            media=["https://example.com/face.png"],
        )

        task = await tool.prepare_task()
        result = tool.durable_result(task, "queue-1")
        payload = json.loads(result.content)

        self.assertIn("selfie_context=repeat_selfie", payload["message_focus"])
        self.assertIn("默认不承诺更新肌肤日记", payload["message_focus"])
        self.assertIn("若用户已明确要求", payload["message_focus"])
        self.assertNotIn("我又帮你把这张照片交给图片分析啦", payload["message_focus"])
        self.assertNotIn("顺手帮用户整理一份今天的护肤安排", payload["message_focus"])

    async def test_failed_latest_analysis_after_historical_success_uses_repeat_selfie(self) -> None:
        repo = _FakeImageRepo()
        runtime_repo = _FakeRuntimeTaskRepo(latest={"status": "failed"}, has_succeeded=True)
        tool = AnalyzeImageTool(  # type: ignore[arg-type]
            image_repo=repo,
            runtime_task_repo=runtime_repo,
        )
        tool.set_context(
            tenant_key="tenant-1",
            session_key="main:tenant-1",
            media=["https://example.com/face.png"],
        )

        task = await tool.prepare_task()
        result = tool.durable_result(task, "queue-1")
        payload = json.loads(result.content)

        self.assertIn("selfie_context=repeat_selfie", payload["message_focus"])
        self.assertIn("默认不承诺更新肌肤日记", payload["message_focus"])

    async def test_failed_latest_analysis_without_success_history_uses_first_selfie(self) -> None:
        repo = _FakeImageRepo()
        runtime_repo = _FakeRuntimeTaskRepo(latest={"status": "failed"}, has_succeeded=False)
        tool = AnalyzeImageTool(  # type: ignore[arg-type]
            image_repo=repo,
            runtime_task_repo=runtime_repo,
        )
        tool.set_context(
            tenant_key="tenant-1",
            session_key="main:tenant-1",
            media=["https://example.com/face.png"],
        )

        task = await tool.prepare_task()
        result = tool.durable_result(task, "queue-1")
        payload = json.loads(result.content)

        self.assertIn("selfie_context=first_selfie", payload["message_focus"])

    async def test_tool_context_uses_origin_session_for_external_payload(self) -> None:
        repo = _FakeImageRepo()
        tool = AnalyzeImageTool(image_repo=repo)  # type: ignore[arg-type]
        tool.set_context(
            tenant_key="tenant-1",
            session_key="main:tenant-1",
            origin_session_key="main:session_123_1777472228080",
            media=["https://example.com/face.png"],
        )

        task = await tool.prepare_task()

        self.assertEqual(task.payload["session_key"], "main:tenant-1")
        self.assertEqual(task.payload["session_id"], "main:session_123_1777472228080")
        self.assertEqual(task.payload["job_id"], "job-1")
        self.assertEqual(task.payload["image_id"], "image-1")

        await tool.on_task_submitted(task, "queue-1")

        self.assertEqual(repo.queued[0][0], "job-1")
        self.assertEqual(repo.queued[0][2], "queue-1")

    async def test_dedupes_recent_same_image_job(self) -> None:
        repo = _FakeImageRepo()
        repo.latest_job = {
            "job_id": "job-existing",
            "image_ref": "https://example.com/face.png",
            "status": "queued",
            "created_at": _utc_naive_now() - timedelta(seconds=10),
        }
        tool = AnalyzeImageTool(image_repo=repo)  # type: ignore[arg-type]
        tool.set_context(
            tenant_key="tenant-1",
            session_key="main:tenant-1",
            media=["https://example.com/face.png"],
        )

        result = await tool.prepare_task()

        payload = json.loads(result.content)
        self.assertEqual(payload["action"], "deduped")
        self.assertEqual(payload["invocation_status"], "deduped")
        self.assertFalse(payload["runtime_task_created"])
        self.assertEqual(payload["source"], "image_job")
        self.assertEqual(payload["job_id"], "job-existing")
        self.assertEqual(payload["business_status"], "queued")
        self.assertEqual(repo.created, [])

    async def test_uploaded_image_job_is_reused_not_deduped(self) -> None:
        repo = _FakeImageRepo()
        repo.latest_job = {
            "job_id": "job-uploaded",
            "image_id": "image-uploaded",
            "image_ref": "https://example.com/face.png",
            "status": "uploaded",
            "created_at": _utc_naive_now() - timedelta(seconds=10),
        }
        tool = AnalyzeImageTool(image_repo=repo)  # type: ignore[arg-type]
        tool.set_context(
            tenant_key="tenant-1",
            session_key="main:tenant-1",
            media=["https://example.com/face.png"],
        )

        task = await tool.prepare_task()

        self.assertEqual(task.payload["job_id"], "job-uploaded")
        self.assertEqual(task.payload["image_id"], "image-uploaded")
        self.assertEqual(task.payload["image"], "https://example.com/face.png")
        self.assertEqual(repo.created, [])

    async def test_dedupes_recent_runtime_task_when_no_matching_image_job(self) -> None:
        repo = _FakeImageRepo()
        runtime_repo = _FakeRuntimeTaskRepo(
            {
                "task_id": "task-existing",
                "status": "wait_external",
                "created_at": _utc_naive_now() - timedelta(seconds=10),
            }
        )
        tool = AnalyzeImageTool(
            image_repo=repo,  # type: ignore[arg-type]
            runtime_task_repo=runtime_repo,  # type: ignore[arg-type]
        )
        tool.set_context(
            tenant_key="tenant-1",
            session_key="main:tenant-1",
            media=["https://example.com/face.png"],
        )

        result = await tool.prepare_task()

        payload = json.loads(result.content)
        self.assertEqual(payload["action"], "deduped")
        self.assertEqual(payload["invocation_status"], "deduped")
        self.assertFalse(payload["runtime_task_created"])
        self.assertEqual(payload["source"], "runtime_task")
        self.assertEqual(payload["task_id"], "task-existing")
        self.assertEqual(payload["runtime_task_status"], "wait_external")
        self.assertEqual(runtime_repo.last_query, ("tenant-1", "image_analysis"))
        self.assertEqual(repo.created, [])

    async def test_recent_runtime_task_does_not_dedupe_different_image_when_job_table_is_available(self) -> None:
        repo = _FakeImageRepo()
        repo.latest_job = {
            "job_id": "job-other",
            "image_ref": "https://example.com/other.png",
            "status": "queued",
            "created_at": _utc_naive_now() - timedelta(seconds=10),
        }
        runtime_repo = _FakeRuntimeTaskRepo(
            {
                "task_id": "task-existing",
                "status": "wait_external",
                "created_at": _utc_naive_now() - timedelta(seconds=10),
            }
        )
        tool = AnalyzeImageTool(
            image_repo=repo,  # type: ignore[arg-type]
            runtime_task_repo=runtime_repo,  # type: ignore[arg-type]
        )
        tool.set_context(
            tenant_key="tenant-1",
            session_key="main:tenant-1",
            media=["https://example.com/face.png"],
        )

        task = await tool.prepare_task()

        self.assertEqual(task.payload["image"], "https://example.com/face.png")
        self.assertEqual(task.payload["job_id"], "job-1")
        self.assertFalse(hasattr(runtime_repo, "last_query"))
        self.assertEqual(runtime_repo.last_succeeded_query, ("tenant-1", "image_analysis"))
        self.assertEqual(len(repo.created), 1)


def _utc_naive_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


if __name__ == "__main__":
    unittest.main()
