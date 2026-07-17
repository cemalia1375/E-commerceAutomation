"""GetCreativeStatsTool — 单条成片累计投放数据查询。"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from Flowcut.tools.creative_stats import GetCreativeStatsTool


class _FakeCreativeRepo:
    def __init__(self, records: dict[int, dict | None]) -> None:
        self._records = records

    async def get(self, creative_id: int) -> dict | None:
        return self._records.get(creative_id)


def _make_tool(records: dict[int, dict | None]) -> GetCreativeStatsTool:
    return GetCreativeStatsTool(creative_repo=_FakeCreativeRepo(records))  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_creative_returns_error() -> None:
    tool = _make_tool({})
    result = await tool.execute(creative_id=999)
    assert result.ok is False
    payload = json.loads(result.content)
    assert payload["ok"] is False
    assert "999" in payload["error"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cross_tenant_creative_rejected() -> None:
    tool = _make_tool({
        10: {
            "id": 10,
            "tenant_key": "other_tenant",
            "status": "READY",
            "qc_synced_at": None,
        }
    })
    tool.set_context(tenant_key="flowcut")
    result = await tool.execute(creative_id=10)
    assert result.ok is False
    payload = json.loads(result.content)
    assert "不属于当前租户" in payload["error"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unsynced_creative_returns_zero_with_warning() -> None:
    tool = _make_tool({
        10: {
            "id": 10,
            "tenant_key": "flowcut",
            "status": "READY",
            "qc_synced_at": None,
            "qc_cost": None,
            "qc_impressions": None,
            "qc_clicks": None,
            "qc_conversions": None,
            "qc_material_id": None,
        }
    })
    result = await tool.execute(creative_id=10)
    assert result.ok is True
    payload = json.loads(result.content)
    assert payload["data"]["qc_cost"] == 0
    assert payload["data"]["ctr"] == 0
    assert payload["source"] == "snapshot_only"
    assert "尚无千川回流数据" in payload["warning"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_synced_creative_returns_correct_ratios() -> None:
    tool = _make_tool({
        20: {
            "id": 20,
            "tenant_key": "flowcut",
            "status": "READY",
            "qc_material_id": "qm_20",
            "qc_cost": 500.0,
            "qc_impressions": 5000,
            "qc_clicks": 200,
            "qc_conversions": 10,
            "qc_synced_at": datetime(2026, 5, 28, 12, 0, 0),
        }
    })
    result = await tool.execute(creative_id=20)
    payload = json.loads(result.content)
    data = payload["data"]
    assert data["qc_cost"] == 500.0
    assert data["ctr"] == pytest.approx(0.04)
    assert data["cvr"] == pytest.approx(0.05)
    assert data["cpa"] == pytest.approx(50.0)
    assert data["qc_synced_at"].startswith("2026-05-28T12:00:00")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_date_range_appears_in_warning() -> None:
    tool = _make_tool({
        20: {
            "id": 20,
            "tenant_key": "flowcut",
            "status": "READY",
            "qc_material_id": "qm",
            "qc_cost": 1.0,
            "qc_impressions": 1,
            "qc_clicks": 1,
            "qc_conversions": 1,
            "qc_synced_at": datetime(2026, 5, 28),
        }
    })
    result = await tool.execute(creative_id=20, date_range="last_7_days")
    payload = json.loads(result.content)
    assert "last_7_days" in payload["warning"]
    assert payload["data"]["requested_date_range"] == "last_7_days"
