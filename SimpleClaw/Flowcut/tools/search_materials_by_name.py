"""search_materials_by_name — 按名称精确（模糊）查找素材。

⚠️ 区别于 search_materials（按 script_id 向量召回）。
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from simpleclaw.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from Flowcut.storage.material_repo import MaterialRepository


_DEFAULT_TENANT_KEY = "flowcut"


class SearchMaterialsByNameTool(Tool):
    """按名称关键词查找素材库（非向量召回）。

    用于用户在 chat 里提到具体素材名时定位 material_id，
    再用 get_material_stats 查投放表现。
    """

    name = "search_materials_by_name"
    description = (
        "按素材名称关键词模糊查找素材库（可选按产品过滤）。"
        "返回 material_id 列表供 get_material_stats 查询投放数据。"
        "注意：本工具是按名称模糊匹配，不是向量召回；"
        "如需按脚本段语义召回素材请使用 search_materials。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "素材名关键词",
            },
            "product": {
                "type": "string",
                "description": "可选产品名过滤",
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

    def __init__(self, *, material_repo: "MaterialRepository") -> None:
        self._repo = material_repo
        self._tenant_key: str = _DEFAULT_TENANT_KEY

    def set_context(self, *, tenant_key: str = "", **_: object) -> None:
        if tenant_key:
            self._tenant_key = tenant_key

    async def execute(
        self,
        name: str,
        product: str | None = None,
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
        rows = await self._repo.search_by_name(
            self._tenant_key, trimmed, product or None, capped,
        )
        items = [
            {
                "material_id": int(r["id"]),
                "name": r["name"],
                "category": r.get("category"),
                "product": r.get("product"),
                "scene_role": r.get("scene_role"),
                "status": r.get("status"),
                "usage_count": int(r.get("usage_count") or 0),
            }
            for r in rows
        ]
        return ToolResult(
            content=json.dumps(
                {
                    "ok": True,
                    "data": {
                        "items": items,
                        "count": len(items),
                        "query": trimmed,
                        "product_filter": product,
                    },
                    "ui_hint": {
                        "render_as": "table",
                        "title": f"匹配「{trimmed}」的素材",
                    },
                },
                ensure_ascii=False,
            ),
            ok=True,
        )
