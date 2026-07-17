"""SearchCreativesByNameTool — 按关键词模糊查找成片。"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from Flowcut.tools.search_creatives import SearchCreativesByNameTool


class _FakeCreativeRepo:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.calls: list[tuple[str, str, int]] = []

    async def search_by_name(self, tenant_key: str, name: str, limit: int) -> list[dict]:
        self.calls.append((tenant_key, name, limit))
        return self._rows[:limit]


def _make_tool(rows: list[dict]) -> tuple[SearchCreativesByNameTool, _FakeCreativeRepo]:
    repo = _FakeCreativeRepo(rows)
    return SearchCreativesByNameTool(creative_repo=repo), repo  # type: ignore[arg-type]


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
async def test_basic_search_returns_summary_items() -> None:
    rows = [
        {
            "id": 101,
            "script_id": 50,
            "status": "READY",
            "qc_synced_at": datetime(2026, 5, 28, 12, 0, 0),
            "qc_material_id": "qm_1",
            "qc_cost": 10.5,
            "qc_impressions": 1000,
            "qc_clicks": 50,
            "qc_conversions": 3,
            "ref_video_name": "口红试色",
            "product": "口红",
        },
        {
            "id": 102,
            "script_id": None,
            "status": "PENDING",
            "qc_synced_at": None,
            "qc_material_id": None,
            "qc_cost": None,
            "qc_impressions": None,
            "qc_clicks": None,
            "qc_conversions": None,
            "ref_video_name": "口红开箱",
            "product": "口红",
        },
    ]
    tool, repo = _make_tool(rows)
    tool.set_context(tenant_key="t1")
    result = await tool.execute(name="口红", limit=5)
    payload = json.loads(result.content)
    assert payload["ok"] is True
    assert payload["data"]["count"] == 2
    item_a, item_b = payload["data"]["items"]
    assert item_a["creative_id"] == 101
    assert item_a["script_id"] == 50
    assert item_a["has_qc_data"] is True
    assert item_a["ref_video_name"] == "口红试色"
    assert item_b["creative_id"] == 102
    assert item_b["has_qc_data"] is False
    # repo 被调用时携带 tenant + 关键词 + 上限
    assert repo.calls == [("t1", "口红", 5)]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_limit_is_capped() -> None:
    tool, repo = _make_tool([])
    await tool.execute(name="x", limit=1000)
    assert repo.calls[0][2] == 50  # 上限 50
    await tool.execute(name="x", limit=0)
    assert repo.calls[1][2] == 10  # 0 视为未传，回落默认 10
