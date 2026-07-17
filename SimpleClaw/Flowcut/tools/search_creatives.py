"""search_creatives_by_name — 按关键词模糊查找成片。"""
from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING

from simpleclaw.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from Flowcut.storage.creative_repo import CreativeRepository


_DEFAULT_TENANT_KEY = "flowcut"


def _isoformat(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class SearchCreativesByNameTool(Tool):
    """按关键词模糊查找成片（通过关联爆款视频名 / 产品名）。"""

    name = "search_creatives_by_name"
    description = (
        "按关键词模糊查找成片：匹配关联的爆款视频名或产品名。"
        "用于在用户提到成片标题/产品时定位 creative_id，再用 get_creative_stats 查投放数据。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "关键词（视频名或产品名片段）",
            },
            "limit": {
                "type": "integer",
                "description": "返回上限，默认 10",
                "default": 10,
            },
        },
        "required": ["name"],
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
        name: str,
        limit: int = 10,
        **kwargs,
    ) -> ToolResult:
        del kwargs
        trimmed = (name or "").strip()
        if not trimmed:
            return ToolResult(
                content=json.dumps(
                    {"ok": False, "error": "name 不能为空"},
                    ensure_ascii=False,
                ),
                ok=False,
            )
        capped = max(1, min(int(limit or 10), 50))
        rows = await self._repo.search_by_name(self._tenant_key, trimmed, capped)
        items = [
            {
                "creative_id": int(r["id"]),
                "script_id": int(r["script_id"]) if r["script_id"] is not None else None,
                "status": r["status"],
                "ref_video_name": r.get("ref_video_name"),
                "product": r.get("product"),
                "qc_material_id": r.get("qc_material_id"),
                "qc_synced_at": _isoformat(r.get("qc_synced_at")),
                "has_qc_data": r.get("qc_synced_at") is not None,
            }
            for r in rows
        ]
        return ToolResult(
            content=json.dumps(
                {
                    "ok": True,
                    "data": {"items": items, "count": len(items), "query": trimmed},
                    "ui_hint": {
                        "render_as": "table",
                        "title": f"匹配「{trimmed}」的成片",
                    },
                },
                ensure_ascii=False,
            ),
            ok=True,
        )
