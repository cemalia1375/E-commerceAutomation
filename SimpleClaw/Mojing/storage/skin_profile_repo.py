"""SkinProfileRepository — 皮肤画像同步状态读写。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from Mojing.storage.database import Database


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


class SkinProfileRepository:
    """异步封装 nb_tenant_skin_profiles 与 nb_tenant_profile_block_meta。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def find_pending(self, tenant_key: str) -> dict[str, Any] | None:
        """返回该租户最新一条待同步皮肤画像。"""
        return await self._fetch_one_profile(
            """
            WHERE tenant_key = %s AND sync_status = 'pending'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (tenant_key,),
        )

    async def get_latest(self, tenant_key: str) -> dict[str, Any] | None:
        """返回该租户最新皮肤画像，不限制同步状态。"""
        return await self._fetch_one_profile(
            """
            WHERE tenant_key = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (tenant_key,),
        )

    async def has_profile_since(
        self,
        *,
        tenant_key: str,
        since: str | datetime,
        image_id: str | None = None,
        image_ref: str | None = None,
        message_id: str | None = None,
    ) -> bool:
        """验证图片分析异步结果是否已经写入画像表。

        n8n/Java 写入侧不一定保留 job_id，因此这里按可用字段逐步收窄：
        tenant + created_at 是兜底，message_id / image_url / analysis_id 用于提高准确度。
        """
        return await self.find_profile_since(
            tenant_key=tenant_key,
            since=since,
            image_id=image_id,
            image_ref=image_ref,
            message_id=message_id,
        ) is not None

    async def find_profile_since(
        self,
        *,
        tenant_key: str,
        since: str | datetime,
        image_id: str | None = None,
        image_ref: str | None = None,
        message_id: str | None = None,
    ) -> dict[str, Any] | None:
        """返回图片分析异步结果对应的画像行，未找到则返回 None。"""
        candidates: list[tuple[str, Any]] = []
        if message_id:
            candidates.append(("message_id = %s", message_id))
        if image_ref:
            candidates.append(("image_url = %s", image_ref))
        if image_id:
            candidates.append(("analysis_id = %s", image_id))

        if message_id:
            return await self._fetch_one_profile(
                """
                WHERE tenant_key = %s AND created_at >= %s AND message_id = %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (tenant_key, since, message_id),
            )

        if not candidates:
            return await self._fetch_one_profile(
                """
                WHERE tenant_key = %s AND created_at >= %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (tenant_key, since),
            )

        for clause, value in candidates:
            profile = await self._fetch_one_profile(
                f"""
                WHERE tenant_key = %s AND created_at >= %s AND {clause}
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (tenant_key, since, value),
            )
            if profile is not None:
                return profile

        return None

    async def mark_synced(self, profile_id: int, *, sync_reason: str) -> None:
        now = _now()
        await self._execute(
            """
            UPDATE nb_tenant_skin_profiles
            SET sync_status = 'synced',
                sync_reason = %s,
                synced_to_user_doc_at = %s,
                sync_error = NULL,
                updated_at = %s
            WHERE profile_id = %s
            """,
            (sync_reason, now, now, profile_id),
        )

    async def mark_skipped(self, profile_id: int, *, sync_reason: str) -> None:
        now = _now()
        await self._execute(
            """
            UPDATE nb_tenant_skin_profiles
            SET sync_status = 'skipped',
                sync_reason = %s,
                sync_error = NULL,
                updated_at = %s
            WHERE profile_id = %s
            """,
            (sync_reason, now, profile_id),
        )

    async def mark_failed(self, profile_id: int, *, error: str) -> None:
        now = _now()
        await self._execute(
            """
            UPDATE nb_tenant_skin_profiles
            SET sync_status = 'failed',
                sync_error = %s,
                updated_at = %s
            WHERE profile_id = %s
            """,
            ((error or "")[:2000], now, profile_id),
        )

    async def get_block_meta(self, tenant_key: str, block_name: str) -> dict[str, Any] | None:
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT meta_id, tenant_key, block_name,
                           last_writer, last_profile_id, content_hash,
                           last_synced_at, created_at, updated_at
                    FROM nb_tenant_profile_block_meta
                    WHERE tenant_key = %s AND block_name = %s
                    LIMIT 1
                    """,
                    (tenant_key, block_name),
                )
                row = await cur.fetchone()
                cols = [d[0] for d in cur.description] if cur.description else []
        return dict(zip(cols, row)) if row else None

    async def upsert_block_meta(
        self,
        *,
        tenant_key: str,
        block_name: str,
        last_writer: str,
        last_profile_id: int | None,
        content_hash: str,
    ) -> None:
        now = _now()
        await self._execute(
            """
            INSERT INTO nb_tenant_profile_block_meta
                (tenant_key, block_name, last_writer, last_profile_id,
                 content_hash, last_synced_at, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                last_writer     = VALUES(last_writer),
                last_profile_id = VALUES(last_profile_id),
                content_hash    = VALUES(content_hash),
                last_synced_at  = VALUES(last_synced_at),
                updated_at      = VALUES(updated_at)
            """,
            (tenant_key, block_name, last_writer, last_profile_id, content_hash, now, now, now),
        )

    async def list_profiles_in_range(
        self,
        tenant_key: str,
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        """返回 [start, end) 内该租户的全部皮肤画像行，按 created_at 倒序。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        profile_id, tenant_key, session_key, message_id,
                        image_url, analysis_id,
                        skin_attribute_json, overall_state,
                        advantages_json, signals_json,
                        sync_status, sync_reason,
                        synced_to_user_doc_at, sync_error,
                        created_at, updated_at
                    FROM nb_tenant_skin_profiles
                    WHERE tenant_key = %s AND created_at >= %s AND created_at < %s
                    ORDER BY created_at DESC
                    """,
                    (tenant_key, start, end),
                )
                rows = await cur.fetchall()
                cols = [d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, row)) for row in rows] if rows else []

    async def backdate_profile(self, profile_id: int, *, created_at: datetime) -> None:
        """测试回填专用：把画像的 created_at/updated_at 改写为历史时刻。

        synced_to_user_doc_at 保留真实时间作审计痕迹（趋势/画像链路不读它）。
        必须在 USER.md sync 完成之后调用，避免 sync 链路按时间窗找不到该画像。
        """
        ts = created_at.strftime("%Y-%m-%d %H:%M:%S")
        await self._execute(
            """
            UPDATE nb_tenant_skin_profiles
            SET created_at = %s,
                updated_at = %s
            WHERE profile_id = %s
            """,
            (ts, ts, profile_id),
        )

    async def _fetch_one_profile(
        self,
        where_sql: str,
        params: tuple[Any, ...],
    ) -> dict[str, Any] | None:
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT
                        profile_id, tenant_key, session_key, message_id,
                        image_url, analysis_id,
                        skin_attribute_json, overall_state,
                        advantages_json, signals_json,
                        sync_status, sync_reason,
                        synced_to_user_doc_at, sync_error,
                        created_at, updated_at
                    FROM nb_tenant_skin_profiles
                    {where_sql}
                    """,
                    params,
                )
                row = await cur.fetchone()
                cols = [d[0] for d in cur.description] if cur.description else []
        return dict(zip(cols, row)) if row else None

    async def _execute(self, sql: str, params: tuple[Any, ...]) -> None:
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)

    @staticmethod
    def parse_json_field(raw: Any) -> Any:
        """解析 JSON 字段，兼容驱动返回 dict/list 或字符串。"""
        if raw is None:
            return None
        if isinstance(raw, (dict, list)):
            return raw
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
