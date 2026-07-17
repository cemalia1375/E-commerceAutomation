"""Action builders for durable obligations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from simpleclaw.runtime.task_protocol import TaskEnvelope, make_trace_id
from Mojing.runtime.streams import MojingTaskStream
from Mojing.runtime.task_types import MojingTaskType


DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED = "image_analysis_succeeded"
DEPENDENCY_CABINET_PRODUCT_RESEARCH_SUCCEEDED = "cabinet_product_research_succeeded"
ACTION_GENERATE_SKIN_DIARY = "generate_skin_diary"
ACTION_GENERATE_DEEP_REPORT = "generate_deep_report"
ACTION_CONFIRM_SKINCARE_CABINET_RECORD = "confirm_skincare_cabinet_record"


@dataclass(frozen=True)
class ObligationRuntimeTask:
    task: TaskEnvelope
    summary: str


def build_obligation_runtime_task(
    obligation: dict[str, Any],
    *,
    tenant_key: str,
    source_session_key: str,
    profile_id: int | str | None,
    source_task_id: str,
    user_profile: str = "",
) -> ObligationRuntimeTask | None:
    """Build the runtime task for an obligation action."""
    action_type = str(obligation.get("action_type") or "").strip()
    if action_type == ACTION_GENERATE_SKIN_DIARY:
        return _build_skin_diary_generation_task(
            obligation,
            tenant_key=tenant_key,
            source_session_key=source_session_key,
            profile_id=profile_id,
            source_task_id=source_task_id,
        )
    if action_type == ACTION_GENERATE_DEEP_REPORT:
        return _build_deep_report_task(
            obligation,
            tenant_key=tenant_key,
            source_session_key=source_session_key,
            profile_id=profile_id,
            source_task_id=source_task_id,
            user_profile=user_profile,
        )
    if action_type == ACTION_CONFIRM_SKINCARE_CABINET_RECORD:
        return _build_cabinet_product_record_task(
            obligation,
            tenant_key=tenant_key,
            source_session_key=source_session_key,
            source_task_id=source_task_id,
        )
    return None


def _build_skin_diary_generation_task(
    obligation: dict[str, Any],
    *,
    tenant_key: str,
    source_session_key: str,
    profile_id: int | str | None,
    source_task_id: str,
) -> ObligationRuntimeTask | None:
    obligation_id = str(obligation.get("obligation_id") or "").strip()
    if not obligation_id:
        return None

    payload = dict(obligation.get("payload") or {})
    evidence = obligation.get("evidence") or {}
    evidence_text = _evidence_text(evidence)
    session_key = str(payload.get("session_key") or f"skin_diary:{tenant_key}").strip()
    generation_input = dict(payload.get("generation_input") or {})
    generation_input.setdefault("source", "mixed")
    generation_input.setdefault("evidence", evidence_text or "用户要求在图片分析完成后同步今日护肤计划。")
    generation_input.setdefault("regeneration_reason", "user_requested_after_image_analysis")
    notes = str(generation_input.get("notes") or "").strip()
    context_note = _compact_json({
        "obligation_id": obligation_id,
        "source_task_id": source_task_id,
        "profile_id": str(profile_id or ""),
    })
    generation_input["notes"] = f"{notes} {context_note}".strip()[:600]

    task_id = f"obl_{obligation_id}"[:64]
    task = TaskEnvelope(
        task_type=MojingTaskType.SKIN_DIARY_GENERATION,
        payload={
            "tenant_key": tenant_key,
            "session_key": session_key,
            "source": "obligation",
            "action_key": "skin_diary.handoff",
            "profile_id": profile_id,
            "source_task_id": source_task_id,
            "obligation_id": obligation_id,
            "query": "[系统通知] 用户之前要求图片分析完成后同步今日护肤计划，当前图片分析已完成。",
            "generation_input": generation_input,
        },
        stream=MojingTaskStream.SKIN_DIARY,
        tenant_key=tenant_key,
        session_key=session_key,
        scope_key=f"{MojingTaskType.SKIN_DIARY_GENERATION}:{tenant_key}",
        task_id=task_id,
        service_role="mojing:obligation-dispatch",
    )
    return ObligationRuntimeTask(
        task=task,
        summary="obligation dispatched skin diary generation",
    )


def _build_deep_report_task(
    obligation: dict[str, Any],
    *,
    tenant_key: str,
    source_session_key: str,
    profile_id: int | str | None,
    source_task_id: str,
    user_profile: str = "",
) -> ObligationRuntimeTask | None:
    obligation_id = str(obligation.get("obligation_id") or "").strip()
    if not obligation_id:
        return None

    payload = dict(obligation.get("payload") or {})
    evidence = obligation.get("evidence") or {}
    evidence_text = _evidence_text(evidence)
    execution_session_key = f"deep_report:{tenant_key}"
    origin_session_key = str(
        payload.get("session_id")
        or payload.get("origin_session_key")
        or source_session_key
        or f"main:{tenant_key}"
    ).strip()
    if not origin_session_key or origin_session_key.startswith("deep_report:"):
        origin_session_key = f"main:{tenant_key}"
    trace_id = make_trace_id()
    user_query = str(payload.get("user_query") or "").strip()
    if not user_query:
        user_query = evidence_text or "用户之前要求在图片分析完成后生成深度分析报告。"
    context_note = _compact_json({
        "obligation_id": obligation_id,
        "source_task_id": source_task_id,
        "profile_id": str(profile_id or ""),
    })
    user_profile = str(user_profile or "").strip()
    parts = [f"用户当前问题：{user_query}"]
    if user_profile:
        parts.append(f"用户画像：\n{user_profile}")
    parts.append(f"系统待办上下文：{context_note}")
    user_query = "\n\n".join(parts)[:4000]

    task_id = f"obl_{obligation_id}"[:64]
    task = TaskEnvelope(
        task_type=MojingTaskType.DEEP_RESEARCH,
        payload={
            "tenant_key": tenant_key,
            "source": "obligation",
            "action_key": "deep_report.handoff",
            "user_id": tenant_key,
            "session_id": origin_session_key,
            "origin_session_key": origin_session_key,
            "user_query": user_query,
            "trace_id": trace_id,
            "profile_id": profile_id,
            "source_task_id": source_task_id,
            "obligation_id": obligation_id,
        },
        stream=MojingTaskStream.DEEP_RESEARCH,
        tenant_key=tenant_key,
        session_key=execution_session_key,
        scope_key=f"{MojingTaskType.DEEP_RESEARCH}:{execution_session_key}",
        task_id=task_id,
        trace_id=trace_id,
        service_role="mojing:obligation-dispatch",
    )
    return ObligationRuntimeTask(
        task=task,
        summary="obligation dispatched deep report generation",
    )


def _build_cabinet_product_record_task(
    obligation: dict[str, Any],
    *,
    tenant_key: str,
    source_session_key: str,
    source_task_id: str,
) -> ObligationRuntimeTask | None:
    obligation_id = str(obligation.get("obligation_id") or "").strip()
    if not obligation_id:
        return None

    payload = dict(obligation.get("payload") or {})
    product_id = _int_or_none(payload.get("product_id") or payload.get("dependency_business_ref_id"))
    if product_id is None:
        return None

    usage_status = str(payload.get("usage_status") or "").strip()
    task_id = f"obl_{obligation_id}"[:64]
    task = TaskEnvelope(
        task_type=MojingTaskType.CABINET_PRODUCT_RECORD,
        payload={
            "tenant_key": tenant_key,
            "source": "obligation",
            "action_key": "skincare_cabinet.record",
            "product_id": product_id,
            "usage_status": usage_status,
            "source_task_id": source_task_id,
            "obligation_id": obligation_id,
            "origin_session_key": str(payload.get("origin_session_key") or source_session_key or "").strip(),
        },
        stream=MojingTaskStream.CABINET_PRODUCT,
        tenant_key=tenant_key,
        session_key=source_session_key,
        scope_key=f"{MojingTaskType.CABINET_PRODUCT_RECORD}:{tenant_key}:{product_id}",
        task_id=task_id,
        service_role="mojing:obligation-dispatch",
    )
    return ObligationRuntimeTask(
        task=task,
        summary="obligation dispatched skincare cabinet record",
    )


def _evidence_text(evidence: Any) -> str:
    if isinstance(evidence, dict):
        parts: list[str] = []
        for key in ("user_request", "agent_commitment", "summary"):
            value = str(evidence.get(key) or "").strip()
            if value:
                parts.append(value)
        raw_items = evidence.get("items")
        if isinstance(raw_items, list):
            parts.extend(str(item).strip() for item in raw_items if str(item).strip())
        return "；".join(parts)[:600]
    if isinstance(evidence, list):
        return "；".join(str(item).strip() for item in evidence if str(item).strip())[:600]
    return str(evidence or "").strip()[:600]


def _compact_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _int_or_none(value: Any) -> int | None:
    try:
        parsed = int(value)
    except Exception:
        return None
    return parsed if parsed > 0 else None
