"""Runtime tool discovery tool."""

from __future__ import annotations

import json
from typing import Any

from simpleclaw.tools.base import Tool, ToolResult
from simpleclaw.tools.catalog import ToolCatalog, ToolDescriptor, ToolExposureState


class ToolSearchTool(Tool):
    """Search deferred tools and expose matching schemas to the model."""

    name = "tool_search"
    description = (
        "Search for currently hidden tools by capability. Use this when the "
        "needed capability is not directly available in the visible tool list."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Capability or task to search for.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of tools to return.",
                "minimum": 1,
                "maximum": 20,
                "default": 8,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    always_load = True
    should_defer = False
    tool_category = "runtime_governance"
    search_hint = "discover hidden deferred tools and expose their schemas"
    read_only = False
    destructive = False
    concurrency_safe = True
    requires_approval = False

    def __init__(self, catalog: ToolCatalog, exposure_state: ToolExposureState) -> None:
        self._catalog = catalog
        self._exposure_state = exposure_state

    def cast_params(self, params: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(params)
        normalized["query"] = str(normalized.get("query") or "").strip()
        try:
            max_results = int(normalized.get("max_results") or 8)
        except Exception:
            max_results = 8
        normalized["max_results"] = min(20, max(1, max_results))
        return normalized

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        if not str(params.get("query") or "").strip():
            return ["query is required"]
        return []

    async def execute(self, query: str, max_results: int = 8) -> ToolResult:
        candidates = [
            descriptor.name
            for descriptor in self._catalog.descriptors()
            if descriptor.name not in self._exposure_state.disabled_tools
        ]
        matches = self._catalog.search(
            query,
            max_results=max_results,
            only_deferred=True,
            candidates=candidates,
        )
        names = [descriptor.name for descriptor in matches]
        newly_discovered = self._exposure_state.discover(*names)
        return ToolResult(
            content=json.dumps(
                {
                    "ok": True,
                    "action": "tool_search",
                    "query": query,
                    "found": len(matches),
                    "discovered_tools": names,
                    "newly_discovered_tools": newly_discovered,
                    "tools": [_tool_summary(descriptor) for descriptor in matches],
                    "exposure_version": self._exposure_state.version,
                },
                ensure_ascii=False,
            )
        )


def _tool_summary(descriptor: ToolDescriptor) -> dict[str, Any]:
    return {
        "name": descriptor.name,
        "description": descriptor.description,
        "category": descriptor.tool_category,
        "risk_level": descriptor.risk_level,
        "read_only": descriptor.read_only,
        "destructive": descriptor.destructive,
        "requires_approval": descriptor.requires_approval,
    }
