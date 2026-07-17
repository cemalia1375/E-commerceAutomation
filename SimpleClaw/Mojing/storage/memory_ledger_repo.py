"""MySQL persistence for memory ledger governance records."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from simpleclaw.memory.ledger import (
    MemoryLedgerDreamStatus,
    MemoryLedgerRecord,
    MemoryLedgerStatus,
    MemorySnapshot,
    now_ms,
)
from Mojing.storage.database import Database


_SELECT_COLUMNS = """
    ledger_id, tenant_key, session_key, source, trigger_type, status,
    runtime_task_id, trace_id, message_seq_start, message_seq_end,
    last_consolidated_from, last_consolidated_to, dropped_count,
    tokens_before, tokens_after, source_chunk_hash, source_chunk_json,
    memory_before_json, memory_actions_json, memory_after_json,
    business_snapshot_json, dream_status, last_error, metadata_json,
    created_at, updated_at, completed_at
"""


class MemoryLedgerRepository:
    """Persist memory ledger records in nb_memory_ledgers."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def create_ledger(self, record: MemoryLedgerRecord) -> MemoryLedgerRecord:
        now = _dt(record.created_at_ms)
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO nb_memory_ledgers
                        (ledger_id, tenant_key, session_key, source, trigger_type, status,
                         runtime_task_id, trace_id, message_seq_start, message_seq_end,
                         last_consolidated_from, last_consolidated_to, dropped_count,
                         tokens_before, tokens_after, source_chunk_hash, source_chunk_json,
                         memory_before_json, memory_actions_json, memory_after_json,
                         business_snapshot_json, dream_status, last_error, metadata_json,
                         created_at, updated_at, completed_at)
                    VALUES
                        (%s, %s, %s, %s, %s, %s,
                         %s, %s, %s, %s,
                         %s, %s, %s,
                         %s, %s, %s, %s,
                         %s, %s, %s,
                         %s, %s, %s, %s,
                         %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        updated_at=VALUES(updated_at)
                    """,
                    (
                        record.ledger_id,
                        record.tenant_key,
                        record.session_key,
                        record.source,
                        record.trigger_type,
                        record.status,
                        record.runtime_task_id,
                        record.trace_id,
                        record.message_seq_start,
                        record.message_seq_end,
                        record.last_consolidated_from,
                        record.last_consolidated_to,
                        record.dropped_count,
                        record.tokens_before,
                        record.tokens_after,
                        record.source_chunk_hash,
                        _json_or_none(record.source_chunk),
                        _snapshot_json(record.memory_before),
                        _json_or_none(record.memory_actions),
                        _snapshot_json(record.memory_after),
                        _json_or_none(record.business_snapshot),
                        record.dream_status,
                        record.last_error,
                        _json_or_none(record.metadata),
                        now,
                        now,
                        _dt(record.completed_at_ms),
                    ),
                )
        return record

    async def get_ledger(self, ledger_id: str) -> MemoryLedgerRecord | None:
        ledger_id = str(ledger_id or "").strip()
        if not ledger_id:
            return None
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT {_SELECT_COLUMNS}
                    FROM nb_memory_ledgers
                    WHERE ledger_id=%s
                    LIMIT 1
                    """,
                    (ledger_id,),
                )
                row = await cur.fetchone()
                cols = [d[0] for d in cur.description] if cur.description else []
        return _record_from_row(dict(zip(cols, row))) if row else None

    async def update_ledger(
        self,
        ledger_id: str,
        *,
        status: MemoryLedgerStatus | None = None,
        runtime_task_id: str | None = None,
        trace_id: str | None = None,
        memory_before: MemorySnapshot | None = None,
        memory_actions: list[dict[str, Any]] | None = None,
        memory_after: MemorySnapshot | None = None,
        business_snapshot: dict[str, Any] | None = None,
        dream_status: MemoryLedgerDreamStatus | None = None,
        last_error: str | None = None,
        metadata: dict[str, Any] | None = None,
        completed: bool = False,
    ) -> MemoryLedgerRecord | None:
        ledger_id = str(ledger_id or "").strip()
        if not ledger_id:
            return None
        if metadata is not None:
            current = await self.get_ledger(ledger_id)
            merged_metadata = dict(current.metadata if current is not None else {})
            merged_metadata.update(metadata)
            metadata = merged_metadata

        assignments = ["updated_at=%s"]
        params: list[Any] = [_dt(now_ms())]
        optional = {
            "status": status,
            "runtime_task_id": runtime_task_id,
            "trace_id": trace_id,
            "memory_before_json": _snapshot_json(memory_before) if memory_before is not None else None,
            "memory_actions_json": _json_or_none(memory_actions) if memory_actions is not None else None,
            "memory_after_json": _snapshot_json(memory_after) if memory_after is not None else None,
            "business_snapshot_json": _json_or_none(business_snapshot) if business_snapshot is not None else None,
            "dream_status": dream_status,
            "last_error": last_error,
            "metadata_json": _json_or_none(metadata) if metadata is not None else None,
        }
        for column, value in optional.items():
            if value is None:
                continue
            assignments.append(f"{column}=%s")
            params.append(value)
        if completed:
            assignments.append("completed_at=%s")
            params.append(_dt(now_ms()))
        params.append(ledger_id)

        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    UPDATE nb_memory_ledgers
                    SET {', '.join(assignments)}
                    WHERE ledger_id=%s
                    """,
                    tuple(params),
                )
        return await self.get_ledger(ledger_id)

    async def list_dream_pending(
        self,
        *,
        tenant_key: str | None = None,
        source: str | None = None,
        limit: int = 20,
    ) -> list[MemoryLedgerRecord]:
        where = ["dream_status='pending'"]
        params: list[Any] = []
        if tenant_key:
            where.append("tenant_key=%s")
            params.append(str(tenant_key).strip())
        if source:
            where.append("source=%s")
            params.append(str(source).strip())
        params.append(max(1, min(int(limit or 20), 200)))
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT {_SELECT_COLUMNS}
                    FROM nb_memory_ledgers
                    WHERE {' AND '.join(where)}
                    ORDER BY created_at ASC
                    LIMIT %s
                    """,
                    tuple(params),
                )
                rows = await cur.fetchall()
                cols = [d[0] for d in cur.description] if cur.description else []
        return [_record_from_row(dict(zip(cols, row))) for row in rows]


