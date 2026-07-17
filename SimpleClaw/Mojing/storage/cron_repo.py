"""CronRepository — 读写 nb_cron_jobs 表。

job 类型：
  once     — 指定时刻执行一次，执行后 status → done
  interval — 每隔 N 秒执行，持续有效
  cron     — 标准 cron 表达式（"0 8 * * *"），持续有效

run_at 语义：下次应执行的时间，调度器通过 run_at <= NOW 判断是否到期。
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from Mojing.utils.cron_time import dt_str, now_local, now_local_str
from Mojing.storage.database import Database


class CronRepository:
    """nb_cron_jobs 的 CRUD 封装。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------

    async def add(
        self,
        *,
        tenant_key: str,
        session_key: str,
        cron_type: str,          # "once" | "interval" | "cron"
        task: str,
        run_at: datetime,
        cron_expr: str | None = None,
        interval_s: int | None = None,
    ) -> str:
        """插入新 job，返回 job_id。"""
        job_id = uuid.uuid4().hex
        now = now_local_str()
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO nb_cron_jobs
                        (id, tenant_key, session_key, cron_type, cron_expr,
                         interval_s, run_at, task, status, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'active', %s, %s)
                    """,
                    (job_id, tenant_key, session_key, cron_type,
                     cron_expr, interval_s, dt_str(run_at), task, now, now),
                )
        return job_id

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    async def get_due_jobs(self) -> list[dict[str, Any]]:
        """返回所有当前到期且 active 的 job。"""
        now = now_local_str()
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, tenant_key, session_key, cron_type,
                           cron_expr, interval_s, run_at, task
                    FROM nb_cron_jobs
                    WHERE status = 'active' AND run_at <= %s
                    ORDER BY run_at ASC
                    LIMIT 50
                    """,
                    (now,),
                )
                rows = await cur.fetchall()

        return _rows_to_jobs(rows)

    async def release_stale_running(self, *, max_age_s: int) -> int:
        """释放长时间未完成的 running job，避免实例异常退出后永久卡死。"""
        cutoff = dt_str(now_local() - timedelta(seconds=max(max_age_s, 1)))
        now = now_local_str()
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE nb_cron_jobs
                    SET status = 'active', updated_at = %s
                    WHERE status = 'running' AND updated_at <= %s
                    """,
                    (now, cutoff),
                )
                return cur.rowcount

    async def claim_due_jobs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """原子认领到期 job，防止多实例重复消费。"""
        now = now_local_str()
        limit = max(1, int(limit or 1))
        async with self._db.acquire() as conn:
            try:
                await conn.begin()
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        SELECT id, tenant_key, session_key, cron_type,
                               cron_expr, interval_s, run_at, task
                        FROM nb_cron_jobs
                        WHERE status = 'active' AND run_at <= %s
                        ORDER BY run_at ASC
                        LIMIT %s
                        FOR UPDATE SKIP LOCKED
                        """,
                        (now, limit),
                    )
                    rows = await cur.fetchall()
                    jobs = _rows_to_jobs(rows)
                    if jobs:
                        ids = [job["id"] for job in jobs]
                        placeholders = ",".join(["%s"] * len(ids))
                        await cur.execute(
                            f"""
                            UPDATE nb_cron_jobs
                            SET status = 'running', updated_at = %s
                            WHERE id IN ({placeholders})
                            """,
                            [now, *ids],
                        )
                await conn.commit()
                return jobs
            except Exception:
                await conn.rollback()
                raise

    async def list_by_tenant(self, tenant_key: str) -> list[dict[str, Any]]:
        """返回该租户所有 active 的 job（供 cron_list 工具使用）。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT id, cron_type, cron_expr, interval_s,
                           run_at, task, last_run_at
                    FROM nb_cron_jobs
                    WHERE tenant_key = %s AND status = 'active'
                    ORDER BY run_at ASC
                    """,
                    (tenant_key,),
                )
                rows = await cur.fetchall()

        return [
            {
                "id":          r[0],
                "cron_type":   r[1],
                "cron_expr":   r[2],
                "interval_s":  r[3],
                "run_at":      str(r[4]) if r[4] else None,
                "task":        r[5],
                "last_run_at": str(r[6]) if r[6] else None,
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # 执行后更新
    # ------------------------------------------------------------------

    async def mark_done(self, job_id: str) -> None:
        """once 类型执行完后标记为 done。"""
        now = now_local_str()
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE nb_cron_jobs
                    SET status = 'done', last_run_at = %s, updated_at = %s
                    WHERE id = %s AND status = 'running'
                    """,
                    (now, now, job_id),
                )

    async def update_next_run(self, job_id: str, next_run_at) -> None:
        """interval / cron 类型执行完后更新下次执行时间。"""
        now = now_local_str()
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE nb_cron_jobs
                    SET status = 'active', run_at = %s, last_run_at = %s, updated_at = %s
                    WHERE id = %s AND status = 'running'
                    """,
                    (dt_str(next_run_at), now, now, job_id),
                )

    async def release_claim(self, job_id: str) -> None:
        """释放已认领但尚未完成的 job，使其可在后续 tick 重新被消费。"""
        now = now_local_str()
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE nb_cron_jobs
                    SET status = 'active', updated_at = %s
                    WHERE id = %s AND status = 'running'
                    """,
                    (now, job_id),
                )

    async def remove(self, job_id: str, tenant_key: str) -> bool:
        """删除指定 job（校验 tenant_key 防止越权）。"""
        now = now_local_str()
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE nb_cron_jobs
                    SET status = 'done', updated_at = %s
                    WHERE id = %s AND tenant_key = %s AND status IN ('active', 'running')
                    """,
                    (now, job_id, tenant_key),
                )
                return cur.rowcount > 0


def _rows_to_jobs(rows: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    return [
        {
            "id":          r[0],
            "tenant_key":  r[1],
            "session_key": r[2],
            "cron_type":   r[3],
            "cron_expr":   r[4],
            "interval_s":  r[5],
            "run_at":      r[6],
            "task":        r[7],
        }
        for r in rows
    ]
