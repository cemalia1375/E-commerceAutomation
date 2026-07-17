"""get_material_stats — 单条素材的累计投放数据（通过 material_usage 聚合）。"""
from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING

from simpleclaw.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from Flowcut.storage.material_repo import MaterialRepository


_DEFAULT_TENANT_KEY = "flowcut"


def _safe_div(a: float, b: float) -> float:
    return float(a) / float(b) if b else 0.0


def _isoformat(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class GetMaterialStatsTool(Tool):
    """按 material_id 查询素材的关联成片投放数据聚合。

    通过 fc_material_usage 关联到 fc_creative，SUM 其千川回流数据。
    """

    name = "get_material_stats"
    description = (
        "查询指定 material_id 素材在所有关联成片上的累计投放数据，"
        "包括用于多少条成片、累计消耗、曝光、点击、转化、ROI 指标。"
        "返回的是当前累计快照，暂不支持按日期切片。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "material_id": {
                "type": "integer",
                "description": "素材 ID",
            },
            "date_range": {
                "type": "string",
                "description": "可选时间范围。当前实现忽略，仅返回累计快照。",
            },
        },
        "required": ["material_id"],
    }
    execution_mode = "inline"
    needs_followup = True
    tool_category = "sync_read"
    read_only = True

    def __init__(self, *, material_repo: "MaterialRepository") -> None:
        self._repo = material_repo
        self._tenant_key: str = _DEFAULT_TENANT_KEY

    def set_context(self, *, tenant_key: str = "", **_: object) -> None:
        if tenant_key:
            self._tenant_key = tenant_key

    async def execute(
        self,
        material_id: int,
        date_range: str | None = None,
        **kwargs,
    ) -> ToolResult:
        del kwargs
        material = await self._repo.get(int(material_id))
        if material is None:
            return ToolResult(
                content=json.dumps(
                    {"ok": False, "error": f"素材 {material_id} 不存在"},
                    ensure_ascii=False,
                ),
                ok=False,
            )
        if material.get("tenant_key") != self._tenant_key:
            return ToolResult(
                content=json.dumps(
                    {"ok": False, "error": f"素材 {material_id} 不属于当前租户"},
                    ensure_ascii=False,
                ),
                ok=False,
            )

        agg = await self._repo.aggregate_qc_via_usage(int(material_id))
        if agg is None:
            return ToolResult(
                content=json.dumps(
                    {"ok": False, "error": f"素材 {material_id} 聚合查询失败"},
                    ensure_ascii=False,
                ),
                ok=False,
            )

        used = int(agg.get("used_in_creatives") or 0)
        cost = float(agg.get("total_cost") or 0)
        imps = int(agg.get("total_impressions") or 0)
        clicks = int(agg.get("total_clicks") or 0)
        convs = int(agg.get("total_conversions") or 0)
        last_sync = agg.get("last_synced_at")

        data = {
            "material_id": int(agg["id"]),
            "name": agg.get("name"),
            "product": agg.get("product"),
            "scene_role": agg.get("scene_role"),
            "used_in_creatives": used,
            "total_cost": cost,
            "total_impressions": imps,
            "total_clicks": clicks,
            "total_conversions": convs,
            "ctr": _safe_div(clicks, imps),
            "cvr": _safe_div(convs, clicks),
            "cpa": _safe_div(cost, convs),
            "last_synced_at": _isoformat(last_sync),
            "requested_date_range": date_range,
        }

        payload: dict = {
            "ok": True,
            "data": data,
            "source": "snapshot_only",
            "ui_hint": {
                "render_as": "stats_card",
                "title": f"素材「{data['name']}」累计投放",
            },
        }
        warnings: list[str] = []
        if used == 0:
            warnings.append("该素材尚未被用于任何成片。")
        elif last_sync is None:
            warnings.append("该素材关联的成片尚无千川回流数据。")
        if date_range:
            warnings.append(f"date_range='{date_range}' 当前未生效。")
        if warnings:
            payload["warning"] = " ".join(warnings)

        return ToolResult(
            content=json.dumps(payload, ensure_ascii=False),
            ok=True,
        )
