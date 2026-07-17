from __future__ import annotations

import json
import unittest

from Mojing.tools.runtime_status import CheckRuntimeStatusTool


class _RuntimeTaskRepo:
    def __init__(self, by_type=None, recent=None, by_scope=None, by_source_task_id=None) -> None:
        self.by_type = by_type or {}
        self.recent = recent or []
        self.by_scope = by_scope or {}
        self.by_source_task_id = by_source_task_id or {}

    async def find_latest_task_for(self, *, tenant_key: str, task_type: str):
        del tenant_key
        return self.by_type.get(str(task_type))

    async def find_latest_by_scope_key(self, *, tenant_key: str, task_type: str, scope_key: str):
        del tenant_key, task_type
        return self.by_scope.get(str(scope_key))

    async def find_latest_by_source_task_id(self, *, tenant_key: str, task_type: str, source_task_id: str):
        del tenant_key, task_type
        return self.by_source_task_id.get(str(source_task_id))

    async def list_recent(self, *, tenant_key: str = "", limit: int = 20):
        del tenant_key, limit
        return list(self.recent)


class _ImageRepo:
    def __init__(self, latest=None) -> None:
        self.latest = latest

    async def find_latest_job(self, tenant_key: str):
        del tenant_key
        return self.latest


class RuntimeStatusToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_schema_excludes_skin_diary_when_disabled(self) -> None:
        tool = CheckRuntimeStatusTool(
            runtime_task_repo=_RuntimeTaskRepo(),
            include_deep_report=True,
            include_skin_diary=False,
        )

        target_schema = tool.parameters["properties"]["target"]

        self.assertNotIn("skin_diary", target_schema["enum"])
        self.assertNotIn("肌肤日记", tool.description)
        self.assertEqual(
            tool.validate_params({"target": "skin_diary"}),
            ["target must be one of image_analysis/deep_report/all_recent"],
        )

    async def test_schema_includes_skin_diary_by_default(self) -> None:
        tool = CheckRuntimeStatusTool(runtime_task_repo=_RuntimeTaskRepo())

        target_schema = tool.parameters["properties"]["target"]

        self.assertIn("skin_diary", target_schema["enum"])
        self.assertIn("肌肤日记", tool.description)

    async def test_execute_rejects_skin_diary_when_disabled(self) -> None:
        tool = CheckRuntimeStatusTool(
            runtime_task_repo=_RuntimeTaskRepo(),
            include_deep_report=True,
            include_skin_diary=False,
        )
        tool.set_context(tenant_key="tenant-1", session_key="main:tenant-1")

        result = await tool.execute(target="skin_diary")
        payload = json.loads(result.content)

        self.assertFalse(result.ok)
        self.assertEqual(payload["status"], "unsupported_target")
        self.assertIn("不要编造", payload["model_guidance"])

    async def test_schema_excludes_deep_report_when_disabled(self) -> None:
        tool = CheckRuntimeStatusTool(
            runtime_task_repo=_RuntimeTaskRepo(),
            include_deep_report=False,
            include_skin_diary=True,
        )

        target_schema = tool.parameters["properties"]["target"]

        self.assertNotIn("deep_report", target_schema["enum"])
        self.assertNotIn("深度报告", tool.description)
        self.assertEqual(
            tool.validate_params({"target": "deep_report"}),
            ["target must be one of image_analysis/skin_diary/all_recent"],
        )

    async def test_execute_rejects_deep_report_when_disabled(self) -> None:
        tool = CheckRuntimeStatusTool(
            runtime_task_repo=_RuntimeTaskRepo(),
            include_deep_report=False,
            include_skin_diary=True,
        )
        tool.set_context(tenant_key="tenant-1", session_key="main:tenant-1")

        result = await tool.execute(target="deep_report")
        payload = json.loads(result.content)

        self.assertFalse(result.ok)
        self.assertEqual(payload["status"], "unsupported_target")
        self.assertIn("不要编造", payload["model_guidance"])

    async def test_missing_context(self) -> None:
        tool = CheckRuntimeStatusTool(runtime_task_repo=_RuntimeTaskRepo())

        result = await tool.execute(target="all_recent")
        payload = json.loads(result.content)

        self.assertFalse(result.ok)
        self.assertEqual(payload["status"], "missing_context")

    async def test_image_analysis_running(self) -> None:
        tool = CheckRuntimeStatusTool(
            runtime_task_repo=_RuntimeTaskRepo(
                by_scope={
                    "image_analysis:tenant-1:img-current": {
                        "task_type": "image_analysis",
                        "status": "running",
                    },
                },
            ),
            image_repo=_ImageRepo({"image_id": "img-current"}),
        )
        tool.set_context(tenant_key="tenant-1", session_key="main:tenant-1")

        result = await tool.execute(target="image_analysis")
        payload = json.loads(result.content)

        self.assertTrue(result.ok)
        self.assertEqual(payload["target"], "image_analysis")
        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["user_visible_summary"], "图片分析还在处理中。")
        self.assertIn("不要编造结果", payload["model_guidance"])

    async def test_image_analysis_uses_current_image_scope(self) -> None:
        tool = CheckRuntimeStatusTool(
            runtime_task_repo=_RuntimeTaskRepo(
                by_type={"image_analysis": {"task_type": "image_analysis", "status": "succeeded"}},
                by_scope={
                    "image_analysis:tenant-1:img-current": {
                        "task_type": "image_analysis",
                        "status": "running",
                    }
                },
            ),
            image_repo=_ImageRepo({"image_id": "img-current"}),
        )
        tool.set_context(tenant_key="tenant-1", session_key="main:tenant-1")

        result = await tool.execute(target="image_analysis")
        payload = json.loads(result.content)

        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["scope"], "current_image")
        self.assertEqual(payload["user_visible_summary"], "图片分析还在处理中。")

    async def test_image_analysis_does_not_fallback_when_current_image_has_no_task(self) -> None:
        tool = CheckRuntimeStatusTool(
            runtime_task_repo=_RuntimeTaskRepo(
                by_type={"image_analysis": {"task_type": "image_analysis", "status": "succeeded"}},
            ),
            image_repo=_ImageRepo({"image_id": "img-current"}),
        )
        tool.set_context(tenant_key="tenant-1", session_key="main:tenant-1")

        result = await tool.execute(target="image_analysis")
        payload = json.loads(result.content)

        self.assertEqual(payload["status"], "empty")
        self.assertEqual(payload["scope"], "current_image")
        self.assertIn("刚刚这张图片", payload["user_visible_summary"])
        self.assertIn("不要引用旧图片分析结果", payload["model_guidance"])

    async def test_deep_report_succeeded(self) -> None:
        tool = CheckRuntimeStatusTool(
            runtime_task_repo=_RuntimeTaskRepo(
                by_type={"deep_research": {"task_type": "deep_research", "status": "succeeded"}}
            )
        )
        tool.set_context(tenant_key="tenant-1", session_key="main:tenant-1")

        result = await tool.execute(target="deep_report")
        payload = json.loads(result.content)

        self.assertEqual(payload["status"], "succeeded")
        self.assertEqual(payload["user_visible_summary"], "深度报告已经生成。")
        self.assertIn("深度分析报告会话", payload["model_guidance"])

    async def test_skin_diary_uses_current_image_analysis_source_task(self) -> None:
        tool = CheckRuntimeStatusTool(
            runtime_task_repo=_RuntimeTaskRepo(
                by_type={
                    "skin_diary_generation": {
                        "task_type": "skin_diary_generation",
                        "status": "succeeded",
                    },
                },
                by_scope={
                    "image_analysis:tenant-1:img-current": {
                        "task_id": "image-task-current",
                        "task_type": "image_analysis",
                        "status": "succeeded",
                    },
                },
                by_source_task_id={
                    "image-task-current": {
                        "task_type": "skin_diary_generation",
                        "status": "running",
                    },
                },
            ),
            image_repo=_ImageRepo({"image_id": "img-current"}),
        )
        tool.set_context(tenant_key="tenant-1", session_key="main:tenant-1")

        result = await tool.execute(target="skin_diary")
        payload = json.loads(result.content)

        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["scope"], "current_image")
        self.assertEqual(payload["user_visible_summary"], "肌肤日记还在生成中。")

    async def test_skin_diary_does_not_fallback_when_current_image_has_no_diary_task(self) -> None:
        tool = CheckRuntimeStatusTool(
            runtime_task_repo=_RuntimeTaskRepo(
                by_type={
                    "skin_diary_generation": {
                        "task_type": "skin_diary_generation",
                        "status": "succeeded",
                    },
                },
                by_scope={
                    "image_analysis:tenant-1:img-current": {
                        "task_id": "image-task-current",
                        "task_type": "image_analysis",
                        "status": "succeeded",
                    },
                },
            ),
            image_repo=_ImageRepo({"image_id": "img-current"}),
        )
        tool.set_context(tenant_key="tenant-1", session_key="main:tenant-1")

        result = await tool.execute(target="skin_diary")
        payload = json.loads(result.content)

        self.assertEqual(payload["status"], "empty")
        self.assertEqual(payload["scope"], "current_image")
        self.assertIn("刚刚这张图片", payload["user_visible_summary"])
        self.assertIn("不要引用旧肌肤日记结果", payload["model_guidance"])

    async def test_image_analysis_failed(self) -> None:
        tool = CheckRuntimeStatusTool(
            runtime_task_repo=_RuntimeTaskRepo(
                by_scope={
                    "image_analysis:tenant-1:img-current": {
                        "task_type": "image_analysis",
                        "status": "failed",
                    },
                },
            ),
            image_repo=_ImageRepo({"image_id": "img-current"}),
        )
        tool.set_context(tenant_key="tenant-1", session_key="main:tenant-1")

        result = await tool.execute(target="image_analysis")
        payload = json.loads(result.content)

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["user_visible_summary"], "刚刚这次图片分析没有成功，没有拿到结果。")
        self.assertIn("图片分析断掉了", payload["model_guidance"])
        self.assertIn("用刚才那张照片重新分析一次", payload["model_guidance"])
        self.assertIn("照片明显不可用", payload["model_guidance"])
        self.assertIn("不要编造肤况结论", payload["model_guidance"])

    async def test_deep_report_failed(self) -> None:
        tool = CheckRuntimeStatusTool(
            runtime_task_repo=_RuntimeTaskRepo(
                by_type={"deep_research": {"task_type": "deep_research", "status": "failed"}}
            )
        )
        tool.set_context(tenant_key="tenant-1", session_key="main:tenant-1")

        result = await tool.execute(target="deep_report")
        payload = json.loads(result.content)

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["user_visible_summary"], "刚刚这次深度报告生成没有成功，没有拿到结果。")
        self.assertIn("重新生成一次", payload["model_guidance"])
        self.assertIn("不要编造报告内容", payload["model_guidance"])

    async def test_skin_diary_failed(self) -> None:
        tool = CheckRuntimeStatusTool(
            runtime_task_repo=_RuntimeTaskRepo(
                by_scope={
                    "image_analysis:tenant-1:img-current": {
                        "task_id": "image-task-current",
                        "task_type": "image_analysis",
                        "status": "succeeded",
                    },
                },
                by_source_task_id={
                    "image-task-current": {
                        "task_type": "skin_diary_generation",
                        "status": "failed",
                    }
                },
            ),
            image_repo=_ImageRepo({"image_id": "img-current"}),
        )
        tool.set_context(tenant_key="tenant-1", session_key="main:tenant-1")

        result = await tool.execute(target="skin_diary")
        payload = json.loads(result.content)

        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["user_visible_summary"], "刚刚这次肌肤日记生成没有成功，没有生成新版日记。")
        self.assertIn("重新生成一次", payload["model_guidance"])
        self.assertIn("不要编造日记内容", payload["model_guidance"])

    async def test_target_empty(self) -> None:
        tool = CheckRuntimeStatusTool(runtime_task_repo=_RuntimeTaskRepo())
        tool.set_context(tenant_key="tenant-1", session_key="main:tenant-1")

        result = await tool.execute(target="deep_report")
        payload = json.loads(result.content)

        self.assertEqual(payload["status"], "empty")
        self.assertIsNone(payload["latest_task"])
        self.assertEqual(payload["user_visible_summary"], "还没有查到深度报告任务。")

    async def test_all_recent_empty(self) -> None:
        tool = CheckRuntimeStatusTool(runtime_task_repo=_RuntimeTaskRepo())
        tool.set_context(tenant_key="tenant-1", session_key="main:tenant-1")

        result = await tool.execute(target="all_recent")
        payload = json.loads(result.content)

        self.assertEqual(payload["status"], "empty")
        self.assertEqual(payload["recent_tasks"], [])
        self.assertIsNone(payload["focus_task"])

    async def test_all_recent_filters_internal_tasks_and_uses_focus_task(self) -> None:
        tool = CheckRuntimeStatusTool(
            runtime_task_repo=_RuntimeTaskRepo(
                recent=[
                    {"task_type": "structured_memory", "status": "succeeded"},
                    {"task_type": "obligation_extract", "status": "succeeded"},
                    {"task_type": "cabinet_product_research", "status": "wait_external"},
                    {"task_type": "postprocess", "status": "succeeded"},
                    {"task_type": "deep_research", "status": "failed"},
                ]
            )
        )
        tool.set_context(tenant_key="tenant-1", session_key="main:tenant-1")

        result = await tool.execute(target="all_recent")
        payload = json.loads(result.content)

        self.assertEqual(payload["status"], "available")
        self.assertEqual(payload["focus_task"]["task_type"], "cabinet_product_research")
        self.assertEqual(payload["focus_task"]["status"], "wait_external")
        self.assertEqual(payload["user_visible_summary"], "产品资料调研还在处理中。")
        self.assertEqual(len(payload["recent_tasks"]), 2)


if __name__ == "__main__":
    unittest.main()
