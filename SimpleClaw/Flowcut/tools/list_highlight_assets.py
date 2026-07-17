"""list_highlight_assets 工具：让 Agent 查询高光资产库。"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from simpleclaw.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from Flowcut.storage.highlight_asset_repo import HighlightAssetRepository


class ListHighlightAssetsTool(Tool):
    name = "list_highlight_assets"
    description = (
        "查询高光资产库中的 AI 漫剧原片和数字人承接视频。"
        "当用户提到某个剧名、原片库、数字人库、批量高光生成时，先用本工具定位资产。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "asset_type": {
                "type": "string",
                "enum": ["episode_source", "digital_human_connector"],
                "description": "资产类型：原片或数字人",
            },
            "query": {
                "type": "string",
                "description": "可选，按剧名、文件名或数字人角色做包含匹配",
            },
            "limit": {
                "type": "integer",
                "default": 200,
                "description": "返回上限，默认 200",
            },
        },
        "required": ["asset_type"],
    }
    execution_mode = "inline"
    needs_followup = True
    tool_category = "sync_read"
    read_only = True

    def __init__(self, *, highlight_asset_repo: "HighlightAssetRepository") -> None:
        self._repo = highlight_asset_repo
        self._tenant_key = "flowcut"

    def set_context(self, *, tenant_key: str = "", **_: object) -> None:
        if tenant_key:
            self._tenant_key = tenant_key

    async def execute(
        self,
        asset_type: str,
        query: str | None = None,
        limit: int = 200,
        **_: Any,
    ) -> ToolResult:
        capped = max(1, min(int(limit or 200), 500))
        rows = await self._repo.list_by_tenant(
            self._tenant_key,
            asset_type=asset_type,
            limit=500,
        )
        q = (query or "").strip().lower()
        if q:
            rows = [
                row
                for row in rows
                if q in str(row.get("name") or "").lower()
                or q in str(row.get("drama_name") or "").lower()
                or q in str(row.get("connector_role") or "").lower()
            ]
        rows = rows[:capped]
        items = [
            {
                "asset_id": int(row["id"]),
                "asset_type": row.get("asset_type"),
                "name": row.get("name"),
                "drama_name": row.get("drama_name"),
                "episode_no": row.get("episode_no"),
                "connector_role": row.get("connector_role"),
                "status": row.get("status"),
                "file_size": row.get("file_size"),
            }
            for row in rows
        ]
        return ToolResult(
            content=json.dumps(
                {
                    "ok": True,
                    "data": {
                        "items": items,
                        "count": len(items),
                        "asset_type": asset_type,
                        "query": query,
                    },
                },
                ensure_ascii=False,
            ),
            ok=True,
        )
