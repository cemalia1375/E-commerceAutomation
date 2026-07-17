"""Attention providers for WorkItem governance."""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger

from simpleclaw.context.providers import AttentionPacket, ContextBuildContext
from simpleclaw.workitem.protocol import WorkEvidenceRecord, WorkItemRecord
from simpleclaw.workitem.store import WorkItemStore


@dataclass(slots=True)
class WorkItemAttentionProvider:
    """Surface active WorkItem state to the next ReAct iteration."""

    store: WorkItemStore
    max_items: int = 3
    source: str = "work_item_state"
    priority: int = 80

    async def collect_attention(self, ctx: ContextBuildContext) -> list[AttentionPacket]:
        try:
            items = await self.store.list_active_work_items(
                tenant_key=_optional_filter(ctx.tenant_key),
                session_key=_optional_filter(ctx.cache_session_key),
                limit=max(1, self.max_items),
            )
        except AttributeError:
            return []
        except Exception as exc:
            logger.warning("WorkItemAttentionProvider.list_active_work_items failed: {}", exc)
            return []
        if not items:
            return []

        evidence_by_item: dict[str, list[WorkEvidenceRecord]] = {}
        for item in items:
            try:
                evidence_by_item[item.work_item_id] = await self.store.list_evidence(
                    item.work_item_id,
                    limit=3,
                )
            except Exception as exc:
                logger.warning("WorkItemAttentionProvider.list_evidence failed: {}", exc)
                evidence_by_item[item.work_item_id] = []

        return [
            AttentionPacket(
                content=_render_work_item_notice(items, evidence_by_item),
                source=self.source,
                priority=self.priority,
                lifetime="until_changed",
                placement="before_last_user",
                metadata={
                    "work_items": [
                        _work_item_signature(item, evidence_by_item.get(item.work_item_id, []))
                        for item in items
                    ],
                },
            )
        ]


def _render_work_item_notice(
    items: list[WorkItemRecord],
    evidence_by_item: dict[str, list[WorkEvidenceRecord]],
) -> str:
    lines = ["当前任务状态："]
    for item in items:
        lines.append(_work_item_line(item))
        evidence = evidence_by_item.get(item.work_item_id, [])
        if evidence:
            lines.append(f"  Evidence: {_evidence_line(evidence[0])}")
    return "\n".join(lines)


def _work_item_line(item: WorkItemRecord) -> str:
    summary = f" - {item.current_summary}" if item.current_summary else ""
    return (
        f"- {item.title}({item.work_item_id}) "
        f"status={item.status}, goal={item.goal}{summary}"
    )


def _evidence_line(evidence: WorkEvidenceRecord) -> str:
    ref = ""
    if evidence.business_ref_type and evidence.business_ref_id:
        ref = f", {evidence.business_ref_type}={evidence.business_ref_id}"
    elif evidence.business_ref_id:
        ref = f", business_ref_id={evidence.business_ref_id}"
    return f"{evidence.evidence_type}{ref}, {evidence.summary}"


def _work_item_signature(
    item: WorkItemRecord,
    evidence: list[WorkEvidenceRecord],
) -> dict:
    return {
        "work_item_id": item.work_item_id,
        "status": item.status,
        "updated_at_ms": item.updated_at_ms,
        "current_summary": item.current_summary,
        "evidence": [
            {
                "evidence_id": ev.evidence_id,
                "business_ref_type": ev.business_ref_type,
                "business_ref_id": ev.business_ref_id,
            }
            for ev in evidence
        ],
    }


def _optional_filter(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text or text == "__default__":
        return None
    return text
