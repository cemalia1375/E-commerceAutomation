"""TenantStateRepository — 读写 nb_tenant_state.journey_json。

只关心 journey 状态：stage（当前阶段）+ milestones（已完成里程碑）。
其余字段（heartbeat、cron 等）当前阶段不实现，留空。

journey_json 格式：
    {
        "stage": "novice" | "explore" | "mature",
        "milestones": {
            "explore_entered": true
        }
    }
"""

from __future__ import annotations

import json

from Mojing.storage.database import Database

_DEFAULT_JOURNEY: dict = {"stage": "novice", "milestones": {}}


class TenantStateRepository:
    """读写 nb_tenant_state 中的 journey_json 字段。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get_journey(self, tenant_key: str) -> dict:
        """返回 journey dict（不存在时返回默认值，不写 DB）。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT journey_json FROM nb_tenant_state WHERE tenant_key = %s LIMIT 1",
                    (tenant_key,),
                )
                row = await cur.fetchone()

        if row is None or not row[0]:
            return dict(_DEFAULT_JOURNEY)

        raw = row[0]
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(data, dict):
                return dict(_DEFAULT_JOURNEY)
            return {
                "stage":      data.get("stage") or "novice",
                "milestones": dict(data.get("milestones") or {}),
            }
        except Exception:
            return dict(_DEFAULT_JOURNEY)

    async def get_stage(self, tenant_key: str) -> str:
        """便捷方法：只返回当前 stage 字符串。"""
        return (await self.get_journey(tenant_key))["stage"]

    async def save_journey(self, tenant_key: str, journey: dict) -> None:
        """将 journey dict 写回 DB（upsert）。"""
        from datetime import datetime
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        payload = json.dumps(journey, ensure_ascii=False)

        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO nb_tenant_state (tenant_key, journey_json, updated_at)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        journey_json = VALUES(journey_json),
                        updated_at   = VALUES(updated_at)
                    """,
                    (tenant_key, payload, now),
                )