def _record_from_row(row: dict[str, Any]) -> MemoryLedgerRecord:
    return MemoryLedgerRecord(
        ledger_id=str(row.get("ledger_id") or ""),
        tenant_key=str(row.get("tenant_key") or ""),
        session_key=str(row.get("session_key") or ""),
        source=str(row.get("source") or "main"),
        trigger_type=str(row.get("trigger_type") or "context_compression"),  # type: ignore[arg-type]
        status=str(row.get("status") or "queued"),  # type: ignore[arg-type]
        runtime_task_id=row.get("runtime_task_id"),
        trace_id=row.get("trace_id"),
        message_seq_start=_int_or_none(row.get("message_seq_start")),
        message_seq_end=_int_or_none(row.get("message_seq_end")),
        last_consolidated_from=_int_or_none(row.get("last_consolidated_from")),
        last_consolidated_to=_int_or_none(row.get("last_consolidated_to")),
        dropped_count=int(row.get("dropped_count") or 0),
        tokens_before=_int_or_none(row.get("tokens_before")),
        tokens_after=_int_or_none(row.get("tokens_after")),
        source_chunk_hash=row.get("source_chunk_hash"),
        source_chunk=_decode_json(row.get("source_chunk_json")),
        memory_before=_snapshot_from_json(row.get("memory_before_json")),
        memory_actions=_decode_json(row.get("memory_actions_json")),
        memory_after=_snapshot_from_json(row.get("memory_after_json")),
        business_snapshot=_decode_json(row.get("business_snapshot_json")),
        dream_status=str(row.get("dream_status") or "pending"),  # type: ignore[arg-type]
        last_error=row.get("last_error"),
        metadata=_decode_json(row.get("metadata_json")) or {},
    )


def _snapshot_json(snapshot: MemorySnapshot | None) -> str | None:
    return _json_or_none(snapshot.to_dict()) if snapshot is not None else None


def _snapshot_from_json(value: Any) -> MemorySnapshot | None:
    data = _decode_json(value)
    if not isinstance(data, dict):
        return None
    return MemorySnapshot(
        items=list(data.get("items") or []),
        metadata=dict(data.get("metadata") or {}),
    )


def _json_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _decode_json(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return None
    return value


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except Exception:
        return None


def _dt(ms: int | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, UTC).strftime("%Y-%m-%d %H:%M:%S")
