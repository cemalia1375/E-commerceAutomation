"""CreativeRepository — fc_creative 表的读写封装。"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Any
from Flowcut.storage.database import Database


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _escape_like(value: str) -> str:
    """转义 LIKE 通配符，防止用户输入 % / _ 触发意外匹配。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class CreativeRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(self, *, tenant_key: str, session_key: str,
                     script_id: int | None = None) -> dict[str, Any]:
        """创建成片记录，status='PENDING'。"""
        now = _now()
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO fc_creative
                        (tenant_key, session_key, script_id, status, label,
                         created_at, updated_at)
                    VALUES (%s, %s, %s, 'PENDING', 'NORMAL', %s, %s)
                    """,
                    (tenant_key, session_key, script_id, now, now),
                )
                creative_id = cur.lastrowid
                await cur.execute(
                    "SELECT * FROM fc_creative WHERE id=%s", (creative_id,)
                )
                row = await cur.fetchone()
                cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    async def create_highlight_job(
        self,
        *,
        tenant_key: str,
        session_key: str,
        script_id: int,
        creative_type: str,
        batch_id: str,
        source_asset_id: int | None,
        connector_asset_id: int | None = None,
    ) -> dict[str, Any]:
        """创建一条高光批量产物记录，先以 PROCESSING 状态占位。"""
        now = _now()
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO fc_creative
                        (tenant_key, session_key, script_id, creative_type, batch_id,
                         source_asset_id, connector_asset_id, status, label,
                         created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'PROCESSING', 'NORMAL', %s, %s)
                    """,
                    (
                        tenant_key,
                        session_key,
                        script_id,
                        creative_type,
                        batch_id,
                        source_asset_id,
                        connector_asset_id,
                        now,
                        now,
                    ),
                )
                creative_id = cur.lastrowid
                await cur.execute("SELECT * FROM fc_creative WHERE id=%s", (creative_id,))
                row = await cur.fetchone()
                cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    async def create_cross_episode_job(
        self,
        *,
        tenant_key: str,
        session_key: str,
        script_id: int | None,
        batch_id: str,
        source_asset_id: int,
        clip_plan_json: str,
        highlight_start: float,
        highlight_reason_json: str,
        connector_asset_id: int | None = None,
        status: str = "PENDING",
    ) -> dict[str, Any]:
        """创建一条跨集高光切片成片记录，带好 clip_plan_json，待 VIDEO_COMPOSE 合成。"""
        now = _now()
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO fc_creative
                        (tenant_key, session_key, script_id, creative_type, batch_id,
                         source_asset_id, connector_asset_id, status, label, highlight_start,
                         highlight_reason_json, clip_plan_json, created_at, updated_at)
                    VALUES (%s, %s, %s, 'continuous_cross_episode', %s, %s, %s, %s,
                            'NORMAL', %s, %s, %s, %s, %s)
                    """,
                    (
                        tenant_key, session_key, script_id, batch_id,
                        source_asset_id, connector_asset_id, status, highlight_start,
                        highlight_reason_json, clip_plan_json, now, now,
                    ),
                )
                creative_id = cur.lastrowid
                await cur.execute("SELECT * FROM fc_creative WHERE id=%s", (creative_id,))
                row = await cur.fetchone()
                cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    async def find_highlight_by_script(
        self,
        *,
        tenant_key: str,
        script_id: int,
        creative_type: str,
    ) -> dict[str, Any] | None:
        """查找某个脚本已保存的高光成片记录，避免按钮重复创建。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT * FROM fc_creative
                    WHERE tenant_key=%s
                      AND script_id=%s
                      AND creative_type=%s
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (tenant_key, script_id, creative_type),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    async def find_latest_highlight_by_script(
        self,
        *,
        tenant_key: str,
        script_id: int,
    ) -> dict[str, Any] | None:
        """查找某个脚本最新的高光成片记录，并带出原片/数字人资产信息。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        c.*,
                        rv.name AS ref_video_name,
                        src.name AS source_asset_name,
                        src.drama_name AS source_drama_name,
                        src.episode_no AS source_episode_no,
                        src.oss_key AS source_asset_oss_key,
                        src.oss_url AS source_asset_oss_url,
                        conn.name AS connector_asset_name,
                        conn.connector_role AS connector_role,
                        conn.oss_key AS connector_asset_oss_key,
                        conn.oss_url AS connector_asset_oss_url
                    FROM fc_creative c
                    LEFT JOIN fc_script s ON s.id = c.script_id
                    LEFT JOIN fc_reference_video rv ON rv.id = s.reference_video_id
                    LEFT JOIN fc_highlight_asset src ON src.id = c.source_asset_id
                    LEFT JOIN fc_highlight_asset conn ON conn.id = c.connector_asset_id
                    WHERE c.tenant_key=%s
                      AND c.script_id=%s
                      AND c.creative_type IN ('highlight_original', 'highlight_digital_human')
                    ORDER BY c.id DESC
                    LIMIT 1
                    """,
                    (tenant_key, script_id),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    async def get(self, creative_id: int) -> dict[str, Any] | None:
        """按 id 查询成片。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT * FROM fc_creative WHERE id=%s", (creative_id,)
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    async def delete(self, creative_id: int) -> None:
        """删除成片：先清 fc_material_usage 关联行，再删 fc_creative 行。

        不级联删除源高光资产（fc_highlight_asset）——源资产可被多条成片复用。
        """
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM fc_material_usage WHERE creative_id=%s", (creative_id,)
                )
                await cur.execute(
                    "DELETE FROM fc_creative WHERE id=%s", (creative_id,)
                )

    async def list_by_tenant(self, tenant_key: str, *,
                              limit: int = 50,
                              offset: int = 0) -> list[dict[str, Any]]:
        """列出租户的成片。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        c.*,
                        rv.name AS ref_video_name,
                        src.name AS source_asset_name,
                        src.drama_name AS source_drama_name,
                        src.episode_no AS source_episode_no,
                        src.oss_url AS source_asset_oss_url,
                        conn.name AS connector_asset_name,
                        conn.connector_role AS connector_role,
                        conn.oss_url AS connector_asset_oss_url
                    FROM fc_creative c
                    LEFT JOIN fc_script s ON s.id = c.script_id
                    LEFT JOIN fc_reference_video rv ON rv.id = s.reference_video_id
                    LEFT JOIN fc_highlight_asset src ON src.id = c.source_asset_id
                    LEFT JOIN fc_highlight_asset conn ON conn.id = c.connector_asset_id
                    WHERE c.tenant_key=%s
                    ORDER BY c.created_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    (tenant_key, limit, offset),
                )
                rows = await cur.fetchall()
                cols = [d[0] for d in cur.description]

        return [dict(zip(cols, row)) for row in rows]

    async def update_status(self, creative_id: int, status: str, *,
                             oss_key: str | None = None,
                             oss_url: str | None = None,
                             srt_url: str | None = None) -> None:
        """更新成片状态及可选 OSS 字段。"""
        now = _now()
        set_clauses = ["status=%s", "updated_at=%s"]
        params: list[Any] = [status, now]

        if oss_key is not None:
            set_clauses.append("oss_key=%s")
            params.append(oss_key)
        if oss_url is not None:
            set_clauses.append("oss_url=%s")
            params.append(oss_url)
        if srt_url is not None:
            set_clauses.append("srt_url=%s")
            params.append(srt_url)

        params.append(creative_id)
        sql = f"UPDATE fc_creative SET {', '.join(set_clauses)} WHERE id=%s"

        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, tuple(params))

    async def set_connector_asset(
        self, creative_id: int, connector_asset_id: int | None,
    ) -> None:
        """设置/清空成片要拼接的数字人连接器资产（跨集高光接数字人用）。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE fc_creative SET connector_asset_id=%s, updated_at=%s WHERE id=%s",
                    (connector_asset_id, _now(), creative_id),
                )

    async def set_preroll_asset(
        self, creative_id: int, preroll_asset_id: int | None,
    ) -> None:
        """设置/清空成片要叠加的前贴素材（跨集高光用）。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE fc_creative SET preroll_asset_id=%s, updated_at=%s WHERE id=%s",
                    (preroll_asset_id, _now(), creative_id),
                )

    async def mark_highlight_ready(
        self,
        creative_id: int,
        *,
        highlight_start: float | None,
        highlight_end: float | None,
        highlight_reason: dict[str, Any] | None,
        compose_plan: dict[str, Any] | None,
        status: str = "READY",
    ) -> None:
        """高光分析完成后回写成片库元数据。"""
        now = _now()
        await self._update_highlight_result(
            creative_id,
            status=status,
            highlight_start=highlight_start,
            highlight_end=highlight_end,
            highlight_reason=highlight_reason,
            compose_plan=compose_plan,
            updated_at=now,
        )

    async def mark_highlight_failed(
        self,
        creative_id: int,
        *,
        error: str,
    ) -> None:
        """高光分析失败后标记成片记录。"""
        now = _now()
        await self._update_highlight_result(
            creative_id,
            status="FAILED",
            highlight_start=None,
            highlight_end=None,
            highlight_reason={"error": error[:2000]},
            compose_plan=None,
            updated_at=now,
        )

    async def _update_highlight_result(
        self,
        creative_id: int,
        *,
        status: str,
        highlight_start: float | None,
        highlight_end: float | None,
        highlight_reason: dict[str, Any] | None,
        compose_plan: dict[str, Any] | None,
        updated_at: str,
    ) -> None:
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE fc_creative
                    SET status=%s,
                        highlight_start=%s,
                        highlight_end=%s,
                        highlight_reason_json=%s,
                        compose_plan_json=%s,
                        updated_at=%s
                    WHERE id=%s
                    """,
                    (
                        status,
                        highlight_start,
                        highlight_end,
                        json.dumps(highlight_reason, ensure_ascii=False) if highlight_reason else None,
                        json.dumps(compose_plan, ensure_ascii=False) if compose_plan else None,
                        updated_at,
                        creative_id,
                    ),
                )

    async def update_label(self, creative_id: int, label: str) -> None:
        """更新成片标签（NORMAL / HOT / DEAD）。"""
        now = _now()
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE fc_creative
                    SET label=%s, updated_at=%s
                    WHERE id=%s
                    """,
                    (label, now, creative_id),
                )

    async def update_qianchuan_ids(self, creative_id: int, *,
                                    material_id: str,
                                    campaign_id: str) -> None:
        """绑定千川素材 ID 和计划 ID。"""
        now = _now()
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE fc_creative
                    SET qianchuan_material_id=%s, qianchuan_campaign_id=%s,
                        updated_at=%s
                    WHERE id=%s
                    """,
                    (material_id, campaign_id, now, creative_id),
                )

    async def find_by_qc_material_id(self, qc_material_id: str) -> dict[str, Any] | None:
        """按千川 material_id 查找成片记录（首次绑定后用此接口做匹配）。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT * FROM fc_creative WHERE qc_material_id=%s LIMIT 1",
                    (qc_material_id,),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    async def find_by_id_exact(self, creative_id: int) -> dict[str, Any] | None:
        """按 fc_creative.id 精确查找（复用 get，语义更明确）。"""
        return await self.get(creative_id)

    async def insert_from_qc(
        self,
        *,
        tenant_key: str,
        qc_material_id: str,
        material_name: str,
        oss_url: str | None,
        qc_cost: float | None,
        qc_impressions: int | None,
        qc_clicks: int | None,
        qc_conversions: int | None,
    ) -> int:
        """千川反向同步：为千川已存在但 flowcut 没有的物料新建 fc_creative 行。

        语义：当 sync 拉到一条 qc_material_id，既未按 id 命中、也未按文件名命中现有
        fc_creative 时，直接 INSERT 一条新 creative。session_key 用 sentinel
        'qianchuan_import' 标识来源，status='ACTIVE'（千川物料默认就是投放中的）。
        oss_key 留空（视频本体仍在千川 CDN，未下载到 flowcut OSS）。

        返回新插入的 creative_id。
        """
        now = _now()
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO fc_creative
                        (tenant_key, session_key, oss_key, oss_url, status, label,
                         qc_material_id, qc_cost, qc_impressions, qc_clicks,
                         qc_conversions, qc_synced_at, created_at, updated_at)
                    VALUES (%s, 'qianchuan_import', %s, %s,
                            'ACTIVE', 'NORMAL',
                            %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        tenant_key,
                        f"qianchuan/{material_name}" if material_name else "",
                        oss_url or "",
                        qc_material_id,
                        qc_cost,
                        qc_impressions,
                        qc_clicks,
                        qc_conversions,
                        now,
                        now,
                        now,
                    ),
                )
                return int(cur.lastrowid)

    async def search_by_name(
        self,
        tenant_key: str,
        name: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """按关键词模糊查找成片，匹配 fc_reference_video.name 或 fc_script.product。

        fc_creative 自身没有 title 字段，按用户视角通过关联视频名 / 产品名搜索。
        """
        escaped = _escape_like(name)
        pattern = f"%{escaped}%"
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        c.id, c.script_id, c.status, c.qc_synced_at,
                        c.qc_material_id,
                        c.qc_cost, c.qc_impressions, c.qc_clicks, c.qc_conversions,
                        rv.name    AS ref_video_name,
                        s.product  AS product
                    FROM fc_creative c
                    LEFT JOIN fc_script s ON s.id = c.script_id
                    LEFT JOIN fc_reference_video rv ON rv.id = s.reference_video_id
                    WHERE c.tenant_key = %s
                      AND (rv.name LIKE %s ESCAPE '\\\\'
                           OR s.product LIKE %s ESCAPE '\\\\')
                    ORDER BY c.created_at DESC
                    LIMIT %s
                    """,
                    (tenant_key, pattern, pattern, int(limit)),
                )
                rows = await cur.fetchall()
                cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in rows]

    async def update_qc_stats(
        self,
        creative_id: int,
        *,
        qc_material_id: str | None = None,
        qc_cost: float | None,
        qc_impressions: int | None,
        qc_clicks: int | None,
        qc_conversions: int | None,
    ) -> None:
        """更新千川数据回流字段；qc_material_id 仅首次绑定时传入（之后传 None 跳过覆写）。"""
        now = _now()
        set_clauses = [
            "qc_cost=%s",
            "qc_impressions=%s",
            "qc_clicks=%s",
            "qc_conversions=%s",
            "qc_synced_at=%s",
            "updated_at=%s",
        ]
        params: list[Any] = [qc_cost, qc_impressions, qc_clicks, qc_conversions, now, now]

        if qc_material_id is not None:
            set_clauses.insert(0, "qc_material_id=%s")
            params.insert(0, qc_material_id)

        params.append(creative_id)
        sql = f"UPDATE fc_creative SET {', '.join(set_clauses)} WHERE id=%s"

        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, tuple(params))
