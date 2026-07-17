"""SkinDiaryResultRepository — nb_skin_diary_results 表封装。

读取最新结果用于子 Agent 上下文注入；写入由 generate_skin_diary 工具创建新版本。
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from Mojing.storage.database import Database
from Mojing.utils.skin_diary_time import (
    infer_skin_diary_metadata_from_create_time,
    skin_diary_business_day_range,
    skin_diary_slot_range,
    strip_tz,
)


def _parse_json(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (list, dict)):
        return v
    try:
        return json.loads(v)
    except Exception:
        return None


def _format_row(row: Any) -> dict[str, Any]:
    create_time = row[6]
    inferred = infer_skin_diary_metadata_from_create_time(create_time or row[5])
    return {
        "state":         row[0],
        "summary":       row[1],
        "chips":         _parse_json(row[2]) or [],
        "morning_steps": _parse_json(row[3]) or [],
        "evening_steps": _parse_json(row[4]) or [],
        "analyzed_at":   str(row[5]) if row[5] else None,
        "create_time":   str(create_time) if create_time else None,
        "update_time":   str(row[7]) if len(row) > 7 and row[7] else None,
        "diary_date":    inferred.business_date.isoformat() if inferred.business_date else None,
        "diary_slot":    inferred.diary_slot or "",
        "generation_reason": inferred.generation_reason or "",
    }


class SkinDiaryResultRepository:
    """读写皮肤日记分析结果。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def has_today_result(self, tenant_key: str) -> bool:
        """返回该租户今天是否已有未删除的肌肤日记结果。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT 1
                    FROM nb_skin_diary_results
                    WHERE tenant_key = %s
                      AND deleted = 0
                      AND DATE(analyzed_at) = CURRENT_DATE()
                    LIMIT 1
                    """,
                    (tenant_key,),
                )
                row = await cur.fetchone()
        return row is not None

    async def has_result_for_business_date(
        self,
        tenant_key: str,
        business_date: date,
    ) -> bool:
        """返回该租户在指定业务日期是否已有任意未删除的肌肤日记。"""
        bounds = skin_diary_business_day_range(business_date)
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT 1
                    FROM nb_skin_diary_results
                    WHERE tenant_key = %s
                      AND deleted = 0
                      AND COALESCE(create_time, analyzed_at) >= %s
                      AND COALESCE(create_time, analyzed_at) < %s
                    LIMIT 1
                    """,
                    (tenant_key, strip_tz(bounds.start), strip_tz(bounds.end)),
                )
                row = await cur.fetchone()
        return row is not None

    async def has_result_for_business_date_slot(
        self,
        tenant_key: str,
        business_date: date,
        diary_slot: str,
    ) -> bool:
        """返回该业务日期 + slot 是否已有未删除的肌肤日记。"""
        bounds = skin_diary_slot_range(business_date, diary_slot)
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT 1
                    FROM nb_skin_diary_results
                    WHERE tenant_key = %s
                      AND deleted = 0
                      AND COALESCE(create_time, analyzed_at) >= %s
                      AND COALESCE(create_time, analyzed_at) < %s
                    LIMIT 1
                    """,
                    (tenant_key, strip_tz(bounds.start), strip_tz(bounds.end)),
                )
                row = await cur.fetchone()
        return row is not None

    async def get_latest(self, tenant_key: str) -> dict[str, Any] | None:
        """返回该租户最近一条未删除的分析结果，无结果时返回 None。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT state, summary, chips, morning_steps, evening_steps, analyzed_at, create_time, update_time
                    FROM nb_skin_diary_results
                    WHERE tenant_key = %s AND deleted = 0
                    ORDER BY COALESCE(create_time, analyzed_at) DESC
                    LIMIT 1
                    """,
                    (tenant_key,),
                )
                row = await cur.fetchone()

        if row is None:
            return None
        return _format_row(row)

    async def get_latest_updated_since(
        self,
        tenant_key: str,
        since: Any,
    ) -> dict[str, Any] | None:
        """返回指定时间后实际写入/更新的最新肌肤日记。

        肌肤日记的 create_time 会按业务日期/时段落到晨间、午间或晚间代表时间，
        不能稳定表示 runtime task 完成时间；这里用 update_time 锚定实际落库时间。
        """
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT state, summary, chips, morning_steps, evening_steps, analyzed_at, create_time, update_time
                    FROM nb_skin_diary_results
                    WHERE tenant_key = %s
                      AND deleted = 0
                      AND update_time >= %s
                    ORDER BY update_time DESC
                    LIMIT 1
                    """,
                    (tenant_key, since),
                )
                row = await cur.fetchone()

        if row is None:
            return None
        return _format_row(row)

    async def get_results_for_business_date(
        self,
        tenant_key: str,
        business_date: date,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """返回指定业务日期下保留的肌肤日记，按生成时间倒序。"""
        bounds = skin_diary_business_day_range(business_date)
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT state, summary, chips, morning_steps, evening_steps, analyzed_at, create_time, update_time
                    FROM nb_skin_diary_results
                    WHERE tenant_key = %s
                      AND deleted = 0
                      AND COALESCE(create_time, analyzed_at) >= %s
                      AND COALESCE(create_time, analyzed_at) < %s
                    ORDER BY COALESCE(create_time, analyzed_at) DESC
                    LIMIT %s
                    """,
                    (
                        tenant_key,
                        strip_tz(bounds.start),
                        strip_tz(bounds.end),
                        max(1, min(int(limit), 100)),
                    ),
                )
                rows = await cur.fetchall()
        return [_format_row(row) for row in rows]

    async def create_result(
        self,
        *,
        tenant_key: str,
        analyzed_at: Any,
        create_time: Any,
        state: str,
        summary: str,
        chips: list[dict[str, Any]],
        morning_steps: list[dict[str, Any]],
        evening_steps: list[dict[str, Any]],
        raw_output: dict[str, Any] | None = None,
        creator: str = "generate_skin_diary",
    ) -> int:
        """插入一条新的肌肤日记结果，保留历史版本。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO nb_skin_diary_results
                        (tenant_key, analyzed_at, state, summary, chips,
                         morning_steps, evening_steps, raw_output,
                         creator, create_time, updater, update_time)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                    """,
                    (
                        tenant_key,
                        analyzed_at,
                        state,
                        summary,
                        json.dumps(chips, ensure_ascii=False),
                        json.dumps(morning_steps, ensure_ascii=False),
                        json.dumps(evening_steps, ensure_ascii=False),
                        json.dumps(raw_output or {}, ensure_ascii=False),
                        creator,
                        create_time,
                        creator,
                    ),
                )
                return int(getattr(cur, "lastrowid", 0) or 0)
