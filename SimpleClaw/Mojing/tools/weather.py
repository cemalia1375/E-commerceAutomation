"""Inline weather query tool."""

from __future__ import annotations

import json
from typing import Any

from simpleclaw.tools.base import Tool, ToolResult
from Mojing.services.weather import BaiduWeatherService, FOCUSES, TIME_SCOPES


class QueryWeatherTool(Tool):
    """Query current domestic weather with a tiny model-facing surface."""

    name = "query_weather"
    description = (
        "查询用户所在地区的实时天气和基础预报。"
        "用于用户明确问天气、出门/出去玩安排、紫外线、防晒、天气对护肤的影响时。"
        "只需要传用户说的城市/区县/地区名；没有地点时先问城市。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "城市/区县/地区名，例如 北京、北京市、朝阳区、广州天河区；也可传经纬度，经度在前。",
            },
            "focus": {
                "type": "string",
                "enum": ["general", "outing", "skincare"],
                "description": "general=普通天气；outing=出门/出去玩；skincare=防晒/保湿/护肤影响。",
                "default": "general",
            },
            "time_scope": {
                "type": "string",
                "enum": ["today", "tomorrow", "next_days"],
                "description": "today=今天/当前；tomorrow=明天；next_days=未来几天。",
                "default": "today",
            },
        },
        "required": ["location"],
    }
    needs_followup = True
    tool_category = "sync_read"
    read_only = True
    risk_level = "low"

    def __init__(
        self,
        *,
        api_url: str = "",
        ak: str = "",
        timeout_s: float = 3.0,
        transport: Any | None = None,
        weather_service: BaiduWeatherService | None = None,
    ) -> None:
        self._weather_service = weather_service or BaiduWeatherService(
            api_url=api_url,
            ak=ak,
            timeout_s=timeout_s,
            transport=transport,
        )

    def cast_params(self, params: dict[str, Any]) -> dict[str, Any]:
        location = str((params or {}).get("location") or "").strip()
        focus = str((params or {}).get("focus") or "general").strip().lower()
        time_scope = str((params or {}).get("time_scope") or "today").strip().lower()
        return {
            "location": location,
            "focus": focus if focus in FOCUSES else "general",
            "time_scope": time_scope if time_scope in TIME_SCOPES else "today",
        }

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not str(params.get("location") or "").strip():
            errors.append("location is required")
        if str(params.get("focus") or "general").strip().lower() not in FOCUSES:
            errors.append("focus must be one of general/outing/skincare")
        if str(params.get("time_scope") or "today").strip().lower() not in TIME_SCOPES:
            errors.append("time_scope must be one of today/tomorrow/next_days")
        return errors

    async def execute(self, location: str = "", focus: str = "general", time_scope: str = "today") -> ToolResult:
        payload = await self._weather_service.query(
            location=location,
            focus=focus,
            time_scope=time_scope,
        )
        return ToolResult(
            content=json.dumps(payload, ensure_ascii=False, default=str),
            ok=bool(payload.get("ok")),
        )
