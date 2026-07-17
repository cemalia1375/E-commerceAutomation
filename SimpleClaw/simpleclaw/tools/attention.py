"""Attention providers for tool governance."""

from __future__ import annotations

from dataclasses import dataclass

from simpleclaw.context.providers import AttentionPacket, ContextBuildContext
from simpleclaw.tools.catalog import ToolCatalog, ToolDescriptor, ToolExposureState


@dataclass(slots=True)
class DeferredToolsAttentionProvider:
    """Expose the existence of deferred tools without exposing their schemas.

    The provider is state-driven, not intent-driven: it does not inspect the
    user query or route by keywords. It only reports that some tools are hidden
    and can be discovered through tool_search.
    """

    catalog: ToolCatalog
    exposure_state: ToolExposureState
    max_items: int = 5
    source: str = "deferred_tools_delta"
    priority: int = 90

    async def collect_attention(self, ctx: ContextBuildContext) -> list[AttentionPacket]:
        del ctx

        searchable = self._searchable_deferred_tools()
        if not searchable:
            return []

        hidden = self._hidden_deferred_tools(searchable)
        if not hidden:
            return []

        examples = searchable[: max(1, self.max_items)]
        content = _render_deferred_tools_notice(examples)
        return [
            AttentionPacket(
                content=content,
                source=self.source,
                priority=self.priority,
                lifetime="until_changed",
                placement="before_last_user",
                metadata={
                    "searchable_deferred_tools": [tool.name for tool in searchable],
                    "max_items": self.max_items,
                },
            )
        ]

    def _searchable_deferred_tools(self) -> list[ToolDescriptor]:
        descriptors: list[ToolDescriptor] = []
        for descriptor in self.catalog.descriptors():
            if descriptor.name in self.exposure_state.disabled_tools:
                continue
            if descriptor.should_defer and not descriptor.always_load:
                descriptors.append(descriptor)
        return descriptors

    def _hidden_deferred_tools(
        self,
        searchable: list[ToolDescriptor],
    ) -> list[ToolDescriptor]:
        visible_names = set(self.exposure_state.visible_tool_names(self.catalog))
        return [
            descriptor
            for descriptor in searchable
            if descriptor.name not in visible_names
        ]


def _render_deferred_tools_notice(examples: list[ToolDescriptor]) -> str:
    lines = [
        "部分工具未直接暴露给模型。如当前可见工具不足以完成任务，请先调用 `tool_search` 搜索隐藏能力。",
        "",
        "可搜索能力示例：",
    ]
    lines.extend(_tool_line(descriptor) for descriptor in examples)
    return "\n".join(lines)


def _tool_line(descriptor: ToolDescriptor) -> str:
    summary = _summary_text(descriptor)
    risk = f"risk={descriptor.risk_level}"
    approval = ", requires_approval" if descriptor.requires_approval else ""
    destructive = ", destructive" if descriptor.destructive else ""
    return f"- {descriptor.name}: {summary} ({risk}{approval}{destructive})"


def _summary_text(descriptor: ToolDescriptor) -> str:
    text = descriptor.description or descriptor.search_hint or descriptor.tool_category
    return _truncate(str(text).strip(), 80) or "hidden tool"


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."
