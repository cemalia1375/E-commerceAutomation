"""Mojing before-tool gates."""

from __future__ import annotations

from loguru import logger

from simpleclaw.harness.lifecycle import ToolGateDecision, ToolInvocationContext

from Mojing.harness.readiness import (
    CapabilityDecision,
    DeepReportReadiness,
    HistoricalImageReadiness,
    SkinDiaryGenerationReadiness,
)

_DEEP_REPORT_TOOLS = {"deep_report_chat", "deep_research"}
_DEDUPED_REASONS = {
    "deep_report_running",
    "skin_diary_generation_running",
}
_EVIDENCE_TOOL = "retrieve_evidence"
_SKIN_DIARY_GENERATION_TOOL = "generate_skin_diary"
_SKIN_DIARY_HANDOFF_TOOL = "notify_skin_diary_chat"


class DeepReportGate:
    """Gate deep report delegation/generation behind readiness checks."""

    def __init__(self, readiness: DeepReportReadiness) -> None:
        self._readiness = readiness

    async def before_tool(self, ctx: ToolInvocationContext) -> ToolGateDecision | None:
        if ctx.tool_name not in _DEEP_REPORT_TOOLS:
            return None
        tenant_key = str(ctx.tenant_key or ctx.metadata.get("tenant_key") or "").strip()
        decision = await self._readiness.check_deep_report(tenant_key)
        if decision.allowed:
            return None
        return _to_gate_decision(decision)


class HistoricalImageGate:
    """Gate historical image fetches behind objective availability checks."""

    def __init__(self, readiness: HistoricalImageReadiness) -> None:
        self._readiness = readiness

    async def before_tool(self, ctx: ToolInvocationContext) -> ToolGateDecision | None:
        if ctx.tool_name != _EVIDENCE_TOOL:
            return None

        route = str(ctx.params.get("route") or "auto").strip()
        if route == "text_memory":
            return None
        if route == "auto":
            from Mojing.evidence import route_evidence_query
            media = ctx.metadata.get("media")
            routed = route_evidence_query(
                ctx.user_message or "",
                has_current_media=bool(media),
            )
            if routed.kind != "historical_image":
                return None
        elif route != "historical_image":
            return None

        tenant_key = str(ctx.tenant_key or ctx.metadata.get("tenant_key") or "").strip()
        media = ctx.metadata.get("media")
        exclude_refs = [str(ref).strip() for ref in (media or []) if str(ref or "").strip()]
        try:
            decision = await self._readiness.check_historical_image(
                tenant_key,
                exclude_refs=exclude_refs,
            )
        except Exception as exc:
            logger.warning("historical image gate lookup failed: tenant={} err={}", tenant_key, exc)
            return None
        if decision.allowed:
            return None
        return _to_gate_decision(decision)


class SkinDiaryGenerationGate:
    """Gate skin diary generation behind objective readiness checks."""

    def __init__(self, readiness: SkinDiaryGenerationReadiness) -> None:
        self._readiness = readiness

    async def before_tool(self, ctx: ToolInvocationContext) -> ToolGateDecision | None:
        if ctx.tool_name == _SKIN_DIARY_HANDOFF_TOOL:
            intent = str(ctx.params.get("intent") or "chat").strip()
            if intent != "handoff":
                return None
        elif ctx.tool_name != _SKIN_DIARY_GENERATION_TOOL:
            return None
        tenant_key = str(ctx.tenant_key or ctx.metadata.get("tenant_key") or "").strip()
        if ctx.tool_name == _SKIN_DIARY_HANDOFF_TOOL:
            decision = await self._readiness.check_skin_diary_handoff(tenant_key)
        else:
            decision = await self._readiness.check_generate_skin_diary(
                tenant_key,
                generation_input=ctx.params,
            )
        if decision.allowed:
            return None
        return _to_gate_decision(decision)


def _to_gate_decision(decision: CapabilityDecision) -> ToolGateDecision:
    action = "deduped" if decision.reason in _DEDUPED_REASONS else "deferred"
    return ToolGateDecision.deny(
        action=action,
        reason=decision.reason,
        phase=decision.phase,
        message_focus=decision.message_focus,
        facts={"capability": decision.capability},
        ok=True,
    )
