"""search_materials product 三段回退逻辑测试。

回退顺序：
  1. caller 显式传 product → 用 caller 的值
  2. 否则取 fc_script.product
  3. 都为空 → 直接报错（ToolResult.ok=False）
"""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from Flowcut.tools.search_materials import SearchMaterialsTool


def _make_tool(script_record: dict):
    script_repo = MagicMock()
    script_repo.get = AsyncMock(return_value=script_record)
    material_repo = MagicMock()
    vector_store = MagicMock()
    embedding_service = MagicMock()
    return SearchMaterialsTool(
        material_repo=material_repo,
        script_repo=script_repo,
        vector_store=vector_store,
        embedding_service=embedding_service,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_explicit_product_wins_over_script_product(monkeypatch):
    captured = {}

    async def fake_match(segments, *, tenant_key, product, **kw):
        captured["product"] = product
        return []

    monkeypatch.setattr(
        "Flowcut.tools.search_materials.match_segments_parallel", fake_match,
    )
    tool = _make_tool({
        "tenant_key": "t",
        "segments_json": json.dumps([{"idx": 0, "visual": "v", "copy": "c"}]),
        "product": "scriptproduct",
    })
    result = await tool.execute(script_id=1, product="explicit")
    assert result.ok
    assert captured["product"] == "explicit"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_falls_back_to_script_product_when_caller_omits(monkeypatch):
    captured = {}

    async def fake_match(segments, *, tenant_key, product, **kw):
        captured["product"] = product
        return []

    monkeypatch.setattr(
        "Flowcut.tools.search_materials.match_segments_parallel", fake_match,
    )
    tool = _make_tool({
        "tenant_key": "t",
        "segments_json": json.dumps([{"idx": 0, "visual": "v", "copy": "c"}]),
        "product": "scriptproduct",
    })
    result = await tool.execute(script_id=1, product="")
    assert result.ok
    assert captured["product"] == "scriptproduct"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_error_when_no_product_anywhere():
    tool = _make_tool({
        "tenant_key": "t",
        "segments_json": json.dumps([{"idx": 0, "visual": "v", "copy": "c"}]),
        "product": None,
    })
    result = await tool.execute(script_id=1, product="")
    assert not result.ok
    assert "请先" in result.content and "产品" in result.content
