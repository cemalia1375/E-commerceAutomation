"""ScriptRepository — fc_script 表的读写封装（v2 schema）。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from Flowcut.storage.database import Database


class StatusConflictError(Exception):
    """脚本状态不允许该操作（如 CONFIRMED 改 segments）。"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _row_to_dict(cols: list[str], row: tuple) -> dict[str, Any]:
    item = dict(zip(cols, row))
    raw = item.get("segments_json")
    if isinstance(raw, str):
        item["segments"] = json.loads(raw)
    else:
        item["segments"] = raw or []
    item.pop("segments_json", None)
    return item


_COLS = [
    "id", "tenant_key", "source", "reference_video_id", "product",
    "segments_json", "status", "created_at", "updated_at",
]
_SELECT_COLS = ", ".join(_COLS)

_VALID_STATUSES = ("PROCESSING", "DRAFT", "CONFIRMED", "FAILED")


class ScriptRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(
        self,
        *,
        tenant_key: str,
        source: str,
        segments: list[dict],
        reference_video_id: int | None = None,
        product: str | None = None,
        status: str = "DRAFT",
    ) -> dict[str, Any]:
        if source not in ("decomposed", "uploaded"):
            raise ValueError(f"invalid source: {source}")
        if status not in _VALID_STATUSES:
            raise ValueError(f"invalid status: {status}")
        now = _now()
        segments_json = json.dumps(segments, ensure_ascii=False)
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO fc_script
                        (tenant_key, source, reference_video_id, product,
                         segments_json, status, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (tenant_key, source, reference_video_id, product,
                     segments_json, status, now, now),
                )
                script_id = cur.lastrowid
                await conn.commit()
        result = await self.get(script_id)
        assert result is not None
        return result

    async def get(self, script_id: int) -> dict[str, Any] | None:
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"SELECT {_SELECT_COLS} FROM fc_script WHERE id = %s",
                    (script_id,),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                return _row_to_dict(_COLS, row)

    async def list_by_tenant(
        self,
        tenant_key: str,
        *,
        status: str | None = None,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        where = ["tenant_key = %s"]
        args: list[Any] = [tenant_key]
        if status:
            where.append("status = %s")
            args.append(status)
        if source:
            where.append("source = %s")
            args.append(source)
        sql = (
            f"SELECT {_SELECT_COLS} FROM fc_script "
            f"WHERE {' AND '.join(where)} ORDER BY id DESC"
        )
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, args)
                rows = await cur.fetchall()
                return [_row_to_dict(_COLS, r) for r in rows]

    async def update_segments(
        self, script_id: int, segments: list[dict]
    ) -> None:
        record = await self.get(script_id)
        if record is None:
            raise ValueError(f"script {script_id} not found")
        if record["status"] != "DRAFT":
            raise StatusConflictError(
                f"script {script_id} status={record['status']}, only DRAFT can edit"
            )
        segments_json = json.dumps(segments, ensure_ascii=False)
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE fc_script SET segments_json=%s, updated_at=%s WHERE id=%s",
                    (segments_json, _now(), script_id),
                )
                await conn.commit()

    async def update_segments_and_status(
        self,
        script_id: int,
        segments: list[dict],
        status: str,
    ) -> None:
        """一次 UPDATE 写 segments + status + updated_at。

        与 update_segments 不同，本方法不强制要求当前 status 是 DRAFT；
        用于 scene_decompose executor 把预建的 PROCESSING 脚本迁移到 DRAFT/FAILED。
        仍校验目标 status 在 _VALID_STATUSES 内。
        """
        if status not in _VALID_STATUSES:
            raise ValueError(f"invalid status: {status}")
        record = await self.get(script_id)
        if record is None:
            raise ValueError(f"script {script_id} not found")
        segments_json = json.dumps(segments, ensure_ascii=False)
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE fc_script SET segments_json=%s, status=%s, "
                    "updated_at=%s WHERE id=%s",
                    (segments_json, status, _now(), script_id),
                )
                await conn.commit()

    async def update_status(self, script_id: int, status: str) -> None:
        if status not in _VALID_STATUSES:
            raise ValueError(f"invalid status: {status}")
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE fc_script SET status=%s, updated_at=%s WHERE id=%s",
                    (status, _now(), script_id),
                )
                await conn.commit()

    async def update_product(self, script_id: int, product: str | None) -> None:
        """更新脚本绑定的产品；product=None 表示清空。"""
        record = await self.get(script_id)
        if record is None:
            raise ValueError(f"script {script_id} not found")
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE fc_script SET product=%s, updated_at=%s WHERE id=%s",
                    (product, _now(), script_id),
                )
                await conn.commit()
