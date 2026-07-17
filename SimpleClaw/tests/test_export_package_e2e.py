"""ExportPackage executor 集成测试（mock OSS）。"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from simpleclaw.runtime.task_protocol import TaskEnvelope

from Flowcut.runtime.executors import make_export_package_executor


class ExportPackageExecutorTest(unittest.IsolatedAsyncioTestCase):
    async def test_export_produces_valid_zip(self) -> None:
        script_repo = AsyncMock()
        script_repo.get.return_value = {
            "id": 1,
            "source": "uploaded",
            "segments": [
                {"idx": 0, "start_time": 0, "end_time": 3, "visual": "v", "copy": "c"}
            ],
            "reference_video_id": None,
            "status": "CONFIRMED",
        }
        material_repo = AsyncMock()
        material_repo.get.return_value = {"id": 10, "oss_key": "fake/10.mp4"}
        ref_video_repo = AsyncMock()

        oss_client = MagicMock()

        def fake_download(key: str, dst: str) -> None:
            Path(dst).write_bytes(b"\x00" * 100)

        oss_client.download = fake_download
        oss_client.upload = MagicMock()
        oss_client.presigned_get_url = MagicMock(return_value="https://oss/url.zip")

        executor = make_export_package_executor(
            script_repo=script_repo,
            material_repo=material_repo,
            ref_video_repo=ref_video_repo,
            oss_client=oss_client,
        )

        task = TaskEnvelope(
            task_id="t1",
            task_type="export_package",
            tenant_key="t1",
            stream="flowcut:export_package",
            scope_key="export:1:t1",
            payload={"script_id": 1, "selections": {"0": [10]}},
        )

        result = await executor(task)

        self.assertEqual(result.status, "succeeded")
        oss_client.upload.assert_called_once()
        self.assertEqual(result.details["result_url"], "https://oss/url.zip")
        self.assertEqual(result.details["missing_materials"], [])

    async def test_export_marks_missing_materials(self) -> None:
        script_repo = AsyncMock()
        script_repo.get.return_value = {
            "id": 2,
            "source": "uploaded",
            "segments": [],
            "reference_video_id": None,
            "status": "CONFIRMED",
        }
        material_repo = AsyncMock()
        material_repo.get.return_value = None  # 找不到素材
        ref_video_repo = AsyncMock()

        oss_client = MagicMock()
        oss_client.download = MagicMock()
        oss_client.upload = MagicMock()
        oss_client.presigned_get_url = MagicMock(return_value="https://oss/x.zip")

        executor = make_export_package_executor(
            script_repo=script_repo,
            material_repo=material_repo,
            ref_video_repo=ref_video_repo,
            oss_client=oss_client,
        )

        task = TaskEnvelope(
            task_type="export_package",
            tenant_key="t1",
            stream="flowcut:export_package",
            payload={"script_id": 2, "selections": {"0": [99]}},
        )

        result = await executor(task)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.details["missing_materials"], [99])

    async def test_export_fails_when_script_missing(self) -> None:
        script_repo = AsyncMock()
        script_repo.get.return_value = None
        material_repo = AsyncMock()
        ref_video_repo = AsyncMock()
        oss_client = MagicMock()

        executor = make_export_package_executor(
            script_repo=script_repo,
            material_repo=material_repo,
            ref_video_repo=ref_video_repo,
            oss_client=oss_client,
        )

        task = TaskEnvelope(
            task_type="export_package",
            tenant_key="t1",
            stream="flowcut:export_package",
            payload={"script_id": 999, "material_ids": []},
        )

        result = await executor(task)
        self.assertEqual(result.status, "failed")
        self.assertIn("999", result.error or "")


if __name__ == "__main__":
    unittest.main()
