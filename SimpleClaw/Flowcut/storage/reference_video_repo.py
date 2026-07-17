"""ReferenceVideoRepository — fc_reference_video 表的读写封装。"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from Flowcut.storage.database import Database


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class ReferenceVideoRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(
        self,
        *,
        tenant_key: str,
        oss_key: str,
        oss_url: str,
        name: str,
        duration: float,
        file_size: int,
        product: str | None = None,
        thumbnail_url: str | None = None,
    ) -> dict[str, Any]:
        """创建参考视频记录，status='PROCESSING'。"""
        now = _now()
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO fc_reference_video
                        (tenant_key, oss_key, oss_url, thumbnail_url, name,
                         product, duration, file_size, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (tenant_key, oss_key, oss_url, thumbnail_url, name,
                     product, duration, file_size, now, now),
                )
                vid = cur.lastrowid
                await cur.execute(
                    "SELECT * FROM fc_reference_video WHERE id=%s", (vid,)
                )
                row = await cur.fetchone()
                cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    async def get(self, video_id: int) -> dict[str, Any] | None:
        """按 id 查询参考视频。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT * FROM fc_reference_video WHERE id=%s", (video_id,)
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    async def update_status(
        self,
        video_id: int,
        status: str,
        *,
        scene_data: list[dict] | None = None,
    ) -> None:
        """更新参考视频状态及可选字段（scene_data）。"""
        import json as _json
        now = _now()
        set_clauses = ["status=%s", "updated_at=%s"]
        params: list[Any] = [status, now]

        if scene_data is not None:
            set_clauses.append("scene_data_json=%s")
            params.append(_json.dumps(scene_data, ensure_ascii=False))

        params.append(video_id)
        sql = f"UPDATE fc_reference_video SET {', '.join(set_clauses)} WHERE id=%s"

        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, tuple(params))

    async def list_by_tenant(
        self,
        tenant_key: str,
        *,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """列出租户的参考视频。"""
        sql = "SELECT * FROM fc_reference_video WHERE tenant_key=%s"
        params: list[Any] = [tenant_key]

        if status is not None:
            sql += " AND status=%s"
            params.append(status)

        sql += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
        params.append(limit)
        params.append(offset)

        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, tuple(params))
                rows = await cur.fetchall()
                cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in rows]

    async def set_audio(self, ref_video_id: int, audio_oss_key: str) -> None:
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE fc_reference_video SET audio_oss_key=%s, updated_at=%s WHERE id=%s",
                    (audio_oss_key, _now(), ref_video_id),
                )
                await conn.commit()

    async def set_script_id(self, ref_video_id: int, script_id: int) -> None:
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE fc_reference_video SET script_id=%s, updated_at=%s WHERE id=%s",
                    (script_id, _now(), ref_video_id),
                )
                await conn.commit()
