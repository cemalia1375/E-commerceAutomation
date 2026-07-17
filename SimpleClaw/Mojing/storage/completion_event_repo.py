"""Persistent completion events shared by system activations and providers."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from Mojing.runtime.activations.models import ActivationRequest
from Mojing.storage.database import Database


_SELECT_COLUMNS = """
    event_id, tenant_key, session_key, task_id, task_type, activation_kind,
    status, source_session_key, business_ref_type, business_ref_id,
    summary, reminder_text, dedupe_key, payload_json, activation_ingress_id,
    consumed_by, consumed_at, created_at, updated_at
"""


class CompletionEventRepository:
    """Durable notification handoff between proactive activation and provider fallback."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert_from_activation(
        self,
        request: ActivationRequest,
        *,
        ingress_id: str | None = None,
    ) -> dict[str, Any] | None:
        tenant_key = str(request.tenant_key or "").strip()
        dedupe_key = str(request.dedupe_key or "").strip()
        task_id = str(request.task_id or "").strip()
        if not tenant_key or not dedupe_key or not task_id:
            return None
        event_id = _event_id(tenant_key, dedupe_key)
        payload = {
            **dict(request.payload_json or {}),
            "activation_kind": request.activation_kind,
            "task_id": task_id,
            "source_type": request.source_type,
            "source_id": request.effective_source_id,
            "source_session_key": request.source_session_key,
            "business_ref_type": request.business_ref_type,
            "business_ref_id": request.business_ref_id,
        }
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO nb_runtime_completion_events
                        (event_id, tenant_key, session_key, task_id, task_type,
                         activation_kind, status, source_session_key,
                         business_ref_type, business_ref_id, summary,
                         reminder_text, dedupe_key, payload_json,
                         activation_ingress_id)
                    VALUES
                        (%s, %s, %s, %s, %s, %s, 'pending', %s, %s, %s, %s,
                         %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        session_key=VALUES(session_key),
                        task_id=VALUES(task_id),
                        task_type=VALUES(task_type),
                        activation_kind=VALUES(activation_kind),
                        source_session_key=VALUES(source_session_key),
                        business_ref_type=VALUES(business_ref_type),
                        business_ref_id=VALUES(business_ref_id),
                        summary=VALUES(summary),
                        reminder_text=VALUES(reminder_text),
                        payload_json=VALUES(payload_json),
                        activation_ingress_id=COALESCE(VALUES(activation_ingress_id), activation_ingress_id),
                        status=CASE
                            WHEN status IN ('activated', 'provider_consumed') THEN status
                            ELSE 'pending'
                        END
                    """,
                    (
                        event_id,
                        tenant_key,
                        request.session_key,
                        task_id,
                        _task_type_from_activation(request.activation_kind),
                        request.activation_kind,
                        request.source_session_key,
                        request.business_ref_type,
                        request.business_ref_id,
                        request.summary,
                        request.reminder_text,
                        dedupe_key,
                        json.dumps(payload, ensure_ascii=False),
                        ingress_id,
                    ),
                )
        return await self.find_by_dedupe(tenant_key=tenant_key, dedupe_key=dedupe_key)

    async def attach_ingress(
        self,
        *,
        tenant_key: str,
        dedupe_key: str | None,
        ingress_id: str,
    ) -> None:
        if not tenant_key or not dedupe_key or not ingress_id:
            return
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE nb_runtime_completion_events
                    SET activation_ingress_id=%s
                    WHERE tenant_key=%s AND dedupe_key=%s
                    """,
                    (ingress_id, tenant_key, dedupe_key),
                )

    async def mark_consumed_by_activation(
        self,
        *,
        tenant_key: str,
        dedupe_key: str | None,
        ingress_id: str | None = None,
    ) -> None:
        await self._mark_consumed(
            tenant_key=tenant_key,
            dedupe_key=dedupe_key,
            consumed_by="system_activation",
            ingress_id=ingress_id,
        )

    async def mark_consumed_by_provider(self, *, event_id: str) -> None:
        if not event_id:
            return
        now = _now()
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE nb_runtime_completion_events
                    SET status='provider_consumed',
                        consumed_by='provider',
                        consumed_at=%s
                    WHERE event_id=%s AND status='pending'
                    """,
                    (now, event_id),
                )

    async def find_by_dedupe(
        self,
        *,
        tenant_key: str,
        dedupe_key: str,
    ) -> dict[str, Any] | None:
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT {_SELECT_COLUMNS}
                    FROM nb_runtime_completion_events
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

    async def find_oldest_pending(
        self,
        *,
        tenant_key: str,
        session_key: str | None = None,
        activation_kinds: tuple[str, ...] | list[str],
    ) -> dict[str, Any] | None:
        kinds = tuple(str(k or "").strip() for k in activation_kinds if str(k or "").strip())
        if not tenant_key or not kinds:
            return None
        where = ["tenant_key=%s", "status='pending'"]
        params: list[Any] = [tenant_key]
        if session_key:
            where.append("session_key=%s")
            params.append(session_key)
        placeholders = ",".join(["%s"] * len(kinds))
        where.append(f"activation_kind IN ({placeholders})")
        params.extend(kinds)
        sql = f"""
            SELECT {_SELECT_COLUMNS}
            FROM nb_runtime_completion_events
            WHERE {" AND ".join(where)}
            ORDER BY created_at ASC, updated_at ASC
            LIMIT 1
        """
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, tuple(params))
                row = await cur.fetchone()
                if row is None:
                    return None
                cols = [d[0] for d in cur.description]
        return _dict_from_row(dict(zip(cols, row)))

    async def _mark_consumed(
        self,
        *,
        tenant_key: str,
        dedupe_key: str | None,
        consumed_by: str,
        ingress_id: str | None = None,
    ) -> None:
        if not tenant_key or not dedupe_key:
            return
        now = _now()
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE nb_runtime_completion_events
                    SET status='activated',
                        consumed_by=%s,
                        consumed_at=%s,
                        activation_ingress_id=COALESCE(%s, activation_ingress_id)
                    WHERE tenant_key=%s
                      AND dedupe_key=%s
                      AND status='pending'
                    """,
                    (consumed_by, now, ingress_id, tenant_key, dedupe_key),
                )


def _event_id(tenant_key: str, dedupe_key: str) -> str:
    return hashlib.sha256(f"{tenant_key}:{dedupe_key}".encode("utf-8")).hexdigest()[:32]


def _task_type_from_activation(activation_kind: str) -> str | None:
    kind = str(activation_kind or "").strip()
    if kind.endswith("_completion"):
        return kind.removesuffix("_completion")
    if kind.endswith("_failure"):
        return kind.removesuffix("_failure")
    return None


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _dict_from_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    payload = out.get("payload_json")
    if isinstance(payload, dict):
        out["payload"] = payload
    else:
        try:
            parsed = json.loads(payload or "{}")
        except (TypeError, json.JSONDecodeError):
            parsed = {}
        out["payload"] = parsed if isinstance(parsed, dict) else {}
    out.pop("payload_json", None)
    for key in ("created_at", "updated_at", "consumed_at"):
        value = out.get(key)
        if isinstance(value, datetime):
            out[key] = value.strftime("%Y-%m-%d %H:%M:%S")
    return out
