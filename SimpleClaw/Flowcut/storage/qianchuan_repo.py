"""QianchuanRepository — fc_qianchuan_account 表的读写封装。"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from Flowcut.storage.database import Database


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class QianchuanRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def upsert_account(self, *, tenant_key: str, advertiser_id: str,
                              access_token: str, refresh_token: str,
                              access_token_expires_at: datetime,
                              refresh_token_expires_at: datetime) -> None:
        """插入或更新千川账号授权信息。"""
        now = _now()
        access_exp = access_token_expires_at.strftime("%Y-%m-%d %H:%M:%S")
        refresh_exp = refresh_token_expires_at.strftime("%Y-%m-%d %H:%M:%S")
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO fc_qianchuan_account
                        (tenant_key, advertiser_id, access_token, refresh_token,
                         access_token_expires_at, refresh_token_expires_at,
                         status, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, 'active', %s, %s)
                    ON DUPLICATE KEY UPDATE
                        access_token=VALUES(access_token),
                        refresh_token=VALUES(refresh_token),
                        access_token_expires_at=VALUES(access_token_expires_at),
                        refresh_token_expires_at=VALUES(refresh_token_expires_at),
                        updated_at=VALUES(updated_at)
                    """,
                    (tenant_key, advertiser_id, access_token, refresh_token,
                     access_exp, refresh_exp, now, now),
                )

    async def get_account(self, tenant_key: str,
                           advertiser_id: str) -> dict[str, Any] | None:
        """查询指定账号。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT * FROM fc_qianchuan_account
                    WHERE tenant_key=%s AND advertiser_id=%s
                    """,
                    (tenant_key, advertiser_id),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    async def list_accounts(self, tenant_key: str) -> list[dict[str, Any]]:
        """列出租户下所有千川账号。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT * FROM fc_qianchuan_account
                    WHERE tenant_key=%s
                    ORDER BY created_at DESC
                    """,
                    (tenant_key,),
                )
                rows = await cur.fetchall()
                cols = [d[0] for d in cur.description]

        return [dict(zip(cols, row)) for row in rows]

    async def update_tokens(self, tenant_key: str, advertiser_id: str, *,
                             access_token: str,
                             access_token_expires_at: datetime) -> None:
        """刷新 access_token 后更新。"""
        now = _now()
        access_exp = access_token_expires_at.strftime("%Y-%m-%d %H:%M:%S")
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE fc_qianchuan_account
                    SET access_token=%s, access_token_expires_at=%s, updated_at=%s
                    WHERE tenant_key=%s AND advertiser_id=%s
                    """,
                    (access_token, access_exp, now, tenant_key, advertiser_id),
                )

    async def update_campaign_id(self, tenant_key: str, advertiser_id: str, *,
                                  campaign_id: str) -> None:
        """绑定千川全域推广计划 ID。"""
        now = _now()
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE fc_qianchuan_account
                    SET campaign_id=%s, updated_at=%s
                    WHERE tenant_key=%s AND advertiser_id=%s
                    """,
                    (campaign_id, now, tenant_key, advertiser_id),
                )

    async def aggregate_account(self, tenant_key: str) -> dict[str, Any]:
        """租户级累计 SUM(fc_creative.qc_*)，结合 fc_qianchuan_orphan 给出整体投放快照。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        COUNT(*) AS creative_count,
                        COALESCE(SUM(qc_cost), 0)        AS total_cost,
                        COALESCE(SUM(qc_impressions), 0) AS total_impressions,
                        COALESCE(SUM(qc_clicks), 0)      AS total_clicks,
                        COALESCE(SUM(qc_conversions), 0) AS total_conversions,
                        MAX(qc_synced_at)                AS last_synced_at
                    FROM fc_creative
                    WHERE tenant_key=%s
                    """,
                    (tenant_key,),
                )
                row = await cur.fetchone()
        total, cost, imps, clicks, convs, last_sync = row
        return {
            "creative_count": int(total),
            "total_cost": float(cost),
            "total_impressions": int(imps),
            "total_clicks": int(clicks),
            "total_conversions": int(convs),
            "last_synced_at": last_sync.isoformat() if last_sync else None,
        }

    async def count_orphans(self, tenant_key: str) -> int:
        """统计孤儿千川素材数量（未匹配到本地 fc_creative）。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT COUNT(*) FROM fc_qianchuan_orphan WHERE tenant_key=%s",
                    (tenant_key,),
                )
                row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def upsert_orphan(
        self,
        *,
        tenant_key: str,
        qc_material_id: str,
        material_name: str | None,
        qc_cost: float | None,
        qc_conversions: int | None,
        raw_json: dict | None,
    ) -> None:
        """插入或更新孤儿千川素材记录（本地无法匹配到 fc_creative 的行）。"""
        import json as _json
        now = _now()
        raw_str = _json.dumps(raw_json, ensure_ascii=False) if raw_json else None
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO fc_qianchuan_orphan
                        (tenant_key, qc_material_id, material_name,
                         qc_cost, qc_conversions, raw_json, synced_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        material_name=VALUES(material_name),
                        qc_cost=VALUES(qc_cost),
                        qc_conversions=VALUES(qc_conversions),
                        raw_json=VALUES(raw_json),
                        synced_at=VALUES(synced_at)
                    """,
                    (tenant_key, qc_material_id, material_name,
                     qc_cost, qc_conversions, raw_str, now),
                )
