"""Repository for delayed agent obligations.

An obligation is not a runtime task. It is a durable promise/request that can
be dispatched into a runtime task after its dependency becomes true.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from Mojing.storage.database import Database


_SELECT_COLUMNS = """
    obligation_id, tenant_key, session_key, status, action_type,
    dependency_type, payload_json, evidence_json, dispatched_task_id,
    dedupe_key, created_at, updated_at
"""


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


class ObligationRepository:
    """Thin MySQL wrapper around nb_agent_obligations."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def create_pending(
        self,
        *,
        tenant_key: str,
        session_key: str,
        action_type: str,
        dependency_type: str | None = None,
        payload: dict[str, Any] | None = None,
        evidence: dict[str, Any] | list[Any] | None = None,
        dedupe_key: str,
    ) -> dict[str, Any] | None:
        tenant_key = str(tenant_key or "").strip()
        action_type = str(action_type or "").strip()
        dedupe_key = str(dedupe_key or "").strip()
        if not tenant_key or not action_type or not dedupe_key:
            return None

        obligation_id = uuid.uuid4().hex
        now = _now()
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT IGNORE INTO nb_agent_obligations
                        (obligation_id, tenant_key, session_key, status, action_type,
                         dependency_type, payload_json, evidence_json,
                         dispatched_task_id, dedupe_key, created_at, updated_at)
                    VALUES
                        (%s, %s, %s, 'pending', %s, %s, %s, %s, NULL, %s, %s, %s)
                    """,
                    (
                        obligation_id,
                        tenant_key,
                        str(session_key or "").strip() or None,
                        action_type,
                        str(dependency_type or "").strip() or None,
                        _json_or_none(payload or {}),
                        _json_or_none(evidence or {}),
                        dedupe_key,
                        now,
                        now,
                    ),
                )
                inserted = cur.rowcount > 0
        if inserted:
            return await self.get(obligation_id)
        return await self.get_by_dedupe_key(tenant_key=tenant_key, dedupe_key=dedupe_key)

    async def get(self, obligation_id: str) -> dict[str, Any] | None:
        obligation_id = str(obligation_id or "").strip()
        if not obligation_id:
            return None
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT {_SELECT_COLUMNS}
                    FROM nb_agent_obligations
                    WHERE obligation_id=%s
                    LIMIT 1
                    """,
                    (obligation_id,),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                cols = [d[0] for d in cur.description]
        return _dict_from_row(dict(zip(cols, row)))

    async def get_by_dedupe_key(
        self,
        *,
        tenant_key: str,
        dedupe_key: str,
    ) -> dict[str, Any] | None:
        tenant_key = str(tenant_key or "").strip()
        dedupe_key = str(dedupe_key or "").strip()
        if not tenant_key or not dedupe_key:
            return None
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT {_SELECT_COLUMNS}
                    FROM nb_agent_obligations
                    WHERE tenant_key=%s AND dedupe_key=%s
                    LIMIT 1
                    """,
                    (tenant_key, dedupe_key),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                cols = [d[0] for d in cur.description]
        return _dict_from_row(dict(zip(cols, row)))

    async def list_pending_for_dependency(
        self,
        *,
        tenant_key: str,
        dependency_type: str,
        action_type: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        tenant_key = str(tenant_key or "").strip()
        dependency_type = str(dependency_type or "").strip()
        if not tenant_key or not dependency_type:
            return []
        limit = max(1, min(int(limit or 20), 100))
        params: list[Any] = [tenant_key, dependency_type]
        action_clause = ""
        if action_type:
            action_clause = " AND action_type=%s"
            params.append(str(action_type).strip())
        params.append(limit)
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT {_SELECT_COLUMNS}
                    FROM nb_agent_obligations
                    WHERE tenant_key=%s
                      AND status='pending'
                      AND dependency_type=%s
                      {action_clause}
                    ORDER BY created_at ASC
                    LIMIT %s
                    """,
                    tuple(params),
                )
                rows = await cur.fetchall()
                cols = [d[0] for d in cur.description]
        return [_dict_from_row(dict(zip(cols, row))) for row in rows]

    async def find_pending_action(
        self,
        *,
        tenant_key: str,
        session_key: str | None,
        action_type: str,
        dependency_type: str | None = None,
    ) -> dict[str, Any] | None:
        tenant_key = str(tenant_key or "").strip()
        action_type = str(action_type or "").strip()
        if not tenant_key or not action_type:
            return None
        params: list[Any] = [tenant_key, action_type]
        session_clause = ""
        if session_key:
            session_clause = " AND session_key=%s"
            params.append(str(session_key).strip())
        dependency_clause = ""
        if dependency_type:
            dependency_clause = " AND dependency_type=%s"
            params.append(str(dependency_type).strip())
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT {_SELECT_COLUMNS}
                    FROM nb_agent_obligations
                    WHERE tenant_key=%s
                      AND action_type=%s
                      AND status='pending'
                      {session_clause}
                      {dependency_clause}
                    ORDER BY created_at ASC
                    LIMIT 1
                    """,
                    tuple(params),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                cols = [d[0] for d in cur.description]
        return _dict_from_row(dict(zip(cols, row)))

    async def mark_dispatched_if_pending(
        self,
        *,
        obligation_id: str,
        dispatched_task_id: str,
    ) -> bool:
        obligation_id = str(obligation_id or "").strip()
        dispatched_task_id = str(dispatched_task_id or "").strip()
        if not obligation_id or not dispatched_task_id:
            return False
        now = _now()
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE nb_agent_obligations
                    SET status='dispatched',
                        dispatched_task_id=%s,
                        updated_at=%s
                    WHERE obligation_id=%s AND status='pending'
                    """,
                    (dispatched_task_id, now, obligation_id),
                )
                return cur.rowcount > 0

    async def revert_dispatched_to_pending(
        self,
        *,
        obligation_id: str,
        dispatched_task_id: str,
    ) -> bool:
        obligation_id = str(obligation_id or "").strip()
        dispatched_task_id = str(dispatched_task_id or "").strip()
        if not obligation_id or not dispatched_task_id:
            return False
        now = _now()
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE nb_agent_obligations
                    SET status='pending',
                        dispatched_task_id=NULL,
                        updated_at=%s
                    WHERE obligation_id=%s
                      AND status='dispatched'
                      AND dispatched_task_id=%s
                    """,
                    (now, obligation_id, dispatched_task_id),
                )
                return cur.rowcount > 0

    async def cancel_pending(
        self,
        *,
        tenant_key: str,
        session_key: str | None,
        action_type: str,
    ) -> int:
        tenant_key = str(tenant_key or "").strip()
        action_type = str(action_type or "").strip()
        if not tenant_key or not action_type:
            return 0
        now = _now()
        params: list[Any] = [now, tenant_key, action_type]
        session_clause = ""
        if session_key:
            session_clause = " AND session_key=%s"
            params.append(str(session_key).strip())
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    UPDATE nb_agent_obligations
                    SET status='cancelled',
                        updated_at=%s
                    WHERE tenant_key=%s
                      AND action_type=%s
                      AND status='pending'
                      {session_clause}
                    """,
                    tuple(params),
                )
                return int(cur.rowcount or 0)

    async def cancel_pending_obligation(self, *, obligation_id: str) -> bool:
        obligation_id = str(obligation_id or "").strip()
        if not obligation_id:
            return False
        now = _now()
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE nb_agent_obligations
                    SET status='cancelled',
                        updated_at=%s
                    WHERE obligation_id=%s
                      AND status='pending'
                    """,
                    (now, obligation_id),
                )
                return cur.rowcount > 0

    async def list_recent(
        self,
        *,
        tenant_key: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 50), 200))
        params: list[Any] = []
        where = ""
        if tenant_key:
            where = "WHERE tenant_key=%s"
            params.append(str(tenant_key).strip())
        params.append(limit)
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT {_SELECT_COLUMNS}
                    FROM nb_agent_obligations
                    {where}
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    tuple(params),
                )
                rows = await cur.fetchall()
                cols = [d[0] for d in cur.description]
        return [_dict_from_row(dict(zip(cols, row))) for row in rows]


def _dict_from_row(row: dict[str, Any]) -> dict[str, Any]:
    row["payload"] = _decode_json(row.pop("payload_json", None)) or {}
    row["evidence"] = _decode_json(row.pop("evidence_json", None)) or {}
    return row


def _json_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _decode_json(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return None
    return value
