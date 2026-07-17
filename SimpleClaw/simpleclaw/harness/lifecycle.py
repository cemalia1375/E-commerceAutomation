"""Tool lifecycle hooks.

This module defines the pre-tool lifecycle used by the tool execution layer.
It deliberately carries no business policy; applications can implement hooks
that inspect the invocation and decide whether a tool may continue.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from simpleclaw.core.messages import ToolCall


@dataclass(slots=True)
class ToolInvocationContext:
    """Context passed to before-tool hooks."""

    call: ToolCall
    tool: Any | None
    tool_name: str
    params: dict[str, Any]
    available_tools: list[str] = field(default_factory=list)
    tenant_key: str | None = None
    session_key: str | None = None
    origin_session_key: str | None = None
    user_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolGateDecision:
    """Decision returned by a before-tool hook.

    `allowed` controls whether the tool invocation continues.
    `ok` controls how the denial is exposed as a ToolResult.

    Examples:
      allowed=False + ok=True
        Business-level denial such as deferred/deduped/prerequisite_missing.
        The tool should not execute, but the LLM should treat the result as a
        normal structured signal and continue the conversation.

      allowed=False + ok=False
        Gate/runtime failure. The tool should not execute and the LLM may treat
        the result as an execution error.
    """

    allowed: bool
    action: str = "allowed"
    reason: str = ""
    phase: str = ""
    message_focus: str = ""
    facts: dict[str, Any] = field(default_factory=dict)
    ok: bool = True

    @classmethod
    def allow(cls) -> "ToolGateDecision":
        return cls(allowed=True)

    @classmethod
    def deny(
        cls,
        *,
        action: str = "deferred",
        reason: str = "",
        phase: str = "",
        message_focus: str = "",
        facts: dict[str, Any] | None = None,
        ok: bool = True,
    ) -> "ToolGateDecision":
        return cls(
            allowed=False,
            action=action,
            reason=reason,
            phase=phase,
            message_focus=message_focus,
            facts=facts or {},
            ok=ok,
        )

    def to_payload(self, *, tool_name: str = "") -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": self.ok,
            "action": self.action,
            "allowed": self.allowed,
        }
        if tool_name:
            payload["tool"] = tool_name
        if self.reason:
            payload["reason"] = self.reason
        if self.phase:
            payload["phase"] = self.phase
        if self.message_focus:
            payload["message_focus"] = self.message_focus
        if self.facts:
            payload["facts"] = self.facts
        return payload


class BeforeToolHook(Protocol):
    """Application hook that can approve or deny a tool invocation."""

    async def before_tool(
        self,
        ctx: ToolInvocationContext,
    ) -> ToolGateDecision | None: ...


class ToolLifecycle:
    """Runs tool lifecycle hooks in order."""

    def __init__(self, before_tool_hooks: list[BeforeToolHook] | None = None) -> None:
        self._before_tool_hooks = list(before_tool_hooks or [])

    @property
    def before_tool_hooks(self) -> list[BeforeToolHook]:
        return list(self._before_tool_hooks)

    def add_before_tool_hook(self, hook: BeforeToolHook) -> None:
        self._before_tool_hooks.append(hook)

    async def before_tool(
        self,
        ctx: ToolInvocationContext,
    ) -> ToolGateDecision | None:
        for hook in self._before_tool_hooks:
            decision = await hook.before_tool(ctx)
            if decision is None:
                continue
            if not decision.allowed:
                return decision
        return None
