"""MatchByScriptTool 单元测试。"""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from Flowcut.tools.match_by_script import MatchByScriptTool


class MatchByScriptToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_match_calls_matcher_with_script_segments(self) -> None:
        script_repo = AsyncMock()
        script_repo.get.return_value = {
            "id": 1,
            "status": "CONFIRMED",
            "segments": [
                {"idx": 0, "visual": "v", "copy": "c", "start_time": 0, "end_time": 3},
            ],
        }
        tool = MatchByScriptTool(
            script_repo=script_repo,
            embedding_service=AsyncMock(),
            vector_store=AsyncMock(),
            material_repo=AsyncMock(),
            oss_client=None,
        )

        with patch(
            "Flowcut.tools.match_by_script.match_segments_parallel",
            new=AsyncMock(
                return_value=[{
                    "seg_idx": 0, "visual": "v", "copy": "c",
                    "phase1": [], "phase2": [], "error": None,
                }]
            ),
        ) as mock_matcher:
            result = await tool.execute(script_id=1, product="P", tenant_key="t1")

        self.assertTrue(result.ok)
        mock_matcher.assert_called_once()

    async def test_match_rejects_draft_script(self) -> None:
        script_repo = AsyncMock()
        script_repo.get.return_value = {
            "id": 1,
            "status": "DRAFT",
            "segments": [],
        }
        tool = MatchByScriptTool(
            script_repo=script_repo,
            embedding_service=AsyncMock(),
            vector_store=AsyncMock(),
            material_repo=AsyncMock(),
            oss_client=None,
        )

        result = await tool.execute(script_id=1, product="P", tenant_key="t1")

        self.assertFalse(result.ok)
        self.assertIn("CONFIRMED", result.content)

    async def test_match_missing_script(self) -> None:
        script_repo = AsyncMock()
        script_repo.get.return_value = None
        tool = MatchByScriptTool(
            script_repo=script_repo,
            embedding_service=AsyncMock(),
            vector_store=AsyncMock(),
            material_repo=AsyncMock(),
            oss_client=None,
        )

        result = await tool.execute(script_id=99, product="P", tenant_key="t1")

        self.assertFalse(result.ok)
        self.assertIn("不存在", result.content)


if __name__ == "__main__":
    unittest.main()
