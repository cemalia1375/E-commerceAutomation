"""UploadScriptTool 单元测试。"""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from Flowcut.tools.upload_script import UploadScriptTool


class UploadScriptToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_upload_creates_script(self) -> None:
        script_repo = AsyncMock()
        script_repo.create.return_value = {"id": 7, "source": "uploaded"}
        tool = UploadScriptTool(script_repo=script_repo)

        result = await tool.execute(
            tenant_key="t1",
            segments=[{"visual": "v", "copy": "c"}],
        )

        self.assertTrue(result.ok)
        self.assertIn("script_id=", result.content)
        self.assertIn("7", result.content)
        script_repo.create.assert_called_once()
        call_kwargs = script_repo.create.call_args.kwargs
        self.assertEqual(call_kwargs["source"], "uploaded")
        self.assertEqual(call_kwargs["tenant_key"], "t1")
        self.assertEqual(len(call_kwargs["segments"]), 1)

    async def test_upload_rejects_empty_segments(self) -> None:
        script_repo = AsyncMock()
        tool = UploadScriptTool(script_repo=script_repo)

        result = await tool.execute(tenant_key="t1", segments=[])

        self.assertFalse(result.ok)
        script_repo.create.assert_not_called()

    async def test_upload_rejects_segment_both_empty(self) -> None:
        script_repo = AsyncMock()
        tool = UploadScriptTool(script_repo=script_repo)

        result = await tool.execute(
            tenant_key="t1",
            segments=[{"visual": "", "copy": ""}],
        )

        self.assertFalse(result.ok)
        script_repo.create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
