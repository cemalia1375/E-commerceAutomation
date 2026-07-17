"""Shared helpers for Mojing DreamSubagent tools."""

from __future__ import annotations

import json
from typing import Any

from simpleclaw.tools.base import ToolResult


def json_result(payload: dict[str, Any], *, ok: bool = True) -> ToolResult:
    return ToolResult(content=json.dumps(payload, ensure_ascii=False, default=str), ok=ok)


def ledger_to_dict(ledger: Any) -> dict[str, Any]:
    return {
        "ledger_id": ledger.ledger_id,
        "tenant_key": ledger.tenant_key,
        "session_key": ledger.session_key,
        "source": ledger.source,
        "trigger_type": ledger.trigger_type,
        "status": ledger.status,
        "dream_status": ledger.dream_status,
        "message_seq_start": ledger.message_seq_start,
        "message_seq_end": ledger.message_seq_end,
        "input_cursor": ledger.input_cursor,
        "tokens_before": ledger.tokens_before,
        "tokens_after": ledger.tokens_after,
        "dropped_count": ledger.dropped_count,
        "source_chunk": ledger.source_chunk,
        "memory_before": ledger.memory_before.to_dict() if ledger.memory_before else None,
        "memory_actions": ledger.memory_actions or [],
        "memory_after": ledger.memory_after.to_dict() if ledger.memory_after else None,
        "business_snapshot": ledger.business_snapshot,
        "metadata": ledger.metadata,
    }
