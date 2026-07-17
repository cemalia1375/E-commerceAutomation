"""孤儿 runtime task 重入队。

启动时调用 reconcile_orphan_tasks() 一次：
- 任何 status='running' 行：进程已重启 → 没有活的 owner 还在跑它 → 全部重入队
- 任何 status='queued' 行：内存队列在新进程里是空的 → DB 里 queued 的全是孤儿 → 全部重入队
- 重建 TaskEnvelope（保留 task_id，使 record_queued 的 ON DUPLICATE KEY UPDATE
  把 claimed_by/last_error/completed_at 清零、status 回到 queued）
- 通过 runtime.submit_task() 把消息重新放进内存队列

只重入队仍有重试余量（attempt < max_attempts）的任务。

历史阈值 `threshold_seconds` 参数保留，仅作早期版本兼容；当 InMemoryTaskQueue 在
进程内时，启动那一刻所有 queued/running 都是孤儿，无需 staleness 判断。
"""
from __future__ import annotations

import json
from typing import Any

from loguru import logger

from simpleclaw.runtime.services import RuntimeServices
from simpleclaw.runtime.task_protocol import TaskEnvelope
from Flowcut.storage.database import Database


async def _fetch_orphan_rows(
    db: Database, threshold_seconds: int,
) -> list[dict[str, Any]]:
    """扫所有 status IN ('queued','running') 且仍有重试余量的行。

    `threshold_seconds` > 0 时仍按 staleness 过滤（保留兼容）；
    = 0 时（默认）不过滤，把 startup 时全部孤儿都捞上来。
    """
    if threshold_seconds and threshold_seconds > 0:
        where_stale = "AND updated_at < (NOW() - INTERVAL %s SECOND)"
        params: tuple = (threshold_seconds,)
    else:
        where_stale = ""
        params = ()
    sql = f"""
        SELECT task_id, task_type, stream_name, tenant_key, session_key,
               scope_key, trace_id, service_role, attempt, max_attempts,
               payload_json, status, updated_at
        FROM nb_runtime_tasks
        WHERE status IN ('queued', 'running')
          AND attempt < max_attempts
          {where_stale}
        ORDER BY created_at
    """
    async with db.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, params)
            rows = await cur.fetchall()
            cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]


def _row_to_envelope(row: dict[str, Any]) -> TaskEnvelope:
    payload: dict[str, Any]
    raw_payload = row.get("payload_json") or "{}"
    try:
        payload = json.loads(raw_payload)
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}

    return TaskEnvelope(
        task_id=str(row["task_id"]),
        task_type=str(row["task_type"]),
        stream=str(row["stream_name"]),
        tenant_key=row.get("tenant_key"),
        session_key=row.get("session_key"),
        scope_key=row.get("scope_key"),
        trace_id=str(row.get("trace_id") or ""),
        service_role=row.get("service_role"),
        attempt=int(row.get("attempt") or 0),
        max_attempts=int(row.get("max_attempts") or 3),
        payload=payload,
    )


async def reconcile_orphan_tasks(
    db: Database,
    runtime: RuntimeServices,
    *,
    threshold_seconds: int = 0,
) -> int:
    """重入队所有超过 threshold_seconds 仍处于 queued/running 的孤儿任务。

    Returns:
        实际成功重入队的任务数。
    """
    rows = await _fetch_orphan_rows(db, threshold_seconds)
    if not rows:
        logger.info("orphan reconciler: no stale tasks found")
        return 0

    logger.info(
        "orphan reconciler: found {} stale task(s) (threshold={}s)",
        len(rows), threshold_seconds,
    )

    succeeded = 0
    for row in rows:
        envelope = _row_to_envelope(row)
        try:
            await runtime.submit_task(envelope)
            succeeded += 1
            logger.info(
                "orphan reconciler: re-enqueued task_id={} type={} prev_status={}",
                envelope.task_id, envelope.task_type, row.get("status"),
            )
        except Exception as exc:
            logger.warning(
                "orphan reconciler: failed to re-enqueue task_id={} type={}: {}",
                envelope.task_id, envelope.task_type, exc,
            )
    return succeeded
