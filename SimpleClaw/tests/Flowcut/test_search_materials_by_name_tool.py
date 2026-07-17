"""SearchMaterialsByNameTool — 精确名称搜索（区别于向量召回）。"""
from __future__ import annotations

import json

import pytest

from Flowcut.tools.search_materials_by_name import SearchMaterialsByNameTool


class _FakeMaterialRepo:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.calls: list[tuple[str, str, str | None, int]] = []

    async def search_by_name(
        self,
        tenant_key: str,
        name: str,
        product: str | None,
        limit: int,
    ) -> list[dict]:
        self.calls.append((tenant_key, name, product, limit))
        return self._rows[:limit]


def _make_tool(rows: list[dict]) -> tuple[SearchMaterialsByNameTool, _FakeMaterialRepo]:
    repo = _FakeMaterialRepo(rows)
    return SearchMaterialsByNameTool(material_repo=repo), repo  # type: ignore[arg-type]


@pytest.mark.unit
def test_name_distinct_from_vector_search_tool() -> None:
    """与现有 search_materials（向量召回）的名字必须不同。"""
    from Flowcut.tools.search_materials import SearchMaterialsTool

    by_name_tool, _ = _make_tool([])
    assert by_name_tool.name == "search_materials_by_name"
    assert SearchMaterialsTool.name == "search_materials"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_name_returns_error() -> None:
    tool, _ = _make_tool([])
    result = await tool.execute(name="  ")
    assert result.ok is False
    payload = json.loads(result.content)
    assert payload["ok"] is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_product_filter_passed_through() -> None:
    tool, repo = _make_tool([])
    tool.set_context(tenant_key="t_test")
    await tool.execute(name="口红", product="口红A", limit=5)
    assert repo.calls == [("t_test", "口红", "口红A", 5)]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_returns_summary_items() -> None:
    rows = [
        {
            "id": 1,
            "name": "口红试色",
            "category": "video",
            "product": "口红A",
            "scene_role": "演示",
            "status": "READY",
            "usage_count": 3,
        }
    ]
    tool, _ = _make_tool(rows)
    result = await tool.execute(name="口红")
    payload = json.loads(result.content)
    assert payload["data"]["count"] == 1
    item = payload["data"]["items"][0]
    assert item["material_id"] == 1
    assert item["usage_count"] == 3
    assert payload["ui_hint"]["render_as"] == "table"
