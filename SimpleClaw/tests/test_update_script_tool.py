"""UpdateScriptTool 单元测试。"""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from Flowcut.storage.script_repo import StatusConflictError
from Flowcut.tools.update_script import UpdateScriptTool


class UpdateScriptToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_update_success(self) -> None:
        repo = AsyncMock()
        repo.update_segments.return_value = None
        tool = UpdateScriptTool(script_repo=repo)

        result = await tool.execute(
            script_id=1,
            segments=[{"visual": "v", "copy": "c"}],
        )

        self.assertTrue(result.ok)
        repo.update_segments.assert_called_once()

    async def test_update_rejects_confirmed(self) -> None:
        repo = AsyncMock()
        repo.update_segments.side_effect = StatusConflictError("not DRAFT")
        tool = UpdateScriptTool(script_repo=repo)

        result = await tool.execute(
            script_id=1,
            segments=[{"visual": "v", "copy": "c"}],
        )

        self.assertFalse(result.ok)
        self.assertIn("DRAFT", result.content)


if __name__ == "__main__":
    unittest.main()
