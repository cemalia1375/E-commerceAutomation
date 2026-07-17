"""navigate_to 工具：让 agent 主动触发前端页面跳转。"""
from __future__ import annotations

import json
import re
from typing import Any

from simpleclaw.tools.base import Tool, ToolResult

# 与前端 ALLOWED_ROUTE_PATTERNS 保持一致
_ALLOWED_PATTERNS: list[re.Pattern] = [
    re.compile(r"^/$"),
    re.compile(r"^/material(?:\?.*)?$"),
    re.compile(r"^/creative(?:\?.*)?$"),
    re.compile(r"^/workspace/[^/?]+(?:\?.*)?$"),
    re.compile(r"^/dashboard(?:\?.*)?$"),
]

# 占位符路由（含 :param）的模板匹配
_TEMPLATE_PATTERNS: list[re.Pattern] = [
    re.compile(r"^/workspace/:[^/?]+$"),
]


def _is_allowed(route: str) -> bool:
    return any(p.match(route) for p in _ALLOWED_PATTERNS)


def _fill_params(route: str, params: dict[str, Any]) -> str:
    """Fill route parameters with validation to prevent path traversal.

    Raises ValueError if any parameter value contains invalid characters.
    Only alphanumeric, hyphens, and underscores are allowed.
    """
    def replace(m: re.Match) -> str:
        key = m.group(1)
        if key not in params:
            return m.group(0)
        value = str(params[key])
        # Validate: only allow alphanumeric, hyphens, underscores
        if not re.match(r"^[a-zA-Z0-9_-]+$", value):
            raise ValueError(f"Invalid character in param '{key}': {value}")
        return value
    return re.sub(r":(\w+)", replace, route)


class NavigateToTool(Tool):
    name = "navigate_to"
    description = (
        "主动引导用户跳转到指定前端页面。调用前必须先输出一句引导语。"
        "可用路由：/（首页）、/material（素材库）、/creative（成片库）、"
        "/workspace/:scriptId（脚本工作台）、/dashboard（数据看板）。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "route": {
                "type": "string",
                "description": "目标路由，如 /creative 或 /workspace/:scriptId",
            },
            "params": {
                "type": "object",
                "description": "路由参数，如 {\"scriptId\": 123}",
            },
            "mode": {
                "type": "string",
                "enum": ["push", "replace"],
                "description": "跳转模式，默认 push",
            },
        },
        "required": ["route"],
    }
    needs_followup = False
    tool_category = "navigate"
    read_only = True

    async def execute(
        self,
        *,
        route: str,
        params: dict[str, Any] | None = None,
        mode: str = "push",
        **_: Any,
    ) -> ToolResult:
        params = params or {}
        try:
            filled = _fill_params(route, params)
        except ValueError:
            return ToolResult(
                content=json.dumps(
                    {"ok": False, "error": "参数值含非法字符"},
                    ensure_ascii=False,
                ),
                ok=False,
            )
        if not _is_allowed(filled):
            return ToolResult(
                content=json.dumps(
                    {"ok": False, "error": f"不允许跳转到路由：{filled}"},
                    ensure_ascii=False,
                ),
                ok=False,
            )
        return ToolResult(
            content=json.dumps(
                {
                    "ok": True,
                    "navigate": {"route": filled, "params": params, "mode": mode},
                },
                ensure_ascii=False,
            ),
            ok=True,
        )
