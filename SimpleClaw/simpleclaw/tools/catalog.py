"""Tool catalog and exposure state for runtime tool governance."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from simpleclaw.tools.base import Tool, ToolExecutionMode, ToolExposureScope, ToolRiskLevel


@dataclass(frozen=True)
class ToolDescriptor:
    """Searchable metadata snapshot for one tool."""

    name: str
    description: str
    parameters: dict
    needs_followup: bool
    execution_mode: ToolExecutionMode
    tool_category: str
    business_ref_type: str | None
    business_ref_id_field: str | None
    always_load: bool
    should_defer: bool
    search_hint: str
    risk_level: ToolRiskLevel
    read_only: bool
    destructive: bool
    concurrency_safe: bool
    requires_approval: bool
    exposure_scope: ToolExposureScope

    @classmethod
    def from_tool(cls, tool: Tool) -> "ToolDescriptor":
        return cls(
            name=tool.name,
            description=tool.description,
            parameters=tool.parameters,
            needs_followup=bool(getattr(tool, "needs_followup", True)),
            execution_mode=getattr(tool, "execution_mode", "inline"),
            tool_category=str(getattr(tool, "tool_category", "") or "sync_read"),
            business_ref_type=getattr(tool, "business_ref_type", None),
            business_ref_id_field=getattr(tool, "business_ref_id_field", None),
            always_load=bool(getattr(tool, "always_load", False)),
            should_defer=bool(getattr(tool, "should_defer", False)),
            search_hint=str(getattr(tool, "search_hint", "") or ""),
            risk_level=_risk_level(getattr(tool, "risk_level", "low")),
            read_only=bool(getattr(tool, "read_only", False)),
            destructive=bool(getattr(tool, "destructive", False)),
            concurrency_safe=bool(getattr(tool, "concurrency_safe", True)),
            requires_approval=bool(getattr(tool, "requires_approval", False)),
            exposure_scope=_exposure_scope(getattr(tool, "exposure_scope", "session")),
        )

    def to_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolCatalog:
    """Complete backend tool pool.

    Catalog is the source of truth for known tools. It does not decide which
    schemas are visible to the model in a given turn; ToolExposureState does.
    """

    def __init__(self, tools: Iterable[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        if tools:
            for tool in tools:
                self.register(tool)

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools)

    @property
    def tools(self) -> list[Tool]:
        return list(self._tools.values())

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def descriptor(self, name: str) -> ToolDescriptor | None:
        tool = self.get(name)
        return ToolDescriptor.from_tool(tool) if tool is not None else None

    def descriptors(self) -> list[ToolDescriptor]:
        return [ToolDescriptor.from_tool(tool) for tool in self._tools.values()]

    def schemas_for(self, tool_names: Iterable[str]) -> list[dict]:
        schemas: list[dict] = []
        for name in tool_names:
            descriptor = self.descriptor(name)
            if descriptor is not None:
                schemas.append(descriptor.to_schema())
        return schemas

    def search(
        self,
        query: str,
        *,
        max_results: int = 8,
        only_deferred: bool = False,
        candidates: Iterable[str] | None = None,
    ) -> list[ToolDescriptor]:
        candidate_names = list(candidates) if candidates is not None else self.tool_names
        terms = _query_terms(query)
        ranked: list[tuple[int, int, ToolDescriptor]] = []
        for index, name in enumerate(candidate_names):
            descriptor = self.descriptor(name)
            if descriptor is None:
                continue
            if only_deferred and not descriptor.should_defer:
                continue
            score = _score_descriptor(descriptor, terms)
            if score > 0:
                ranked.append((score, -index, descriptor))

        ranked.sort(reverse=True)
        limit = max(1, int(max_results or 8))
        return [descriptor for _, _, descriptor in ranked[:limit]]


@dataclass
class ToolExposureState:
    """Per runtime lane model-visible tool state."""

    exposed_tools: set[str] = field(default_factory=set)
    discovered_tools: set[str] = field(default_factory=set)
    disabled_tools: set[str] = field(default_factory=set)
    version: int = 0

    def expose(self, *tool_names: str) -> list[str]:
        return self._add(self.exposed_tools, tool_names)

    def discover(self, *tool_names: str) -> list[str]:
        return self._add(self.discovered_tools, tool_names)

    def disable(self, *tool_names: str) -> list[str]:
        return self._add(self.disabled_tools, tool_names)

    def enable(self, *tool_names: str) -> list[str]:
        changed: list[str] = []
        for name in _clean_names(tool_names):
            if name in self.disabled_tools:
                self.disabled_tools.remove(name)
                changed.append(name)
        if changed:
            self.version += 1
        return changed

    def visible_tool_names(self, catalog: ToolCatalog) -> list[str]:
        explicit = self.exposed_tools | self.discovered_tools
        visible: list[str] = []
        for descriptor in catalog.descriptors():
            if descriptor.name in self.disabled_tools:
                continue
            if descriptor.always_load or not descriptor.should_defer or descriptor.name in explicit:
                visible.append(descriptor.name)
        return visible

    def is_visible(self, tool_name: str, catalog: ToolCatalog) -> bool:
        return tool_name in set(self.visible_tool_names(catalog))

    def _add(self, target: set[str], tool_names: Iterable[str]) -> list[str]:
        changed: list[str] = []
        for name in _clean_names(tool_names):
            if name not in target:
                target.add(name)
                changed.append(name)
        if changed:
            self.version += 1
        return changed


def _clean_names(tool_names: Iterable[str]) -> list[str]:
    names: list[str] = []
    for name in tool_names:
        text = str(name or "").strip()
        if text:
            names.append(text)
    return names


def _query_terms(query: str) -> list[str]:
    text = str(query or "").strip().lower()
    if not text:
        return []
    terms = [text]
    for term in re.split(r"\s+", text):
        term = term.strip()
        if term and term not in terms:
            terms.append(term)
    return terms


def _score_descriptor(descriptor: ToolDescriptor, terms: list[str]) -> int:
    if not terms:
        return 1

    name = descriptor.name.lower()
    description = descriptor.description.lower()
    hint = descriptor.search_hint.lower()
    category = descriptor.tool_category.lower()
    score = 0

    for term in terms:
        if term == name:
            score += 100
        if name.startswith(term):
            score += 50
        if term in name:
            score += 30
        if term in hint:
            score += 25
        if term in description:
            score += 15
        if term in category:
            score += 8

    if descriptor.should_defer:
        score += 2
    return score


def _risk_level(value: object) -> ToolRiskLevel:
    text = str(value or "").strip().lower()
    if text in {"low", "medium", "high"}:
        return text  # type: ignore[return-value]
    return "low"


def _exposure_scope(value: object) -> ToolExposureScope:
    text = str(value or "").strip().lower()
    if text in {"global", "tenant", "session", "agent", "skill"}:
        return text  # type: ignore[return-value]
    return "session"
