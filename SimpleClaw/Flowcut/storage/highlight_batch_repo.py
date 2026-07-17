"""Cross-episode highlight batch pipeline state repository.

Manages fc_highlight_batch (orchestrator state) and fc_highlight_stage
(per-stage execution records).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

import aiomysql

from Flowcut.storage.database import Database


class HighlightBatchRepository:
    """CRUD for fc_highlight_batch + fc_highlight_stage."""

    def __init__(self, db: Database) -> None:
        self._db = db

    # ── Batch ──────────────────────────────────────────────────────────

    async def create_batch(
        self,
        *,
        tenant_key: str,
        drama_name: str,
        num_candidates: int = 3,
        batch_id: str | None = None,
    ) -> dict:
        """Create a new highlight batch record."""
        bid = batch_id or uuid.uuid4().hex
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO fc_highlight_batch
                        (batch_id, tenant_key, drama_name, num_candidates, status)
                    VALUES (%s, %s, %s, %s, 'EPISODE_PREP')
                    """,
                    (bid, tenant_key, drama_name, num_candidates),
                )
                return await self.get_batch(bid)

    async def get_batch(self, batch_id: str) -> dict | None:
        async with self._db.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT * FROM fc_highlight_batch WHERE batch_id = %s",
                    (batch_id,),
                )
                row = await cur.fetchone()
                return dict(row) if row else None

    async def update_status(self, batch_id: str, status: str) -> None:
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE fc_highlight_batch SET status = %s WHERE batch_id = %s",
                    (status, batch_id),
                )

    async def update_orchestrator_state(
        self, batch_id: str, state: dict
    ) -> None:
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE fc_highlight_batch SET orchestrator_state_json = %s WHERE batch_id = %s",
                    (json.dumps(state, ensure_ascii=False), batch_id),
                )

    async def set_merged_shots(
        self, batch_id: str, shots: list[dict]
    ) -> None:
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE fc_highlight_batch SET merged_shots_json = %s WHERE batch_id = %s",
                    (json.dumps(shots, ensure_ascii=False), batch_id),
                )

    async def set_summary(self, batch_id: str, summary: dict) -> None:
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE fc_highlight_batch SET summary_json = %s WHERE batch_id = %s",
                    (json.dumps(summary, ensure_ascii=False), batch_id),
                )

    async def list_active(self, tenant_key: str) -> list[dict]:
        """List batches that are not in a terminal state."""
        async with self._db.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    """
                    SELECT * FROM fc_highlight_batch
                    WHERE tenant_key = %s
                      AND status NOT IN ('READY', 'PARTIAL', 'FAILED', 'CANCELLED')
                    ORDER BY created_at DESC
                    """,
                    (tenant_key,),
                )
                return [dict(r) for r in await cur.fetchall()]

    async def list_all_active(self, limit: int = 500) -> list[dict]:
        """List non-terminal batches across tenants for startup recovery."""
        limit = max(1, min(int(limit or 500), 2000))
        async with self._db.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    """
                    SELECT * FROM fc_highlight_batch
                    WHERE status NOT IN ('READY', 'PARTIAL', 'FAILED', 'CANCELLED')
                    ORDER BY created_at ASC
                    LIMIT %s
                    """,
                    (limit,),
                )
                return [dict(r) for r in await cur.fetchall()]

    async def fail_stale_active(self, max_age_hours: int = 6) -> int:
        """Fail abandoned batches so startup recovery does not create a task flood."""
        hours = max(1, min(int(max_age_hours or 6), 168))
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    UPDATE fc_highlight_batch
                    SET status = 'FAILED',
                        summary_json = JSON_OBJECT(
                            'error', 'stale batch expired during startup recovery'
                        )
                    WHERE status NOT IN ('READY', 'PARTIAL', 'FAILED', 'CANCELLED')
                      AND created_at < DATE_SUB(UTC_TIMESTAMP(), INTERVAL {hours} HOUR)
                    """
                )
                return int(cur.rowcount or 0)

    async def close_terminal_runtime_tasks(self) -> int:
        """Prevent generic orphan recovery from reviving terminal batch children."""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE nb_runtime_tasks AS task
                    INNER JOIN fc_highlight_batch AS batch
                      ON JSON_UNQUOTE(
                           JSON_EXTRACT(task.payload_json, '$.batch_id')
                         ) = batch.batch_id
                    SET task.status = 'failed',
                        task.last_error = 'parent highlight batch is terminal',
                        task.updated_at = UTC_TIMESTAMP(),
                        task.completed_at = UTC_TIMESTAMP()
                    WHERE batch.status IN ('READY', 'PARTIAL', 'FAILED', 'CANCELLED')
                      AND task.status IN ('queued', 'running')
                      AND task.task_type IN (
                          'highlight_batch',
                          'episode_prepare',
                          'merge_decompose',
                          'start_select',
                          'span_plan'
                      )
                    """
                )
                return int(cur.rowcount or 0)

    async def list_by_drama(
        self, tenant_key: str, drama_name: str, limit: int = 50
    ) -> list[dict]:
        async with self._db.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    """
                    SELECT * FROM fc_highlight_batch
                    WHERE tenant_key = %s AND drama_name = %s
                    ORDER BY created_at DESC LIMIT %s
                    """,
                    (tenant_key, drama_name, limit),
                )
                return [dict(r) for r in await cur.fetchall()]

    # ── Stage ──────────────────────────────────────────────────────────

    async def create_stage(
        self,
        *,
        batch_id: str,
        stage: str,
        episode_no: int | None = None,
        candidate_idx: int | None = None,
        input_json: dict | None = None,
    ) -> dict:
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO fc_highlight_stage
                        (batch_id, stage, episode_no, candidate_idx, input_json, status)
                    VALUES (%s, %s, %s, %s, %s, 'PENDING')
                    """,
                    (
                        batch_id, stage, episode_no, candidate_idx,
                        json.dumps(input_json, ensure_ascii=False) if input_json else None,
                    ),
                )
                sid = cur.lastrowid
                return await self.get_stage(sid)

    async def get_stage(self, stage_id: int) -> dict | None:
        async with self._db.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT * FROM fc_highlight_stage WHERE id = %s",
                    (stage_id,),
                )
                row = await cur.fetchone()
                return dict(row) if row else None

    async def mark_stage_running(
        self, stage_id: int, runtime_task_id: str | None = None
    ) -> None:
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE fc_highlight_stage
                    SET status = 'PROCESSING',
                        runtime_task_id = COALESCE(%s, runtime_task_id),
                        started_at = NOW()
                    WHERE id = %s
                    """,
                    (runtime_task_id, stage_id),
                )

    async def try_mark_stage_running(
        self, stage_id: int, runtime_task_id: str | None = None
    ) -> bool:
        """Atomically claim a pending stage for a runtime task."""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE fc_highlight_stage
                    SET status = 'PROCESSING',
                        runtime_task_id = COALESCE(%s, runtime_task_id),
                        started_at = COALESCE(started_at, NOW())
                    WHERE id = %s AND status = 'PENDING'
                    """,
                    (runtime_task_id, stage_id),
                )
                return int(cur.rowcount or 0) > 0

    async def mark_stage_ready(
        self,
        stage_id: int,
        *,
        creative_id: int | None = None,
        result_json: dict | None = None,
    ) -> None:
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE fc_highlight_stage
                    SET status = 'READY',
                        creative_id = COALESCE(%s, creative_id),
                        result_json = COALESCE(%s, result_json),
                        completed_at = NOW()
                    WHERE id = %s
                    """,
                    (
                        creative_id,
                        json.dumps(result_json, ensure_ascii=False) if result_json else None,
                        stage_id,
                    ),
                )

    async def mark_stage_failed(
        self, stage_id: int, error: str
    ) -> None:
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE fc_highlight_stage
                    SET status = 'FAILED', error = %s, completed_at = NOW()
                    WHERE id = %s
                    """,
                    (error[:2000], stage_id),
                )

    async def mark_stage_retry_pending(
        self, stage_id: int, error: str
    ) -> None:
        """Return a transiently failed stage to PENDING so the same task can retry."""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE fc_highlight_stage
                    SET status = 'PENDING',
                        runtime_task_id = NULL,
                        error = %s,
                        started_at = NULL,
                        completed_at = NULL
                    WHERE id = %s
                    """,
                    (error[:2000], stage_id),
                )

    async def mark_stage_skipped(self, stage_id: int, reason: str = "") -> None:
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE fc_highlight_stage
                    SET status = 'SKIPPED', error = %s, completed_at = NOW()
                    WHERE id = %s
                    """,
                    (reason[:2000], stage_id),
                )

    async def list_stages(
        self,
        batch_id: str,
        stage: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        clauses = ["batch_id = %s"]
        params: list[Any] = [batch_id]
        if stage:
            clauses.append("stage = %s")
            params.append(stage)
        if status:
            clauses.append("status = %s")
            params.append(status)
        where = " AND ".join(clauses)
        async with self._db.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    f"SELECT * FROM fc_highlight_stage WHERE {where} ORDER BY id",
                    tuple(params),
                )
                return [dict(r) for r in await cur.fetchall()]

    async def count_stages_by_status(
        self, batch_id: str, stage: str
    ) -> dict[str, int]:
        """Return {status: count} for stages of a given type in a batch."""
        async with self._db.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    """
                    SELECT status, COUNT(*) AS cnt
                    FROM fc_highlight_stage
                    WHERE batch_id = %s AND stage = %s
                    GROUP BY status
                    """,
                    (batch_id, stage),
                )
                counts: dict[str, int] = {}
                for row in await cur.fetchall():
                    counts[row["status"]] = row["cnt"]
                return counts

    async def cancel_pending_stages(self, batch_id: str) -> int:
        """Cancel all PENDING stages for a batch. Returns count of affected rows."""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE fc_highlight_stage
                    SET status = 'CANCELLED', completed_at = NOW()
                    WHERE batch_id = %s AND status = 'PENDING'
                    """,
                    (batch_id,),
                )
                return cur.rowcount

    async def reset_stages_for_retry(
        self,
        batch_id: str,
        *,
        stages: tuple[str, ...] = ("span_plan",),
    ) -> int:
        """Reset retryable child stages so a failed batch can actually re-run them."""
        if not stages:
            return 0
        placeholders = ", ".join(["%s"] * len(stages))
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    UPDATE fc_highlight_stage
                    SET status = 'PENDING',
                        runtime_task_id = NULL,
                        error = NULL,
                        creative_id = NULL,
                        result_json = NULL,
                        started_at = NULL,
                        completed_at = NULL
                    WHERE batch_id = %s
                      AND stage IN ({placeholders})
                      AND status IN ('FAILED', 'SKIPPED', 'CANCELLED')
                    """,
                    (batch_id, *stages),
                )
                return int(cur.rowcount or 0)

    # ── Composite helpers ──────────────────────────────────────────────

    async def all_stages_completed(
        self, batch_id: str, stage: str, expected_total: int
    ) -> bool:
        """Check whether all stages of a given type have reached a terminal state."""
        counts = await self.count_stages_by_status(batch_id, stage)
        terminal = (
            counts.get("READY", 0)
            + counts.get("FAILED", 0)
            + counts.get("SKIPPED", 0)
            + counts.get("CANCELLED", 0)
        )
        return terminal >= expected_total

    async def get_stage_progress(self, batch_id: str) -> dict:
        """Return structured progress for all stages in a batch."""
        async with self._db.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    """
                    SELECT stage,
                           SUM(CASE WHEN status = 'READY' THEN 1 ELSE 0 END) AS done,
                           SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) AS failed,
                           COUNT(*) AS total
                    FROM fc_highlight_stage
                    WHERE batch_id = %s
                    GROUP BY stage
                    """,
                    (batch_id,),
                )
                progress: dict = {}
                for row in await cur.fetchall():
                    progress[row["stage"]] = {
                        "done": row["done"],
                        "failed": row["failed"],
                        "total": row["total"],
                    }
                return progress
