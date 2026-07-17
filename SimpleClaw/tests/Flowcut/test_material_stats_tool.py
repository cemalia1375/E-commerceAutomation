"""GetMaterialStatsTool — 素材级累计投放数据查询。"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from Flowcut.tools.material_stats import GetMaterialStatsTool


class _FakeMaterialRepo:
    def __init__(
        self,
        *,
        materials: dict[int, dict | None],
        aggregates: dict[int, dict | None],
    ) -> None:
        self._materials = materials
        self._aggregates = aggregates

    async def get(self, material_id: int) -> dict | None:
        return self._materials.get(material_id)

    async def aggregate_qc_via_usage(self, material_id: int) -> dict | None:
        return self._aggregates.get(material_id)


def _make_tool(
    materials: dict[int, dict | None],
    aggregates: dict[int, dict | None],
) -> GetMaterialStatsTool:
    return GetMaterialStatsTool(
        material_repo=_FakeMaterialRepo(  # type: ignore[arg-type]
            materials=materials,
            aggregates=aggregates,
        ),
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_material_returns_error() -> None:
    tool = _make_tool({}, {})
    result = await tool.execute(material_id=999)
    assert result.ok is False
    payload = json.loads(result.content)
    assert "999" in payload["error"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cross_tenant_rejected() -> None:
    tool = _make_tool(
        materials={1: {"id": 1, "tenant_key": "other"}},
        aggregates={1: {}},
    )
    tool.set_context(tenant_key="flowcut")
    result = await tool.execute(material_id=1)
    assert result.ok is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unused_material_warning() -> None:
    tool = _make_tool(
        materials={
            1: {"id": 1, "tenant_key": "flowcut", "name": "M1"},
        },
        aggregates={
            1: {
                "id": 1,
                "name": "M1",
                "product": None,
                "scene_role": None,
                "used_in_creatives": 0,
                "total_cost": 0,
                "total_impressions": 0,
                "total_clicks": 0,
                "total_conversions": 0,
                "last_synced_at": None,
            },
        },
    )
    result = await tool.execute(material_id=1)
    payload = json.loads(result.content)
    assert payload["data"]["used_in_creatives"] == 0
    assert "尚未被用于任何成片" in payload["warning"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_used_but_no_sync_warning() -> None:
    tool = _make_tool(
        materials={
            1: {"id": 1, "tenant_key": "flowcut", "name": "M1"},
        },
        aggregates={
            1: {
                "id": 1,
                "name": "M1",
                "product": "口红",
                "scene_role": "演示",
                "used_in_creatives": 2,
                "total_cost": 0,
                "total_impressions": 0,
                "total_clicks": 0,
                "total_conversions": 0,
                "last_synced_at": None,
            },
        },
    )
    result = await tool.execute(material_id=1)
    payload = json.loads(result.content)
    assert payload["data"]["used_in_creatives"] == 2
    assert "尚无千川回流数据" in payload["warning"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_used_with_sync_correct_ratios() -> None:
    tool = _make_tool(
        materials={
            1: {"id": 1, "tenant_key": "flowcut", "name": "M1"},
        },
        aggregates={
            1: {
                "id": 1,
                "name": "M1",
                "product": "口红",
                "scene_role": "演示",
                "used_in_creatives": 3,
                "total_cost": 200.0,
                "total_impressions": 2000,
                "total_clicks": 100,
                "total_conversions": 5,
                "last_synced_at": datetime(2026, 5, 28, 12, 0, 0),
            },
        },
    )
    result = await tool.execute(material_id=1)
    payload = json.loads(result.content)
    data = payload["data"]
    assert data["used_in_creatives"] == 3
    assert data["total_cost"] == 200.0
    assert data["ctr"] == pytest.approx(0.05)
    assert data["cvr"] == pytest.approx(0.05)
    assert data["cpa"] == pytest.approx(40.0)
    assert "warning" not in payload  # 正常情况无 warning
    assert data["last_synced_at"].startswith("2026-05-28T12:00:00")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_date_range_appended_to_warning() -> None:
    tool = _make_tool(
        materials={
            1: {"id": 1, "tenant_key": "flowcut", "name": "M1"},
        },
        aggregates={
            1: {
                "id": 1,
                "name": "M1",
                "product": None,
                "scene_role": None,
                "used_in_creatives": 1,
                "total_cost": 10.0,
                "total_impressions": 100,
                "total_clicks": 10,
                "total_conversions": 1,
                "last_synced_at": datetime(2026, 5, 28),
            },
        },
    )
    result = await tool.execute(material_id=1, date_range="last_7_days")
    payload = json.loads(result.content)
    assert "last_7_days" in payload["warning"]
    assert payload["data"]["requested_date_range"] == "last_7_days"
