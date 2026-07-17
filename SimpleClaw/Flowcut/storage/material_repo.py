"""MaterialRepository — fc_material 表的读写封装。"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from Flowcut.storage.database import Database


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _escape_like(value: str) -> str:
    """转义 LIKE 通配符，防止用户输入 % / _ 触发意外匹配。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class MaterialRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(self, *, tenant_key: str, oss_key: str, oss_url: str,
                     name: str, category: str, duration: float,
                     file_size: int,
                     parent_material_id: int | None = None,
                     source_video_id: int | None = None,
                     product: str | None = None,
                     scene_role: str | None = None,
                     description: str | None = None) -> dict[str, Any]:
        """创建素材记录，status='PROCESSING'。"""
        now = _now()
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO fc_material
                        (tenant_key, oss_key, oss_url, name, category, duration,
                         file_size, status, parent_material_id, source_video_id,
                         product, scene_role, description, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'PROCESSING',
                            %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (tenant_key, oss_key, oss_url, name, category, duration,
                     file_size, parent_material_id, source_video_id,
                     product, scene_role, description, now, now),
                )
                material_id = cur.lastrowid
                await cur.execute(
                    "SELECT * FROM fc_material WHERE id=%s", (material_id,)
                )
                row = await cur.fetchone()
                cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    async def get(self, material_id: int) -> dict[str, Any] | None:
        """按 id 查询素材。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT * FROM fc_material WHERE id=%s", (material_id,)
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    async def list_by_tenant(self, tenant_key: str, *,
                              category: str | None = None,
                              status: str | None = None,
                              product: str | None = None,
                              scene_role: str | None = None,
                              limit: int = 50,
                              offset: int = 0) -> list[dict[str, Any]]:
        """列出租户的素材（支持 category / status / product / scene_role 过滤）。"""
        sql = "SELECT * FROM fc_material WHERE tenant_key=%s"
        params: list[Any] = [tenant_key]

        if category is not None:
            sql += " AND category=%s"
            params.append(category)
        if status is not None:
            sql += " AND status=%s"
            params.append(status)
        if product is not None:
            sql += " AND product=%s"
            params.append(product)
        if scene_role is not None:
            sql += " AND scene_role=%s"
            params.append(scene_role)

        sql += " ORDER BY created_at DESC LIMIT %s OFFSET %s"
        params.append(limit)
        params.append(offset)

        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, tuple(params))
                rows = await cur.fetchall()
                cols = [d[0] for d in cur.description]

        return [dict(zip(cols, row)) for row in rows]

    async def update_status(self, material_id: int, status: str, *,
                             name: str | None = None,
                             transcript: str | None = None,
                             description: str | None = None,
                             thumbnail_url: str | None = None,
                             preview_url: str | None = None,
                             duration: float | None = None) -> None:
        """更新素材状态（PROCESSING → READY / FAILED）及可选字段。"""
        now = _now()
        set_clauses = ["status=%s", "updated_at=%s"]
        params: list[Any] = [status, now]

        if name is not None:
            set_clauses.append("name=%s")
            params.append(name)
        if transcript is not None:
            set_clauses.append("transcript=%s")
            params.append(transcript)
        if description is not None:
            set_clauses.append("description=%s")
            params.append(description)
        if thumbnail_url is not None:
            set_clauses.append("thumbnail_url=%s")
            params.append(thumbnail_url)
        if preview_url is not None:
            set_clauses.append("preview_url=%s")
            params.append(preview_url)
        if duration is not None:
            set_clauses.append("duration=%s")
            params.append(float(duration))

        params.append(material_id)
        sql = f"UPDATE fc_material SET {', '.join(set_clauses)} WHERE id=%s"

        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, tuple(params))

    async def update(self, material_id: int, *,
                     name: str | None = None,
                     category: str | None = None,
                     product: str | None = None,
                     scene_role: str | None = None,
                     description: str | None = None) -> None:
        """手动更新素材字段。"""
        now = _now()
        set_clauses = ["updated_at=%s"]
        params: list[Any] = [now]

        if name is not None:
            set_clauses.append("name=%s")
            params.append(name)
        if category is not None:
            set_clauses.append("category=%s")
            params.append(category)
        if product is not None:
            set_clauses.append("product=%s")
            params.append(product)
        if scene_role is not None:
            set_clauses.append("scene_role=%s")
            params.append(scene_role)
        if description is not None:
            set_clauses.append("description=%s")
            params.append(description)

        params.append(material_id)
        sql = f"UPDATE fc_material SET {', '.join(set_clauses)} WHERE id=%s"

        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, tuple(params))

    async def delete(self, material_id: int) -> None:
        """删除素材记录。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM fc_material WHERE id=%s", (material_id,)
                )

    async def increment_usage(self, material_id: int) -> None:
        """累加 usage_count。"""
        now = _now()
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE fc_material
                    SET usage_count = usage_count + 1, updated_at=%s
                    WHERE id=%s
                    """,
                    (now, material_id),
                )

    # ── 向量索引相关 ───────────────────────────────────────────

    async def mark_vector_indexed(self, material_id: int) -> None:
        """Qdrant upsert 成功后回写 vector_indexed=TRUE。"""
        now = _now()
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE fc_material
                    SET vector_indexed=1, updated_at=%s
                    WHERE id=%s
                    """,
                    (now, material_id),
                )

    async def list_pending_vector(
        self,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """列出待向量化的素材（READY + 有 description + 未索引）。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT * FROM fc_material
                    WHERE status='READY'
                      AND description IS NOT NULL
                      AND vector_indexed = 0
                    LIMIT %s
                    """,
                    (limit,),
                )
                rows = await cur.fetchall()
                cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in rows]

    async def search_by_name(
        self,
        tenant_key: str,
        name: str,
        product: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """按名称关键词模糊查找素材，可选按产品过滤。"""
        escaped = _escape_like(name)
        pattern = f"%{escaped}%"
        sql_parts = [
            "SELECT id, name, category, product, scene_role, status, usage_count",
            "FROM fc_material",
            "WHERE tenant_key=%s AND name LIKE %s ESCAPE '\\\\'",
        ]
        params: list[Any] = [tenant_key, pattern]
        if product:
            sql_parts.append("AND product=%s")
            params.append(product)
        sql_parts.append("ORDER BY created_at DESC LIMIT %s")
        params.append(int(limit))

        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("\n".join(sql_parts), tuple(params))
                rows = await cur.fetchall()
                cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in rows]

    async def aggregate_qc_via_usage(self, material_id: int) -> dict[str, Any] | None:
        """聚合素材在所有关联成片上的千川回流数据。

        通过 fc_material_usage 找到素材被用过的成片，再 SUM 这些成片的 qc_* 字段。
        只统计有 qc_synced_at 的成片，避免把空数据当 0 算。
        """
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        m.id, m.name, m.product, m.scene_role,
                        COUNT(DISTINCT u.creative_id) AS used_in_creatives,
                        COALESCE(SUM(c.qc_cost), 0)        AS total_cost,
                        COALESCE(SUM(c.qc_impressions), 0) AS total_impressions,
                        COALESCE(SUM(c.qc_clicks), 0)      AS total_clicks,
                        COALESCE(SUM(c.qc_conversions), 0) AS total_conversions,
                        MAX(c.qc_synced_at)                AS last_synced_at
                    FROM fc_material m
                    LEFT JOIN fc_material_usage u ON u.material_id = m.id
                    LEFT JOIN fc_creative c
                          ON c.id = u.creative_id AND c.qc_synced_at IS NOT NULL
                    WHERE m.id = %s
                    GROUP BY m.id, m.name, m.product, m.scene_role
                    """,
                    (int(material_id),),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    async def list_distinct_products(self, tenant_key: str) -> list[str]:
        """返回该租户已有的产品名列表，供前端 AutoComplete 使用。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT DISTINCT product FROM fc_material
                    WHERE tenant_key=%s AND product IS NOT NULL AND product != ''
                    ORDER BY product
                    """,
                    (tenant_key,),
                )
                rows = await cur.fetchall()
        return [r[0] for r in rows if r[0]]
