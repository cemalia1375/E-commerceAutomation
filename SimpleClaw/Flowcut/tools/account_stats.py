"""get_account_stats — 账户级累计投放数据查询。"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from simpleclaw.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from Flowcut.storage.qianchuan_repo import QianchuanRepository


_DEFAULT_TENANT_KEY = "flowcut"


def _safe_div(a: float, b: float) -> float:
    return float(a) / float(b) if b else 0.0


class GetAccountStatsTool(Tool):
    """查询当前租户累计投放数据：消耗、曝光、点击、转化、ROI 等。"""

    name = "get_account_stats"
    description = (
        "查询当前千川账户的累计投放数据汇总，包括成片数量、总消耗、曝光、"
        "点击、转化、点击率、转化率等指标。返回的是当前累计快照，"
        "暂不支持按日期切片。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "date_range": {
                "type": "string",
                "description": "可选时间范围（如 'last_7_days'）。当前实现忽略，仅返回累计快照。",
            },
        },
        "required": [],
    }
    execution_mode = "inline"
    needs_followup = True
    tool_category = "sync_read"
    read_only = True

    def __init__(self, *, qianchuan_repo: "QianchuanRepository") -> None:
        self._repo = qianchuan_repo
        self._tenant_key: str = _DEFAULT_TENANT_KEY

    def set_context(self, *, tenant_key: str = "", **_: object) -> None:
        if tenant_key:
            self._tenant_key = tenant_key

    async def execute(self, date_range: str | None = None, **kwargs) -> ToolResult:
        del kwargs
        agg = await self._repo.aggregate_account(self._tenant_key)
        orphan_count = await self._repo.count_orphans(self._tenant_key)

        clicks = agg["total_clicks"]
        imps = agg["total_impressions"]
        convs = agg["total_conversions"]
        cost = agg["total_cost"]

        data = {
            **agg,
            "orphan_count": orphan_count,
            "ctr": _safe_div(clicks, imps),
            "cvr": _safe_div(convs, clicks),
            "cpa": _safe_div(cost, convs),
            "requested_date_range": date_range,
        }

        payload = {
            "ok": True,
            "data": data,
            "source": "snapshot_only",
            "ui_hint": {
                "render_as": "stats_card",
                "title": "账户累计投放",
            },
        }
        if date_range:
            payload["warning"] = (
                f"当前仅支持累计快照，请求的 date_range='{date_range}' 已忽略。"
            )
        if orphan_count > 0:
            existing = payload.get("warning", "")
            extra = f"另有 {orphan_count} 条千川数据未匹配到本地成片。"
            payload["warning"] = f"{existing} {extra}".strip()

        return ToolResult(
            content=json.dumps(payload, ensure_ascii=False),
            ok=True,
        )
