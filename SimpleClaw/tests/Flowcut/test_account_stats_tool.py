"""GetAccountStatsTool — 账户级累计投放数据查询。"""
from __future__ import annotations

import json

import pytest

from Flowcut.tools.account_stats import GetAccountStatsTool


class _FakeQianchuanRepo:
    def __init__(
        self,
        *,
        agg: dict,
        orphan_count: int = 0,
        tenant_capture: list[str] | None = None,
    ) -> None:
        self._agg = agg
        self._orphan = orphan_count
        self._captures = tenant_capture if tenant_capture is not None else []

    async def aggregate_account(self, tenant_key: str) -> dict:
        self._captures.append(tenant_key)
        return dict(self._agg)

    async def count_orphans(self, tenant_key: str) -> int:
        self._captures.append(f"orphan:{tenant_key}")
        return self._orphan


def _make_tool(repo: _FakeQianchuanRepo) -> GetAccountStatsTool:
    return GetAccountStatsTool(qianchuan_repo=repo)  # type: ignore[arg-type]


@pytest.mark.unit
def test_metadata() -> None:
    tool = _make_tool(_FakeQianchuanRepo(agg={}))
    assert tool.execution_mode == "inline"
    assert tool.needs_followup is True
    assert tool.read_only is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_account_returns_zero_with_source() -> None:
    repo = _FakeQianchuanRepo(
        agg={
            "creative_count": 0,
            "total_cost": 0.0,
            "total_impressions": 0,
            "total_clicks": 0,
            "total_conversions": 0,
            "last_synced_at": None,
        },
        orphan_count=0,
    )
    tool = _make_tool(repo)
    result = await tool.execute()
    payload = json.loads(result.content)
    assert payload["ok"] is True
    assert payload["source"] == "snapshot_only"
    assert payload["data"]["creative_count"] == 0
    assert payload["data"]["orphan_count"] == 0
    assert payload["data"]["ctr"] == 0
    assert payload["data"]["cvr"] == 0
    assert payload["data"]["cpa"] == 0
    assert "warning" not in payload  # 无 orphan、无 date_range 不应有 warning


@pytest.mark.unit
@pytest.mark.asyncio
async def test_populated_account_returns_correct_ratios() -> None:
    repo = _FakeQianchuanRepo(
        agg={
            "creative_count": 4,
            "total_cost": 1000.0,
            "total_impressions": 10000,
            "total_clicks": 500,
            "total_conversions": 25,
            "last_synced_at": "2026-05-28T10:00:00",
        },
    )
    tool = _make_tool(repo)
    result = await tool.execute()
    payload = json.loads(result.content)
    data = payload["data"]
    assert data["total_cost"] == 1000.0
    assert data["ctr"] == pytest.approx(0.05)
    assert data["cvr"] == pytest.approx(0.05)
    assert data["cpa"] == pytest.approx(40.0)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_orphan_count_surfaced_in_warning() -> None:
    repo = _FakeQianchuanRepo(
        agg={
            "creative_count": 2,
            "total_cost": 100.0,
            "total_impressions": 100,
            "total_clicks": 10,
            "total_conversions": 1,
            "last_synced_at": "2026-05-28T10:00:00",
        },
        orphan_count=3,
    )
    tool = _make_tool(repo)
    result = await tool.execute()
    payload = json.loads(result.content)
    assert payload["data"]["orphan_count"] == 3
    assert "3 条千川数据" in payload["warning"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_date_range_triggers_warning() -> None:
    repo = _FakeQianchuanRepo(
        agg={
            "creative_count": 1,
            "total_cost": 1.0,
            "total_impressions": 1,
            "total_clicks": 1,
            "total_conversions": 1,
            "last_synced_at": "2026-05-28T10:00:00",
        },
    )
    tool = _make_tool(repo)
    result = await tool.execute(date_range="last_7_days")
    payload = json.loads(result.content)
    assert payload["data"]["requested_date_range"] == "last_7_days"
    assert "date_range='last_7_days'" in payload["warning"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_set_context_routes_tenant_key() -> None:
    captures: list[str] = []
    repo = _FakeQianchuanRepo(
        agg={
            "creative_count": 0,
            "total_cost": 0,
            "total_impressions": 0,
            "total_clicks": 0,
            "total_conversions": 0,
            "last_synced_at": None,
        },
        tenant_capture=captures,
    )
    tool = _make_tool(repo)
    tool.set_context(tenant_key="custom_tenant")
    await tool.execute()
    assert captures[0] == "custom_tenant"
