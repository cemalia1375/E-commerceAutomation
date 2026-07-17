"""Tenant action usage counters for durable business actions."""

from __future__ import annotations

from datetime import datetime

from Mojing.storage.database import Database


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


class ActionUsageRepository:
    """Read/write nb_tenant_action_usage aggregated counters."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def incr_submitted(self, tenant_key: str, action_key: str) -> None:
        await self._incr(tenant_key, action_key, column="submitted_count")

    async def incr_succeeded(self, tenant_key: str, action_key: str) -> None:
        await self._incr(tenant_key, action_key, column="succeeded_count")

    async def incr_failed(self, tenant_key: str, action_key: str) -> None:
        await self._incr(tenant_key, action_key, column="failed_count")

    async def get_counts(self, tenant_key: str, action_key: str) -> dict[str, int]:
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT submitted_count, succeeded_count, failed_count
                    FROM nb_tenant_action_usage
                    WHERE tenant_key=%s AND action_key=%s
                    LIMIT 1
                    """,
                    (tenant_key, action_key),
                )
                row = await cur.fetchone()
        if not row:
            return {
                "submitted_count": 0,
                "succeeded_count": 0,
                "failed_count": 0,
            }
        return {
            "submitted_count": int(row[0] or 0),
            "succeeded_count": int(row[1] or 0),
            "failed_count": int(row[2] or 0),
        }

    async def _incr(self, tenant_key: str, action_key: str, *, column: str) -> None:
        if not tenant_key or not action_key:
            return
        now = _now()
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    INSERT INTO nb_tenant_action_usage
                        (tenant_key, action_key, submitted_count, succeeded_count, failed_count, created_at, updated_at)
                    VALUES
                        (%s, %s, 0, 0, 0, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        updated_at=VALUES(updated_at)
                    """,
                    (tenant_key, action_key, now, now),
                )
                await cur.execute(
                    f"""
                    UPDATE nb_tenant_action_usage
                    SET {column} = {column} + 1,
                        updated_at = %s
                    WHERE tenant_key = %s AND action_key = %s
                    """,
                    (now, tenant_key, action_key),
                )
