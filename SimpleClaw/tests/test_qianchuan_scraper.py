"""测试千川数据回流两段式对齐逻辑（mock rows，不连真千川）。

覆盖三条路径：
  1. qc_material_id 已绑定到 fc_creative → UPDATE qc_* 字段
  2. 首次同步，按文件名正则 fc-<id>- 找到 creative → 绑定 qc_material_id + UPDATE
  3. 两段都不匹配 → upsert fc_qianchuan_orphan
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from Flowcut.services.qianchuan_scraper import _extract_rows_from_captures, _parse_row
from simpleclaw.runtime.task_protocol import TaskEnvelope, TaskExecutionResult


# ── 单元测试：_parse_row ──────────────────────────────────────────────


@pytest.mark.unit
def test_parse_row_happy_path():
    row = {
        "Dimensions": {
            "material_id": {"Value": "7412345001"},
            "material_name_v2": {"Value": "fc-88-test.mp4"},
        },
        "Metrics": {
            "stat_cost_for_roi2": {"Value": 123.45},
            "total_pay_order_count_for_roi2": {"Value": 5},
        },
    }
    result = _parse_row(row)
    assert result is not None
    assert result["material_id"] == "7412345001"
    assert result["material_name"] == "fc-88-test.mp4"
    assert result["cost"] == pytest.approx(123.45)
    assert result["conversions"] == 5
    assert result["impressions"] is None
    assert result["clicks"] is None


@pytest.mark.unit
def test_parse_row_missing_material_id():
    row = {
        "Dimensions": {"material_name_v2": {"Value": "no_id.mp4"}},
        "Metrics": {},
    }
    assert _parse_row(row) is None


@pytest.mark.unit
def test_parse_row_zero_metrics():
    row = {
        "Dimensions": {"material_id": {"Value": "9999"}, "material_name_v2": {"Value": "x.mp4"}},
        "Metrics": {
            "stat_cost_for_roi2": {"Value": 0},
            "total_pay_order_count_for_roi2": {"Value": 0},
        },
    }
    result = _parse_row(row)
    assert result is not None
    assert result["cost"] == 0.0
    assert result["conversions"] == 0


# ── 单元测试：_extract_rows_from_captures ────────────────────────────


@pytest.mark.unit
def test_extract_rows_filters_non_zero_status():
    captures = [
        {"url": "https://x/statQuery", "status": 200, "body": {
            "status_code": 1,  # 非 0，应过滤
            "data": {"StatsData": {"Rows": [
                {"Dimensions": {"material_id": {"Value": "1"}}, "Metrics": {}}
            ]}}
        }},
    ]
    rows = _extract_rows_from_captures(captures)
    assert rows == []


@pytest.mark.unit
def test_extract_rows_multiple_captures():
    def _make_capture(mid: str) -> dict:
        return {
            "url": "https://x/statQuery",
            "status": 200,
            "body": {
                "status_code": 0,
                "data": {"StatsData": {"Rows": [{
                    "Dimensions": {
                        "material_id": {"Value": mid},
                        "material_name_v2": {"Value": f"fc-{mid}-v.mp4"},
                    },
                    "Metrics": {
                        "stat_cost_for_roi2": {"Value": 10.0},
                        "total_pay_order_count_for_roi2": {"Value": 2},
                    },
                }]}}
            }
        }

    rows = _extract_rows_from_captures([_make_capture("A1"), _make_capture("A2")])
    assert len(rows) == 2
    assert {r["material_id"] for r in rows} == {"A1", "A2"}


# ── 集成测试：make_qianchuan_sync_executor 两段式对齐逻辑 ─────────────


def _make_task(tenant_key: str = "flowcut") -> TaskEnvelope:
    return TaskEnvelope(
        task_type="qianchuan_sync",
        payload={"tenant_key": tenant_key},
        stream="flowcut:qianchuan_sync",
        tenant_key=tenant_key,
    )


def _make_row(material_id: str, material_name: str, cost: float = 9.9) -> dict:
    return {
        "material_id": material_id,
        "material_name": material_name,
        "cost": cost,
        "conversions": 3,
        "impressions": None,
        "clicks": None,
    }


@pytest.mark.asyncio
@pytest.mark.unit
async def test_sync_executor_matches_by_qc_material_id():
    """路径一：qc_material_id 已绑定，直接 UPDATE，不调 find_by_id_exact。"""
    from Flowcut.runtime.executors import make_qianchuan_sync_executor

    creative = {"id": 10, "qc_material_id": "MAT-001"}
    creative_repo = AsyncMock()
    creative_repo.find_by_qc_material_id.return_value = creative
    creative_repo.update_qc_stats = AsyncMock()

    qianchuan_repo = AsyncMock()
    qianchuan_repo.upsert_orphan = AsyncMock()

    rows = [_make_row("MAT-001", "fc-10-promo.mp4")]

    with patch(
        "Flowcut.services.qianchuan_scraper.fetch_video_material_stats",
        new=AsyncMock(return_value=rows),
    ):
        executor = make_qianchuan_sync_executor(
            creative_repo, qianchuan_repo,
            cdp_url="http://127.0.0.1:9222",
            tenant_key="flowcut",
        )
        # 直接注入 import，通过 patch 上游
        result = await executor(_make_task())

    assert result.status == "succeeded"
    creative_repo.update_qc_stats.assert_awaited_once_with(
        10,
        qc_material_id=None,   # 已绑定，不再覆写
        qc_cost=pytest.approx(9.9),
        qc_impressions=None,
        qc_clicks=None,
        qc_conversions=3,
    )
    qianchuan_repo.upsert_orphan.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_sync_executor_matches_by_filename_regex():
    """路径二：qc_material_id 未绑定，从文件名 fc-<id>- 提取 creative_id，首次绑定。"""
    from Flowcut.runtime.executors import make_qianchuan_sync_executor

    creative = {"id": 42, "qc_material_id": None}
    creative_repo = AsyncMock()
    creative_repo.find_by_qc_material_id.return_value = None   # 未绑定
    creative_repo.get.return_value = creative
    creative_repo.update_qc_stats = AsyncMock()

    qianchuan_repo = AsyncMock()

    rows = [_make_row("MAT-NEW", "fc-42-demo.mp4", cost=50.0)]

    with patch(
        "Flowcut.services.qianchuan_scraper.fetch_video_material_stats",
        new=AsyncMock(return_value=rows),
    ):
        executor = make_qianchuan_sync_executor(
            creative_repo, qianchuan_repo,
            cdp_url="http://127.0.0.1:9222",
            tenant_key="flowcut",
        )
        result = await executor(_make_task())

    assert result.status == "succeeded"
    creative_repo.get.assert_awaited_once_with(42)
    creative_repo.update_qc_stats.assert_awaited_once_with(
        42,
        qc_material_id="MAT-NEW",  # 首次绑定
        qc_cost=pytest.approx(50.0),
        qc_impressions=None,
        qc_clicks=None,
        qc_conversions=3,
    )
    qianchuan_repo.upsert_orphan.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_sync_executor_orphan_when_no_match():
    """路径三：两段都不匹配，写孤儿表。"""
    from Flowcut.runtime.executors import make_qianchuan_sync_executor

    creative_repo = AsyncMock()
    creative_repo.find_by_qc_material_id.return_value = None
    creative_repo.get.return_value = None

    qianchuan_repo = AsyncMock()
    qianchuan_repo.upsert_orphan = AsyncMock()

    rows = [_make_row("UNKNOWN-MAT", "some_unrelated_video.mp4", cost=7.0)]

    with patch(
        "Flowcut.services.qianchuan_scraper.fetch_video_material_stats",
        new=AsyncMock(return_value=rows),
    ):
        executor = make_qianchuan_sync_executor(
            creative_repo, qianchuan_repo,
            cdp_url="http://127.0.0.1:9222",
            tenant_key="flowcut",
        )
        result = await executor(_make_task())

    assert result.status == "succeeded"
    assert result.details["orphaned"] == 1
    assert result.details["matched"] == 0
    qianchuan_repo.upsert_orphan.assert_awaited_once()
    call_kwargs = qianchuan_repo.upsert_orphan.await_args.kwargs
    assert call_kwargs["qc_material_id"] == "UNKNOWN-MAT"
    assert call_kwargs["qc_cost"] == pytest.approx(7.0)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_sync_executor_empty_rows():
    """Scraper 返回空列表时，正常返回 succeeded 且计数为 0。"""
    from Flowcut.runtime.executors import make_qianchuan_sync_executor

    creative_repo = AsyncMock()
    qianchuan_repo = AsyncMock()

    with patch(
        "Flowcut.services.qianchuan_scraper.fetch_video_material_stats",
        new=AsyncMock(return_value=[]),
    ):
        executor = make_qianchuan_sync_executor(
            creative_repo, qianchuan_repo,
            cdp_url="http://127.0.0.1:9222",
            tenant_key="flowcut",
        )
        result = await executor(_make_task())

    assert result.status == "succeeded"
    assert "0 rows" in result.summary
