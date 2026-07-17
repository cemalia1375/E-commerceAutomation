"""HighlightAssetRepository — fc_highlight_asset 表的读写封装。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from Flowcut.storage.database import Database


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class HighlightAssetRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(
        self,
        *,
        tenant_key: str,
        asset_type: str,
        oss_key: str,
        oss_url: str,
        name: str,
        file_size: int,
        drama_name: str | None = None,
        episode_no: int | None = None,
        connector_role: str | None = None,
        duration: float = 0.0,
        status: str = "READY",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = _now()
        metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO fc_highlight_asset
                        (tenant_key, asset_type, drama_name, episode_no, connector_role,
                         oss_key, oss_url, name, duration, file_size, status,
                         metadata_json, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        tenant_key,
                        asset_type,
                        drama_name,
                        episode_no,
                        connector_role,
                        oss_key,
                        oss_url,
                        name,
                        duration,
                        file_size,
                        status,
                        metadata_json,
                        now,
                        now,
                    ),
                )
                asset_id = cur.lastrowid
                await cur.execute("SELECT * FROM fc_highlight_asset WHERE id=%s", (asset_id,))
                row = await cur.fetchone()
                cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    async def get(self, asset_id: int) -> dict[str, Any] | None:
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT * FROM fc_highlight_asset WHERE id=%s", (asset_id,))
                row = await cur.fetchone()
                if row is None:
                    return None
                cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    async def list_by_tenant(
        self,
        tenant_key: str,
        *,
        asset_type: str | None = None,
        drama_name: str | None = None,
        connector_role: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM fc_highlight_asset WHERE tenant_key=%s"
        params: list[Any] = [tenant_key]

        if asset_type is not None:
            sql += " AND asset_type=%s"
            params.append(asset_type)
        if drama_name is not None:
            sql += " AND drama_name=%s"
            params.append(drama_name)
        if connector_role is not None:
            sql += " AND connector_role=%s"
            params.append(connector_role)

        sql += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, tuple(params))
                rows = await cur.fetchall()
                cols = [d[0] for d in cur.description]

        return [dict(zip(cols, row)) for row in rows]

    async def list_groups(
        self,
        tenant_key: str,
        *,
        asset_type: str,
        group_field: str,
    ) -> list[dict[str, Any]]:
        """按 group_field（drama_name / connector_role）聚合，返回 [{name, count}]。

        供原片库入口层轻量加载：只取分组名与数量，不拉素材行、不签 OSS URL。
        group_field 由路由按 asset_type 固定传入，非用户输入，无注入风险。
        """
        if group_field not in ("drama_name", "connector_role"):
            raise ValueError(f"unsupported group_field: {group_field}")
        sql = (
            f"SELECT COALESCE({group_field}, '') AS name, COUNT(*) AS count "
            "FROM fc_highlight_asset WHERE tenant_key=%s AND asset_type=%s "
            f"GROUP BY {group_field} ORDER BY name"
        )
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (tenant_key, asset_type))
                rows = await cur.fetchall()
                cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in rows]

    async def delete(self, asset_id: int) -> None:
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("DELETE FROM fc_highlight_asset WHERE id=%s", (asset_id,))
