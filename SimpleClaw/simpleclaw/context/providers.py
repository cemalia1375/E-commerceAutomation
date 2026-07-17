"""Context provider protocols for the generic SimpleClaw runtime.

SimpleClaw owns the protocol and rendering rules. Applications such as Mojing
own the domain-specific providers that implement these protocols.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from simpleclaw.memory.base import Memory


Content = str | list[dict[str, Any]]
AttentionRole = Literal["system", "user"]
AttentionPlacement = Literal["before_last_user", "after_history", "tail"]
AttentionLifetime = Literal["one_turn", "until_changed", "periodic", "always"]


@dataclass(slots=True)
class ContextBuildContext:
    """Input passed to prompt, context, and attention providers."""

    history: list[Any]
    query: str = ""
    tenant_key: str = "__default__"
    cache_lane: str = "agent"
    cache_session_key: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    active_skills: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PromptSection:
    """Stable prompt section, suitable for prefix caching."""

    content: str
    source: str = "static"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ContextSection:
    """Dynamic context section for current background state."""

    content: str
    source: str = "dynamic"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AttentionPacket:
    """A structured attention unit rendered into the current model context.

    Providers should return packets instead of directly mutating prompts. This
    keeps priority, lifetime, source, and rendering policy inspectable.

    ContextBuilder renders packets provided for the current model call. For a
    long-lived builder instance it also applies lightweight lifetime filtering:
    "until_changed" is emitted when its content/metadata signature changes,
    and "periodic" is emitted on content change or at the provider-supplied
    metadata interval. Persistent cross-process policy still belongs to the
    application/provider layer.
    """

    content: Content
    source: str = "attention"
    priority: int = 100
    lifetime: AttentionLifetime = "one_turn"
    role: AttentionRole = "system"
    placement: AttentionPlacement = "before_last_user"
    metadata: dict[str, Any] = field(default_factory=dict)


class StablePromptProvider(Protocol):
    """Produces stable prompt sections for the framework prefix."""

    async def collect_stable_prompt(
        self,
        ctx: ContextBuildContext,
    ) -> list[PromptSection]: ...


class DynamicContextProvider(Protocol):
    """Produces dynamic background context for the current model call."""

    async def collect_dynamic_context(
        self,
        ctx: ContextBuildContext,
    ) -> list[ContextSection]: ...


class AttentionProvider(Protocol):
    """Produces current-turn attention packets."""

    async def collect_attention(
        self,
        ctx: ContextBuildContext,
    ) -> list[AttentionPacket]: ...


@dataclass(slots=True)
class MemoryDynamicContextProvider:
    """Dynamic context provider backed by Memory.as_section()."""

    memory: "Memory"
    top_k: int = 20
    source: str = "memory"

    async def collect_dynamic_context(
        self,
        ctx: ContextBuildContext,
    ) -> list[ContextSection]:
        section = await self.memory.as_section(query=ctx.query, top_k=self.top_k)
        if not section or not section.strip():
            return []
        return [ContextSection(content=section, source=self.source)]
