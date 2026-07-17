"""Mojing dream signal builders.

These helpers keep business provenance out of the generic dream protocol while
still producing framework-level DreamSignal objects.
"""

from __future__ import annotations

from typing import Any

from simpleclaw.dream import DreamSignal


def memory_ledger_applied_signal(ledger: Any) -> DreamSignal:
    """Build a signal for a memory ledger that is ready for dream review."""

    source = str(getattr(ledger, "source", "") or "main").strip() or "main"
    ledger_id = str(getattr(ledger, "ledger_id", "") or "").strip()
    return DreamSignal(
        tenant_key=str(getattr(ledger, "tenant_key", "") or ""),
        session_key=str(getattr(ledger, "session_key", "") or "") or None,
        namespace=source,
        signal_type="memory_ledger_applied",
        reason=f"memory ledger pending dream review: {getattr(ledger, 'input_cursor', '')}",
        subject_type="memory_ledger",
        subject_id=ledger_id,
        source_type="memory_compression",
        source_id=ledger_id,
        input_cursor=str(getattr(ledger, "input_cursor", "") or "") or None,
        payload={
            "memory_ledger_id": ledger_id,
            "source": source,
            "message_seq_start": getattr(ledger, "message_seq_start", None),
            "message_seq_end": getattr(ledger, "message_seq_end", None),
        },
        read_assets=[
            "memory_ledger",
            "session_messages",
            "memory_entries",
            "runtime_tasks",
            "document_versions",
        ],
        write_assets=["dream_artifact"],
    )


def skin_diary_generated_signal(
    *,
    tenant_key: str,
    result_id: str,
    runtime_task_id: str | None = None,
    session_key: str | None = None,
    business_date: str | None = None,
) -> DreamSignal:
    """Build a signal for skin diary result consistency review."""

    effective_session = session_key or f"skin_diary:{tenant_key}"
    return DreamSignal(
        tenant_key=tenant_key,
        session_key=effective_session,
        namespace="skin_diary",
        signal_type="skin_diary_generated",
        reason="skin diary generated; review consistency with USER.md and memory",
        subject_type="skin_diary_result",
        subject_id=str(result_id or ""),
        source_type="runtime_task" if runtime_task_id else "business_event",
        source_id=str(runtime_task_id or result_id or ""),
        payload={
            "result_id": str(result_id or ""),
            "runtime_task_id": str(runtime_task_id or ""),
            "business_date": str(business_date or ""),
        },
        read_assets=[
            "skin_diary_result",
            "USER.md",
            "main_memory",
            "skin_diary_memory",
            "runtime_tasks",
        ],
        write_assets=["dream_artifact"],
        forbidden_assets=["skin_diary_result"],
    )


def deep_report_completed_signal(
    *,
    tenant_key: str,
    report_id: str,
    runtime_task_id: str | None = None,
    session_key: str | None = None,
) -> DreamSignal:
    """Build a signal for deep report consistency review."""

    effective_session = session_key or f"deep_report:{tenant_key}"
    return DreamSignal(
        tenant_key=tenant_key,
        session_key=effective_session,
        namespace="deep_report",
        signal_type="deep_report_completed",
        reason="deep report completed; review consistency with USER.md and memory",
        subject_type="deep_report",
        subject_id=str(report_id or ""),
        source_type="runtime_task" if runtime_task_id else "business_event",
        source_id=str(runtime_task_id or report_id or ""),
        payload={
            "report_id": str(report_id or ""),
            "runtime_task_id": str(runtime_task_id or ""),
        },
        read_assets=[
            "deep_report",
            "USER.md",
            "main_memory",
            "deep_report_memory",
            "runtime_tasks",
        ],
        write_assets=["dream_artifact"],
        forbidden_assets=["deep_report"],
    )
