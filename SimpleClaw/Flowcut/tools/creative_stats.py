"""get_creative_stats — 单条成片的累计投放数据查询。"""
from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING

from simpleclaw.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from Flowcut.storage.creative_repo import CreativeRepository


_DEFAULT_TENANT_KEY = "flowcut"


def _safe_div(a: float, b: float) -> float:
    return float(a) / float(b) if b else 0.0


def _isoformat(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class GetCreativeStatsTool(Tool):
    """按 creative_id 查询单条成片的累计投放数据。"""

    name = "get_creative_stats"
    description = (
        "查询指定 creative_id 成片的累计投放数据：消耗、曝光、点击、转化、"
        "点击率、转化率、转化成本。返回的是当前累计快照，暂不支持按日期切片。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "creative_id": {
                "type": "integer",
                "description": "成片 ID",
            },
            "date_range": {
                "type": "string",
                "description": "可选时间范围。当前实现忽略，仅返回累计快照。",
            },
        },
        "required": ["creative_id"],
    }
    execution_mode = "inline"
    needs_followup = True
    tool_category = "sync_read"
    read_only = True

    def __init__(self, *, creative_repo: "CreativeRepository") -> None:
        self._repo = creative_repo
        self._tenant_key: str = _DEFAULT_TENANT_KEY

    def set_context(self, *, tenant_key: str = "", **_: object) -> None:
        if tenant_key:
            self._tenant_key = tenant_key

    async def execute(
        self,
        creative_id: int,
        date_range: str | None = None,
        **kwargs,
    ) -> ToolResult:
        del kwargs
        creative = await self._repo.get(int(creative_id))
        if creative is None:
            return ToolResult(
                content=json.dumps(
                    {"ok": False, "error": f"成片 {creative_id} 不存在"},
                    ensure_ascii=False,
                ),
                ok=False,
            )

        tenant_key_match = creative.get("tenant_key") == self._tenant_key
        if not tenant_key_match:
            return ToolResult(
                content=json.dumps(
                    {"ok": False, "error": f"成片 {creative_id} 不属于当前租户"},
                    ensure_ascii=False,
                ),
                ok=False,
            )

        synced_at = creative.get("qc_synced_at")
        cost = float(creative.get("qc_cost") or 0)
        imps = int(creative.get("qc_impressions") or 0)
        clicks = int(creative.get("qc_clicks") or 0)
        convs = int(creative.get("qc_conversions") or 0)

        data = {
            "creative_id": int(creative["id"]),
            "status": creative.get("status"),
            "qc_material_id": creative.get("qc_material_id"),
            "qc_cost": cost,
            "qc_impressions": imps,
            "qc_clicks": clicks,
            "qc_conversions": convs,
            "ctr": _safe_div(clicks, imps),
            "cvr": _safe_div(convs, clicks),
            "cpa": _safe_div(cost, convs),
            "qc_synced_at": _isoformat(synced_at),
            "requested_date_range": date_range,
        }

        payload: dict = {
            "ok": True,
            "data": data,
            "source": "snapshot_only",
            "ui_hint": {
                "render_as": "stats_card",
                "title": f"成片 #{creative_id} 累计投放",
            },
        }
        if synced_at is None:
            payload["warning"] = "该成片尚无千川回流数据。"
        if date_range:
            existing = payload.get("warning", "")
            extra = f"date_range='{date_range}' 当前未生效。"
            payload["warning"] = f"{existing} {extra}".strip()

        return ToolResult(
            content=json.dumps(payload, ensure_ascii=False),
            ok=True,
        )
