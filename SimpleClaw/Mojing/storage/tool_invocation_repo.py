"""MySQL storage for Agent tool invocation tracing."""

from __future__ import annotations

import json
from typing import Any

from simpleclaw.tools.invocation import ToolInvocationCompletion, ToolInvocationRecord
from Mojing.storage.database import Database


class ToolInvocationRepository:
    """Persist tool invocation linkage into nb_agent_tool_invocations."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def record_started(self, record: ToolInvocationRecord) -> None:
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO nb_agent_tool_invocations
                        (invocation_id, tenant_key, session_key, tool_call_id,
                         tool_name, tool_category, execution_mode, status,
                         input_json, output_summary, runtime_task_id,
                         business_ref_type, business_ref_id, trace_id, last_error)
                    VALUES
                        (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        status=VALUES(status),
                        updated_at=CURRENT_TIMESTAMP
                    """,
                    (
                        record.invocation_id,
                        record.tenant_key,
                        record.session_key,
                        record.tool_call_id,
                        record.tool_name,
                        record.tool_category,
                        record.execution_mode,
                        record.status,
                        _json_dumps(record.input_json),
                        record.output_summary,
                        record.runtime_task_id,
                        record.business_ref_type,
                        record.business_ref_id,
                        record.trace_id,
                        record.last_error,
                    ),
                )

    async def mark_completed(
        self,
        invocation_id: str,
        completion: ToolInvocationCompletion,
    ) -> None:
        assignments = [
            "status=%s",
            "updated_at=CURRENT_TIMESTAMP",
            "completed_at=CURRENT_TIMESTAMP",
        ]
        params: list[Any] = [completion.status]
        optional_fields = {
            "output_summary": completion.output_summary,
            "runtime_task_id": completion.runtime_task_id,
            "business_ref_type": completion.business_ref_type,
            "business_ref_id": completion.business_ref_id,
            "trace_id": completion.trace_id,
            "last_error": completion.last_error,
        }
        for column, value in optional_fields.items():
            if value is None:
                continue
            assignments.append(f"{column}=%s")
            params.append(value)
        params.append(invocation_id)

        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    UPDATE nb_agent_tool_invocations
                    SET {', '.join(assignments)}
                    WHERE invocation_id=%s
                    """,
                    tuple(params),
                )

    async def find_latest_for_tools(
        self,
        *,
        tenant_key: str,
        tool_names: tuple[str, ...],
    ) -> dict[str, Any] | None:
        tenant_key = str(tenant_key or "").strip()
        names = tuple(str(name or "").strip() for name in tool_names if str(name or "").strip())
        if not tenant_key or not names:
            return None

        placeholders = ", ".join(["%s"] * len(names))
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                cols = (
                    "invocation_id", "tenant_key", "session_key", "message_seq",
                    "tool_call_id", "tool_name", "tool_category", "execution_mode",
                    "status", "input_json", "output_summary", "runtime_task_id",
                    "business_ref_type", "business_ref_id", "trace_id", "last_error",
                    "created_at", "updated_at", "completed_at",
                )
                await cur.execute(
                    f"""
                    SELECT invocation_id, tenant_key, session_key, message_seq,
                           tool_call_id, tool_name, tool_category, execution_mode,
                           status, input_json, output_summary, runtime_task_id,
                           business_ref_type, business_ref_id, trace_id, last_error,
                           created_at, updated_at, completed_at
                    FROM nb_agent_tool_invocations
                    WHERE tenant_key=%s
                      AND tool_name IN ({placeholders})
                    ORDER BY created_at DESC, updated_at DESC
                    LIMIT 1
                    """,
                    (tenant_key, *names),
                )
                row = await cur.fetchone()
        return dict(zip(cols, row)) if row else None

    async def list_recent_for_session(
        self,
        *,
        tenant_key: str,
        session_key: str,
        since_ms: int | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        tenant_key = str(tenant_key or "").strip()
        session_key = str(session_key or "").strip()
        if not tenant_key or not session_key:
            return []
        limit = max(1, min(int(limit or 20), 100))
        where = ["tenant_key=%s", "session_key=%s"]
        params: list[Any] = [tenant_key, session_key]
        if since_ms is not None:
            where.append("created_at >= FROM_UNIXTIME(%s / 1000)")
            params.append(int(since_ms))
        params.append(limit)

        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                cols = (
                    "invocation_id", "tenant_key", "session_key", "message_seq",
                    "tool_call_id", "tool_name", "tool_category", "execution_mode",
                    "status", "input_json", "output_summary", "runtime_task_id",
                    "business_ref_type", "business_ref_id", "trace_id", "last_error",
                    "created_at", "updated_at", "completed_at",
                )
                await cur.execute(
                    f"""
                    SELECT invocation_id, tenant_key, session_key, message_seq,
                           tool_call_id, tool_name, tool_category, execution_mode,
                           status, input_json, output_summary, runtime_task_id,
                           business_ref_type, business_ref_id, trace_id, last_error,
                           created_at, updated_at, completed_at
                    FROM nb_agent_tool_invocations
                    WHERE {" AND ".join(where)}
                    ORDER BY created_at ASC, updated_at ASC
                    LIMIT %s
                    """,
                    tuple(params),
                )
                rows = await cur.fetchall()
        return [_dict_with_json(dict(zip(cols, row))) for row in rows]


def _json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _dict_with_json(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("input_json")
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = None
        row["input_json"] = parsed if isinstance(parsed, dict) else value
    return row
