"""LLM provider cache state repositories.

These tables store provider-side runtime cache ids. They are intentionally
separate from business conversation history (`nb_sessions` /
`nb_session_messages`).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

from Mojing.storage.database import Database


@dataclass(slots=True)
class SessionCacheRecord:
    """One active Responses session cache chain/window."""

    id: int
    response_id: str
    base_response_id: str | None
    turn_count: int
    expire_at: int | None
    main_consolidated_from: int
    context_fingerprint: str | None
    metadata: dict[str, Any]


@dataclass(slots=True)
class PrefixCacheRecord:
    """One active Responses prefix cache entry."""

    id: int
    response_id: str
    expire_at: int | None
    metadata: dict[str, Any]


class LLMCacheRepository:
    """Read/write provider cache ids for prefix/session cache lanes."""

    def __init__(self, db: Database) -> None:
        self._db = db
        self._session_cache: dict[tuple[str, ...], SessionCacheRecord] = {}
        self._session_lock = asyncio.Lock()

    async def get_prefix_cache(
        self,
        *,
        provider: str,
        lane: str,
        tenant_key: str,
        session_key: str,
        model: str,
        thinking_type: str,
        prompt_fingerprint: str,
        tools_fingerprint: str,
        now_ts: int | None = None,
    ) -> PrefixCacheRecord | None:
        """Return the active provider prefix cache row, if still valid."""
        now_ts = int(now_ts if now_ts is not None else time.time())
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, response_id, expire_at, metadata_json
                    FROM nb_llm_prefix_caches
                    WHERE provider = %s
                      AND lane = %s
                      AND tenant_key = %s
                      AND session_key = %s
                      AND model = %s
                      AND thinking_type = %s
                      AND prompt_fingerprint = %s
                      AND tools_fingerprint = %s
                      AND status = 'active'
                      AND (expire_at IS NULL OR expire_at > %s)
                    LIMIT 1
                    """,
                    (
                        provider,
                        lane,
                        tenant_key,
                        session_key,
                        model,
                        thinking_type,
                        prompt_fingerprint,
                        tools_fingerprint,
                        now_ts,
                    ),
                )
                row = await cur.fetchone()
                if row is not None:
                    await cur.execute(
                        """
                        UPDATE nb_llm_prefix_caches
                        SET last_used_at = CURRENT_TIMESTAMP,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                        """,
                        (row[0],),
                    )
        if row is None:
            return None
        return PrefixCacheRecord(
            id=int(row[0]),
            response_id=str(row[1] or ""),
            expire_at=int(row[2]) if row[2] is not None else None,
            metadata=_decode_metadata(row[3]),
        )

    async def upsert_prefix_cache(
        self,
        *,
        provider: str,
        lane: str,
        tenant_key: str,
        session_key: str,
        model: str,
        thinking_type: str,
        prompt_fingerprint: str,
        tools_fingerprint: str,
        response_id: str,
        expire_at: int | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Create or update one provider prefix cache row."""
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO nb_llm_prefix_caches
                        (provider, lane, tenant_key, session_key, model, thinking_type,
                         prompt_fingerprint, tools_fingerprint, response_id,
                         expire_at, status, metadata_json, last_used_at)
                    VALUES
                        (%s, %s, %s, %s, %s, %s,
                         %s, %s, %s,
                         %s, 'active', %s, CURRENT_TIMESTAMP)
                    ON DUPLICATE KEY UPDATE
                        response_id   = VALUES(response_id),
                        expire_at     = VALUES(expire_at),
                        status        = 'active',
                        metadata_json = VALUES(metadata_json),
                        last_used_at  = CURRENT_TIMESTAMP,
                        updated_at    = CURRENT_TIMESTAMP
                    """,
                    (
                        provider,
                        lane,
                        tenant_key,
                        session_key,
                        model,
                        thinking_type,
                        prompt_fingerprint,
                        tools_fingerprint,
                        response_id,
                        expire_at,
                        metadata_json,
                    ),
                )

    async def get_session_cache(
        self,
        *,
        provider: str,
        lane: str,
        tenant_key: str,
        session_key: str,
        model: str,
        thinking_type: str,
        cache_mode: str,
        prompt_fingerprint: str,
        context_version: int,
        now_ts: int | None = None,
    ) -> SessionCacheRecord | None:
        """Return the active cache row for a no-tools session lane."""
        now_ts = int(now_ts if now_ts is not None else time.time())
        cache_key = _session_cache_key(
            provider=provider,
            lane=lane,
            tenant_key=tenant_key,
            session_key=session_key,
            model=model,
            thinking_type=thinking_type,
            cache_mode=cache_mode,
            prompt_fingerprint=prompt_fingerprint,
            context_version=context_version,
        )
        async with self._session_lock:
            cached = self._session_cache.get(cache_key)
            if cached and (cached.expire_at is None or cached.expire_at > now_ts):
                return cached
            if cached is not None:
                self._session_cache.pop(cache_key, None)

        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, response_id, base_response_id, turn_count, expire_at,
                           main_consolidated_from, context_fingerprint, metadata_json
                    FROM nb_llm_session_caches
                    WHERE provider = %s
                      AND lane = %s
                      AND tenant_key = %s
                      AND session_key = %s
                      AND model = %s
                      AND thinking_type = %s
                      AND cache_mode = %s
                      AND prompt_fingerprint = %s
                      AND context_version = %s
                      AND status = 'active'
                      AND (expire_at IS NULL OR expire_at > %s)
                    LIMIT 1
                    """,
                    (
                        provider,
                        lane,
                        tenant_key,
                        session_key,
                        model,
                        thinking_type,
                        cache_mode,
                        prompt_fingerprint,
                        context_version,
                        now_ts,
                    ),
                )
                row = await cur.fetchone()
                if row is not None:
                    await cur.execute(
                        """
                        UPDATE nb_llm_session_caches
                        SET last_used_at = CURRENT_TIMESTAMP,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = %s
                        """,
                        (row[0],),
                    )
        if row is None:
            return None
        record = SessionCacheRecord(
            id=int(row[0]),
            response_id=str(row[1] or ""),
            base_response_id=str(row[2]) if row[2] else None,
            turn_count=int(row[3] or 0),
            expire_at=int(row[4]) if row[4] is not None else None,
            main_consolidated_from=int(row[5] or 0),
            context_fingerprint=str(row[6]) if row[6] else None,
            metadata=_decode_metadata(row[7]),
        )
        async with self._session_lock:
            self._session_cache[cache_key] = record
        return record

    async def upsert_session_cache(
        self,
        *,
        provider: str,
        lane: str,
        tenant_key: str,
        session_key: str,
        model: str,
        thinking_type: str,
        cache_mode: str,
        prompt_fingerprint: str,
        context_version: int,
        main_consolidated_from: int,
        context_fingerprint: str | None,
        response_id: str,
        base_response_id: str | None,
        turn_count: int,
        expire_at: int | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Create or update the active cache row for a no-tools session lane."""
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        cache_key = _session_cache_key(
            provider=provider,
            lane=lane,
            tenant_key=tenant_key,
            session_key=session_key,
            model=model,
            thinking_type=thinking_type,
            cache_mode=cache_mode,
            prompt_fingerprint=prompt_fingerprint,
            context_version=context_version,
        )
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO nb_llm_session_caches
                        (provider, lane, tenant_key, session_key, model, thinking_type,
                         cache_mode, prompt_fingerprint, context_version,
                         main_consolidated_from, context_fingerprint, response_id,
                         base_response_id, turn_count, expire_at, status,
                         metadata_json, last_used_at)
                    VALUES
                        (%s, %s, %s, %s, %s, %s,
                         %s, %s, %s,
                         %s, %s, %s,
                         %s, %s, %s, 'active',
                         %s, CURRENT_TIMESTAMP)
                    ON DUPLICATE KEY UPDATE
                        main_consolidated_from = VALUES(main_consolidated_from),
                        context_fingerprint    = VALUES(context_fingerprint),
                        response_id            = VALUES(response_id),
                        base_response_id       = VALUES(base_response_id),
                        turn_count             = VALUES(turn_count),
                        expire_at              = VALUES(expire_at),
                        status                 = 'active',
                        metadata_json          = VALUES(metadata_json),
                        last_used_at           = CURRENT_TIMESTAMP,
                        updated_at             = CURRENT_TIMESTAMP
                    """,
                    (
                        provider,
                        lane,
                        tenant_key,
                        session_key,
                        model,
                        thinking_type,
                        cache_mode,
                        prompt_fingerprint,
                        context_version,
                        main_consolidated_from,
                        context_fingerprint,
                        response_id,
                        base_response_id,
                        turn_count,
                        expire_at,
                        metadata_json,
                    ),
                )
        async with self._session_lock:
            self._session_cache[cache_key] = SessionCacheRecord(
                id=0,
                response_id=response_id,
                base_response_id=base_response_id,
                turn_count=int(turn_count or 0),
                expire_at=expire_at,
                main_consolidated_from=int(main_consolidated_from or 0),
                context_fingerprint=context_fingerprint,
                metadata=dict(metadata or {}),
            )

    async def invalidate_session_cache(
        self,
        *,
        provider: str,
        lane: str,
        tenant_key: str,
        session_key: str,
        cache_mode: str,
    ) -> None:
        """Mark a session cache lane stale without touching business history."""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE nb_llm_session_caches
                    SET status = 'stale', updated_at = CURRENT_TIMESTAMP
                    WHERE provider = %s
                      AND lane = %s
                      AND tenant_key = %s
                      AND session_key = %s
                      AND cache_mode = %s
                      AND status = 'active'
                    """,
                    (provider, lane, tenant_key, session_key, cache_mode),
                )
        async with self._session_lock:
            stale_keys = [
                key for key in self._session_cache
                if key[0] == provider
                and key[1] == lane
                and key[2] == tenant_key
                and key[3] == session_key
                and key[6] == cache_mode
            ]
            for key in stale_keys:
                self._session_cache.pop(key, None)


def _decode_metadata(metadata_raw: Any) -> dict[str, Any]:
    if not metadata_raw:
        return {}
    try:
        if isinstance(metadata_raw, (bytes, bytearray)):
            metadata_raw = metadata_raw.decode("utf-8", errors="replace")
        metadata = json.loads(metadata_raw) if isinstance(metadata_raw, str) else metadata_raw
    except Exception:
        metadata = {}
    return metadata if isinstance(metadata, dict) else {}


def _session_cache_key(
    *,
    provider: str,
    lane: str,
    tenant_key: str,
    session_key: str,
    model: str,
    thinking_type: str,
    cache_mode: str,
    prompt_fingerprint: str,
    context_version: int,
) -> tuple[str, ...]:
    return (
        provider,
        lane,
        tenant_key,
        session_key,
        model,
        thinking_type,
        cache_mode,
        prompt_fingerprint,
        str(int(context_version or 0)),
    )
