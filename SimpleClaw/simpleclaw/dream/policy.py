"""Admission policy for dream jobs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from simpleclaw.dream.protocol import DreamCandidate, now_ms


@dataclass(slots=True)
class DreamAdmissionContext:
    """Runtime facts used to decide whether a dream candidate can run now."""

    session_busy: bool = False
    user_ingress_pending: bool = False
    higher_priority_activation_pending: bool = False
    running_scope_keys: set[str] = field(default_factory=set)
    last_succeeded_at_ms: int | None = None
    min_interval_ms: int = 30 * 60 * 1000
    force: bool = False
    now_ms: int = field(default_factory=now_ms)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DreamAdmissionDecision:
    """Decision returned by admission control."""

    allowed: bool
    reason: str
    candidate_id: str
    scope_key: str
    notes: list[str] = field(default_factory=list)

    @classmethod
    def allow(cls, candidate: DreamCandidate, *, reason: str = "admitted") -> "DreamAdmissionDecision":
        return cls(
            allowed=True,
            reason=reason,
            candidate_id=candidate.candidate_id,
            scope_key=candidate.scope_key,
        )

    @classmethod
    def deny(
        cls,
        candidate: DreamCandidate,
        *,
        reason: str,
        notes: list[str] | None = None,
    ) -> "DreamAdmissionDecision":
        return cls(
            allowed=False,
            reason=reason,
            candidate_id=candidate.candidate_id,
            scope_key=candidate.scope_key,
            notes=list(notes or []),
        )


class DreamAdmissionPolicy:
    """Default low-priority dream admission policy.

    Dream is intentionally weaker than user messages and most system
    activations. If user work appears, dream should wait or be dropped.
    """

    def admit(
        self,
        candidate: DreamCandidate,
        ctx: DreamAdmissionContext,
    ) -> DreamAdmissionDecision:
        if ctx.force:
            return DreamAdmissionDecision.allow(candidate, reason="forced")
        if ctx.session_busy:
            return DreamAdmissionDecision.deny(candidate, reason="session_busy")
        if ctx.user_ingress_pending:
            return DreamAdmissionDecision.deny(candidate, reason="user_ingress_pending")
        if ctx.higher_priority_activation_pending:
            return DreamAdmissionDecision.deny(candidate, reason="higher_priority_activation_pending")
        if candidate.scope_key in ctx.running_scope_keys:
            return DreamAdmissionDecision.deny(candidate, reason="scope_already_running")
        if ctx.last_succeeded_at_ms is not None and ctx.min_interval_ms > 0:
            elapsed = max(0, ctx.now_ms - int(ctx.last_succeeded_at_ms))
            if elapsed < ctx.min_interval_ms:
                return DreamAdmissionDecision.deny(
                    candidate,
                    reason="cooldown",
                    notes=[f"elapsed_ms={elapsed}", f"min_interval_ms={ctx.min_interval_ms}"],
                )
        return DreamAdmissionDecision.allow(candidate)
