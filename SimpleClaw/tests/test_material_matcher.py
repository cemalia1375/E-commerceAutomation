"""material_matcher 双 query 行为单元测试。"""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from Flowcut.services.material_matcher import match_segment


class MaterialMatcherTest(unittest.IsolatedAsyncioTestCase):
    """验证 match_segment 对 seg.visual / seg.copy 的双 query 行为。"""

    async def test_match_segment_embeds_visual_and_copy_separately(self) -> None:
        seg = {"visual": "厨房", "copy": "洗脸的痛点"}
        embedding = AsyncMock()
        # 用文本长度作为可区分的伪向量
        embedding.embed.side_effect = lambda text: [float(len(text))]
        vector_store = AsyncMock()
        vector_store.search.return_value = []
        material_repo = AsyncMock()

        await match_segment(
            seg,
            tenant_key="t1",
            product="P",
            embedding_service=embedding,
            vector_store=vector_store,
            material_repo=material_repo,
        )

        # visual 和 copy 各 embed 一次
        self.assertEqual(embedding.embed.call_count, 2)
        embedding.embed.assert_any_call("厨房")
        embedding.embed.assert_any_call("洗脸的痛点")

        # search 收到的两个向量不同
        args, _kwargs = vector_store.search.call_args
        visual_vec, copy_vec = args[0], args[1]
        self.assertNotEqual(visual_vec, copy_vec)

    async def test_match_segment_visual_empty_uses_copy_only(self) -> None:
        seg = {"visual": "", "copy": "洗脸"}
        embedding = AsyncMock()
        embedding.embed.return_value = [0.1, 0.2]
        vector_store = AsyncMock()
        vector_store.search.return_value = []
        material_repo = AsyncMock()

        await match_segment(
            seg,
            tenant_key="t1",
            product="P",
            embedding_service=embedding,
            vector_store=vector_store,
            material_repo=material_repo,
        )

        # visual 为空，只 embed copy 一次
        self.assertEqual(embedding.embed.call_count, 1)
        embedding.embed.assert_called_with("洗脸")

    async def test_match_segment_both_empty_returns_error(self) -> None:
        seg = {"visual": "", "copy": ""}
        embedding = AsyncMock()
        vector_store = AsyncMock()
        material_repo = AsyncMock()

        result = await match_segment(
            seg,
            tenant_key="t1",
            product="P",
            embedding_service=embedding,
            vector_store=vector_store,
            material_repo=material_repo,
        )

        self.assertIsNotNone(result["error"])
        self.assertEqual(result["phase1"], [])
        embedding.embed.assert_not_called()
