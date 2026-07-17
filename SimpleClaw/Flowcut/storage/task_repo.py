"""RuntimeTaskRepository — 读写 nb_runtime_tasks 表。

实现 TaskStateStore Protocol，在 Worker 的生命周期钩子里被调用：

  record_queued → status='queued'
  mark_running  → status='running', claimed_by
  mark_finished → status='succeeded' | 'noop' | 'triggered', completed_at
  mark_failed   → status='failed', last_error, completed_at

另外暴露 list_recent() 给 admin 面板读最近任务的状态摘要。
scope_key 会一起落表，便于排查"为什么这条任务在等锁"。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from simpleclaw.runtime.task_protocol import TaskEnvelope, TaskExecutionResult
from Flowcut.storage.database import Database


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


_CN_TZ = timezone(timedelta(hours=8))


def _to_cn_str(v: object) -> str:
    """将 UTC naive datetime 转为北京时间显示字符串。"""
    if isinstance(v, datetime):
        cn = v.replace(tzinfo=timezone.utc).astimezone(_CN_TZ)
        return cn.strftime("%Y-%m-%d %H:%M:%S")
    return str(v) if v is not None else ""


class RuntimeTaskRepository:
    """nb_runtime_tasks 的异步读写封装。实现 TaskStateStore Protocol。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # TaskStateStore 协议实现
    # ------------------------------------------------------------------

    async def record_queued(
        self,
        task: TaskEnvelope,
        *,
        queue_message_id: str | None = None,
    ) -> None:
        """插入一条 status='queued' 记录。

        task_id 已由 TaskEnvelope 生成（default_factory=uuid4），
        若同 task_id 被重试入队（attempt > 0），走 UPDATE 分支刷新状态。
        """
        now = _now()
        payload_json = json.dumps(task.payload, ensure_ascii=False)
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO nb_runtime_tasks
                        (task_id, task_type, stream_name, tenant_key, session_key, scope_key,
                         trace_id, service_role, status, attempt, max_attempts,
                         payload_json, queue_message_id, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'queued', %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        status='queued',
                        attempt=VALUES(attempt),
                        scope_key=VALUES(scope_key),
                        queue_message_id=VALUES(queue_message_id),
                        last_error=NULL,
                        claimed_by=NULL,
                        completed_at=NULL,
                        result_details_json=NULL,
                        updated_at=VALUES(updated_at)
                    """,
                    (
                        task.task_id,
                        task.task_type,
                        task.stream,
                        task.tenant_key,
                        task.session_key,
                        task.scope_key,
                        task.trace_id,
                        task.service_role,
                        task.attempt,
                        task.max_attempts,
                        payload_json,
                        queue_message_id,
                        now,
                        now,
                    ),
                )

    async def mark_running(
        self,
        task: TaskEnvelope,
        *,
        claimed_by: str,
    ) -> None:
        """更新为 status='running'，记录 worker 身份。"""
        now = _now()
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE nb_runtime_tasks
                    SET status='running', claimed_by=%s, updated_at=%s
                    WHERE task_id=%s
                    """,
                    (claimed_by, now, task.task_id),
                )

    async def mark_finished(
        self,
        task: TaskEnvelope,
        result: TaskExecutionResult,
    ) -> None:
        """根据 result.status 写入任务状态。

        triggered 表示外部异步业务已触发但尚未完成，不写 completed_at；
        succeeded / noop 表示本任务生命周期已结束，写 completed_at。

        用 JSON_MERGE_PATCH 将最终结果合并到进度数据上，避免覆盖
        update_progress() 写入的 stage/progress_pct 等前端进度条依赖的字段。
        """
        now = _now()
        status = result.status
        completed_at = None if status == "triggered" else now
        details_json: str | None = None
        if result.details:
            try:
                details_json = json.dumps(result.details, ensure_ascii=False)
            except (TypeError, ValueError):
                details_json = None
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                if details_json is not None:
                    await cur.execute(
                        """
                        UPDATE nb_runtime_tasks
                        SET status=%s, last_error=NULL, updated_at=%s, completed_at=%s,
                            result_details_json=JSON_MERGE_PATCH(
                                COALESCE(result_details_json, '{}'), %s)
                        WHERE task_id=%s
                        """,
                        (status, now, completed_at, details_json, task.task_id),
                    )
                else:
                    await cur.execute(
                        """
                        UPDATE nb_runtime_tasks
                        SET status=%s, last_error=NULL, updated_at=%s, completed_at=%s
                        WHERE task_id=%s
                        """,
                        (status, now, completed_at, task.task_id),
                    )

    async def update_progress(
        self,
        task_id: str,
        progress: dict,
    ) -> None:
        """写入中间进度（不改 status），供前端轮询展示分阶段进度条。

        progress 会序列化为 JSON 写入 result_details_json 字段，
        前端通过 GET /flowcut/tasks/{task_id} 的 details 字段读取。

        单调性守卫：仅当新 progress_pct >= 旧值时才写入，防止并行多剧
        _plan_one_drama() 的 asyncio.gather 导致进度后退。
        """
        now = _now()
        progress_json = json.dumps(progress, ensure_ascii=False)
        new_pct = progress.get("progress_pct", 0)
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE nb_runtime_tasks
                    SET result_details_json=%s, updated_at=%s
                    WHERE task_id=%s
                      AND (
                          result_details_json IS NULL
                          OR JSON_EXTRACT(result_details_json, '$.progress_pct') IS NULL
                          OR CAST(JSON_EXTRACT(result_details_json, '$.progress_pct') AS UNSIGNED) <= %s
                      )
                    """,
                    (progress_json, now, task_id, new_pct),
                )

    async def mark_failed(
        self,
        task: TaskEnvelope,
        error: str,
        *,
        claimed_by: str | None = None,
        summary: str | None = None,
    ) -> None:
        """标记失败，记录错误摘要（截断 2000 字符防止超长）。"""
        del summary
        now = _now()
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                if claimed_by is not None:
                    await cur.execute(
                        """
                        UPDATE nb_runtime_tasks
                        SET status='failed', last_error=%s, claimed_by=%s,
                            updated_at=%s, completed_at=%s
                        WHERE task_id=%s
                        """,
                        (error[:2000], claimed_by, now, now, task.task_id),
                    )
                else:
                    await cur.execute(
                        """
                        UPDATE nb_runtime_tasks
                        SET status='failed', last_error=%s,
                            updated_at=%s, completed_at=%s
                        WHERE task_id=%s
                        """,
                        (error[:2000], now, now, task.task_id),
                    )

    # ------------------------------------------------------------------
    # Admin 查询
    # ------------------------------------------------------------------

    async def mark_task_succeeded(
        self,
        task_id: str,
        *,
        summary: str = "",
    ) -> None:
        """按 task_id 将 triggered 任务标记为业务完成。"""
        now = _now()
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE nb_runtime_tasks
                    SET status='succeeded', last_error=NULL, updated_at=%s, completed_at=%s
                    WHERE task_id=%s
                    """,
                    (now, now, task_id),
                )

    async def mark_task_failed(
        self,
        task_id: str,
        *,
        error: str,
    ) -> None:
        """按 task_id 将 triggered 任务标记为业务失败。"""
        now = _now()
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE nb_runtime_tasks
                    SET status='failed', last_error=%s, updated_at=%s, completed_at=%s
                    WHERE task_id=%s
                    """,
                    ((error or "")[:2000], now, now, task_id),
                )

    async def list_triggered(
        self,
        *,
        task_types: list[str] | tuple[str, ...],
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """列出仍处于 triggered 的任务，供业务完成监视器验证。"""
        types = [str(t) for t in task_types if str(t)]
        if not types:
            return []
        limit = max(1, min(int(limit or 100), 500))
        placeholders = ",".join(["%s"] * len(types))
        sql = f"""
            SELECT task_id, task_type, stream_name, tenant_key, session_key, scope_key,
                   status, attempt, max_attempts, queue_message_id, claimed_by,
                   last_error, payload_json, created_at, updated_at, completed_at
            FROM nb_runtime_tasks
            WHERE status='triggered' AND task_type IN ({placeholders})
            ORDER BY created_at ASC
            LIMIT %s
        """
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, tuple(types) + (limit,))
                rows = await cur.fetchall()
                cols = [d[0] for d in cur.description]

        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(zip(cols, row))
            raw_payload = item.pop("payload_json", None)
            try:
                item["payload"] = json.loads(raw_payload or "{}")
            except (TypeError, json.JSONDecodeError):
                item["payload"] = {}
            for key in ("created_at", "updated_at", "completed_at"):
                v = item.get(key)
                if isinstance(v, datetime):
                    item[key] = _to_cn_str(v)
            out.append(item)
        return out

    async def list_active(
        self,
        *,
        tenant_key: str,
        task_types: list[str] | tuple[str, ...],
        statuses: tuple[str, ...] = ("queued", "running"),
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """列出某租户下仍在途（默认 queued/running）的任务，供前端展示"生成中"占位。

        返回每条含解析后的 payload（drama_name / num_candidates / batch_id 等）。
        """
        types = [str(t) for t in task_types if str(t)]
        stats = [str(s) for s in statuses if str(s)]
        if not types or not stats or not tenant_key:
            return []
        limit = max(1, min(int(limit or 50), 200))
        type_ph = ",".join(["%s"] * len(types))
        stat_ph = ",".join(["%s"] * len(stats))
        sql = f"""
            SELECT task_id, task_type, tenant_key, session_key, status,
                   payload_json, created_at, updated_at
            FROM nb_runtime_tasks
            WHERE tenant_key=%s
              AND task_type IN ({type_ph})
              AND status IN ({stat_ph})
            ORDER BY created_at ASC
            LIMIT %s
        """
        params = (tenant_key, *types, *stats, limit)
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
                rows = await cur.fetchall()
                cols = [d[0] for d in cur.description]

        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(zip(cols, row))
            raw_payload = item.pop("payload_json", None)
            try:
                item["payload"] = json.loads(raw_payload or "{}")
            except (TypeError, json.JSONDecodeError):
                item["payload"] = {}
            for key in ("created_at", "updated_at"):
                v = item.get(key)
                if isinstance(v, datetime):
                    item[key] = _to_cn_str(v)
            out.append(item)
        return out

    async def find_by_task_id(self, task_id: str) -> dict[str, Any] | None:
        """按 task_id 精确查询单条任务记录。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT task_id, task_type, stream_name, tenant_key, session_key, scope_key,
                           status, attempt, max_attempts, claimed_by,
                           last_error, payload_json, result_details_json,
                           created_at, updated_at, completed_at
                    FROM nb_runtime_tasks
                    WHERE task_id=%s
                    LIMIT 1
                    """,
                    (task_id,),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                cols = [d[0] for d in cur.description]

        item = dict(zip(cols, row))
        raw_payload = item.pop("payload_json", None)
        try:
            item["payload"] = json.loads(raw_payload or "{}")
        except (TypeError, json.JSONDecodeError):
            item["payload"] = {}
        raw_details = item.pop("result_details_json", None)
        try:
            details = json.loads(raw_details) if raw_details else {}
        except (TypeError, json.JSONDecodeError):
            details = {}
        item["result_details"] = details if isinstance(details, dict) else {}
        # 便利字段：把 details 里常用的 result_url 平铺出来
        if isinstance(details, dict):
            item["result_url"] = details.get("result_url")
        else:
            item["result_url"] = None
        for key in ("created_at", "updated_at", "completed_at"):
            v = item.get(key)
            if isinstance(v, datetime):
                item[key] = _to_cn_str(v)
        return item

    async def find_latest_task_for(
        self,
        *,
        tenant_key: str,
        task_type: str,
    ) -> dict[str, Any] | None:
        """按租户和 task_type 查询最近一条 runtime task。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT task_id, task_type, stream_name, tenant_key, session_key, scope_key,
                           status, attempt, max_attempts, queue_message_id, claimed_by,
                           last_error, payload_json, created_at, updated_at, completed_at
                    FROM nb_runtime_tasks
                    WHERE tenant_key=%s AND task_type=%s
                    ORDER BY created_at DESC, updated_at DESC
                    LIMIT 1
                    """,
                    (tenant_key, task_type),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                cols = [d[0] for d in cur.description]

        item = dict(zip(cols, row))
        raw_payload = item.pop("payload_json", None)
        try:
            item["payload"] = json.loads(raw_payload or "{}")
        except (TypeError, json.JSONDecodeError):
            item["payload"] = {}
        for key in ("created_at", "updated_at", "completed_at"):
            v = item.get(key)
            if isinstance(v, datetime):
                item[key] = _to_cn_str(v)
        return item

    async def list_recent(
        self,
        *,
        tenant_key: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """返回最近 N 条任务记录（供 admin 面板展示）。

        tenant_key 空串表示不过滤；按 created_at 降序。
        """
        limit = max(1, min(int(limit or 20), 200))
        sql = """
            SELECT task_id, task_type, stream_name, tenant_key, session_key, scope_key,
                   status, attempt, max_attempts, queue_message_id, claimed_by,
                   last_error, created_at, updated_at, completed_at
            FROM nb_runtime_tasks
        """
        params: tuple = ()
        if tenant_key:
            sql += " WHERE tenant_key=%s"
            params = (tenant_key,)
        sql += " ORDER BY created_at DESC LIMIT %s"
        params = params + (limit,)

        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
                rows = await cur.fetchall()
                cols = [d[0] for d in cur.description]

        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(zip(cols, row))
            # 时间戳转字符串，方便 JSON 序列化
            for key in ("created_at", "updated_at", "completed_at"):
                v = item.get(key)
                if isinstance(v, datetime):
                    item[key] = _to_cn_str(v)
            out.append(item)
        return out
