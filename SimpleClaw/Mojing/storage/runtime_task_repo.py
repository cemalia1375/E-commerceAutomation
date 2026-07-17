"""RuntimeTaskRepository — 读写 nb_runtime_tasks 表。

RuntimeTask 只描述后台/外部业务任务的事实状态：

  queued    → 已入队
  running   → worker 已领取
  wait_external  → 已调用外部系统，等待外部结果或业务表回写
  succeeded → monitor / executor 已确认业务结果完成
  failed    → 本地失败、外部失败或 monitor 超时

DB 内部会把新 wait_external 写成 wait_external_owned，避免旧部署实例的全局 monitor
误扫本实例任务；读写边界统一归一为 wait_external。
旧数据里的 triggered / external / waiting_external 会在读写边界归一为 wait_external；
旧 executor 返回 noop 时会归一为 succeeded，并把 outcome 记录到 output_json。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from simpleclaw.runtime.task_protocol import (
    RuntimeEvidence,
    RuntimeTaskRecord,
    TaskEnvelope,
    TaskExecutionResult,
)
from Mojing.storage.database import Database


_SELECT_COLUMNS = """
    task_id, task_type, stream_name, tenant_key, session_key, scope_key,
    trace_id, service_role, tool_name, status, attempt, max_attempts,
    payload_json, output_json, queue_message_id, external_job_id,
    business_ref_type, business_ref_id, summary, claimed_by,
    last_error, created_at, updated_at, completed_at
"""
_WAIT_EXTERNAL_DB_STATUS = "wait_external_owned"
_WAIT_EXTERNAL_ALIASES = {
    "wait_external",
    _WAIT_EXTERNAL_DB_STATUS,
    "triggered",
    "external",
    "waiting_external",
}


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


class RuntimeTaskRepository:
    """nb_runtime_tasks 的异步读写封装。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def record_queued(
        self,
        task: TaskEnvelope,
        *,
        queue_message_id: str | None = None,
        tool_name: str | None = None,
        summary: str | None = None,
    ) -> RuntimeTaskRecord | None:
        """插入或刷新一条 queued 记录。"""
        now = _now()
        payload_json = json.dumps(task.payload, ensure_ascii=False)
        business_ref_type, business_ref_id = _initial_business_ref(task)
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO nb_runtime_tasks
                        (task_id, task_type, stream_name, tenant_key, session_key, scope_key,
                         trace_id, service_role, tool_name, status, attempt, max_attempts,
                         payload_json, output_json, queue_message_id, external_job_id,
                         business_ref_type, business_ref_id, summary, last_error,
                         claimed_by, created_at, updated_at, completed_at)
                    VALUES
                        (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'queued', %s, %s,
                         %s, NULL, %s, NULL, %s, %s, %s, NULL, NULL, %s, %s, NULL)
                    ON DUPLICATE KEY UPDATE
                        status='queued',
                        attempt=VALUES(attempt),
                        scope_key=VALUES(scope_key),
                        tool_name=VALUES(tool_name),
                        queue_message_id=VALUES(queue_message_id),
                        summary=VALUES(summary),
                        output_json=NULL,
                        external_job_id=NULL,
                        business_ref_type=VALUES(business_ref_type),
                        business_ref_id=VALUES(business_ref_id),
                        last_error=NULL,
                        claimed_by=NULL,
                        completed_at=NULL,
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
                        tool_name,
                        task.attempt,
                        task.max_attempts,
                        payload_json,
                        queue_message_id,
                        business_ref_type,
                        business_ref_id,
                        summary,
                        now,
                        now,
                    ),
                )
        return await self.get(task.task_id)

    async def attach_queue_message_id(
        self,
        task: TaskEnvelope | str,
        queue_message_id: str,
    ) -> RuntimeTaskRecord | None:
        """Attach queue metadata without changing the task lifecycle status."""
        now = _now()
        task_id = _task_id(task)
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE nb_runtime_tasks
                    SET queue_message_id=%s,
                        updated_at=%s
                    WHERE task_id=%s
                    """,
                    (queue_message_id, now, task_id),
                )
        return await self.get(task_id)

    async def mark_running(
        self,
        task: TaskEnvelope | str,
        *,
        claimed_by: str | None = None,
        summary: str | None = None,
    ) -> RuntimeTaskRecord | None:
        """更新为 running，记录 worker 身份。"""
        now = _now()
        task_id = _task_id(task)
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE nb_runtime_tasks
                    SET status='running',
                        claimed_by=COALESCE(%s, claimed_by),
                        summary=COALESCE(%s, summary),
                        last_error=NULL,
                        updated_at=%s,
                        completed_at=NULL
                    WHERE task_id=%s
                    """,
                    (claimed_by, summary, now, task_id),
                )
        return await self.get(task_id)

    async def mark_progress(
        self,
        task: TaskEnvelope | str,
        *,
        stage_code: str,
        progress_percent: int,
        current_title: str,
        summary: str | None = None,
        stage_name: str | None = None,
    ) -> RuntimeTaskRecord | None:
        """Merge display progress into output_json without changing lifecycle status."""
        now = _now()
        task_id = _task_id(task)
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT output_json FROM nb_runtime_tasks WHERE task_id=%s",
                    (task_id,),
                )
                row = await cur.fetchone()
                output = _parse_json(row[0] if row else None)
                percent = max(0, min(100, int(progress_percent)))
                output.update({
                    "stageCode": str(stage_code or "").strip(),
                    "progress": percent,
                    "progressPercent": percent,
                    "currentTitle": str(current_title or "").strip(),
                    "progressUpdatedAt": now,
                })
                if stage_name:
                    output["stageName"] = str(stage_name).strip()
                await cur.execute(
                    """
                    UPDATE nb_runtime_tasks
                    SET output_json=%s,
                        summary=COALESCE(%s, summary),
                        updated_at=%s
                    WHERE task_id=%s AND status NOT IN ('succeeded', 'failed')
                    """,
                    (_json_or_none(output), summary, now, task_id),
                )
        return await self.get(task_id)

    async def mark_wait_external(
        self,
        task: TaskEnvelope | str,
        *,
        external_job_id: str | None = None,
        summary: str | None = None,
        evidence: RuntimeEvidence | list[RuntimeEvidence] | None = None,
    ) -> RuntimeTaskRecord | None:
        """标记为 wait_external：外部系统已受理，等待 monitor 确认业务结果。"""
        del evidence
        now = _now()
        task_id = _task_id(task)
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE nb_runtime_tasks
                    SET status=%s,
                        external_job_id=COALESCE(%s, external_job_id),
                        summary=COALESCE(%s, summary),
                        last_error=NULL,
                        updated_at=%s,
                        completed_at=NULL
                    WHERE task_id=%s
                    """,
                    (_WAIT_EXTERNAL_DB_STATUS, external_job_id, summary, now, task_id),
                )
        return await self.get(task_id)

    async def mark_waiting_external(
        self,
        task: TaskEnvelope | str,
        *,
        external_job_id: str | None = None,
        summary: str | None = None,
        evidence: RuntimeEvidence | list[RuntimeEvidence] | None = None,
    ) -> RuntimeTaskRecord | None:
        """兼容旧协议名称，实际写入 wait_external。"""
        return await self.mark_wait_external(
            task,
            external_job_id=external_job_id,
            summary=summary,
            evidence=evidence,
        )

    async def mark_succeeded(
        self,
        task: TaskEnvelope | str,
        *,
        summary: str | None = None,
        business_ref_type: str | None = None,
        business_ref_id: str | None = None,
        output_json: dict[str, Any] | None = None,
        evidence: RuntimeEvidence | list[RuntimeEvidence] | None = None,
    ) -> RuntimeTaskRecord | None:
        """标记业务完成。"""
        del evidence
        now = _now()
        task_id = _task_id(task)
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE nb_runtime_tasks
                    SET status='succeeded',
                        summary=COALESCE(%s, summary),
                        business_ref_type=COALESCE(%s, business_ref_type),
                        business_ref_id=COALESCE(%s, business_ref_id),
                        output_json=%s,
                        last_error=NULL,
                        updated_at=%s,
                        completed_at=%s
                    WHERE task_id=%s
                    """,
                    (
                        summary,
                        business_ref_type,
                        business_ref_id,
                        _json_or_none(output_json),
                        now,
                        now,
                        task_id,
                    ),
                )
        return await self.get(task_id)

    async def mark_failed(
        self,
        task: TaskEnvelope | str,
        error: str,
        *,
        claimed_by: str | None = None,
        summary: str | None = None,
        evidence: RuntimeEvidence | list[RuntimeEvidence] | None = None,
    ) -> RuntimeTaskRecord | None:
        """标记失败，记录错误摘要。"""
        del evidence
        now = _now()
        task_id = _task_id(task)
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE nb_runtime_tasks
                    SET status='failed',
                        last_error=%s,
                        claimed_by=COALESCE(%s, claimed_by),
                        summary=COALESCE(%s, summary),
                        updated_at=%s,
                        completed_at=%s
                    WHERE task_id=%s
                    """,
                    ((error or "")[:2000], claimed_by, summary, now, now, task_id),
                )
        return await self.get(task_id)

    async def mark_finished(
        self,
        task: TaskEnvelope,
        result: TaskExecutionResult,
    ) -> RuntimeTaskRecord | None:
        """根据 executor 结果归一写入 RuntimeTask 状态。"""
        status = _normalize_status(result.status)
        details = dict(result.details or {})
        if status == "failed":
            return await self.mark_failed(
                task,
                result.error or "runtime task failed",
                summary=result.summary,
                evidence=result.evidence,
            )
        if status == "wait_external":
            return await self.mark_wait_external(
                task,
                external_job_id=_optional_str(details.get("external_job_id")),
                summary=result.summary,
                evidence=result.evidence,
            )
        output_json = dict(details)
        if str(result.status or "").strip().lower() == "noop":
            output_json["outcome"] = "noop"
        return await self.mark_succeeded(
            task,
            summary=result.summary,
            business_ref_type=_optional_str(details.get("business_ref_type")),
            business_ref_id=_optional_str(details.get("business_ref_id")),
            output_json=output_json or None,
            evidence=result.evidence,
        )

    async def mark_task_succeeded(
        self,
        task_id: str,
        *,
        summary: str = "",
    ) -> None:
        """按 task_id 将 wait_external 任务标记为业务完成。"""
        await self.mark_succeeded(str(task_id), summary=summary or None)

    async def mark_task_failed(
        self,
        task_id: str,
        *,
        error: str,
    ) -> None:
        """按 task_id 将 wait_external 任务标记为业务失败。"""
        await self.mark_failed(str(task_id), error)

    async def list_wait_external(
        self,
        *,
        task_types: list[str] | tuple[str, ...],
        limit: int = 100,
        claimed_by_values: list[str] | tuple[str, ...] | None = None,
        claimed_by_hosts: list[str] | tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """列出仍处于 wait_external 的任务，兼容旧 triggered / external / waiting_external 数据。"""
        return await self._list_by_statuses(
            statuses=tuple(_WAIT_EXTERNAL_ALIASES),
            task_types=task_types,
            limit=limit,
            claimed_by_values=claimed_by_values,
            claimed_by_hosts=claimed_by_hosts,
        )

    async def list_triggered(
        self,
        *,
        task_types: list[str] | tuple[str, ...],
        limit: int = 100,
        claimed_by_values: list[str] | tuple[str, ...] | None = None,
        claimed_by_hosts: list[str] | tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """兼容旧 monitor 调用名。"""
        return await self.list_wait_external(
            task_types=task_types,
            limit=limit,
            claimed_by_values=claimed_by_values,
            claimed_by_hosts=claimed_by_hosts,
        )

    async def get(self, task_id: str) -> RuntimeTaskRecord | None:
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT {_SELECT_COLUMNS}
                    FROM nb_runtime_tasks
                    WHERE task_id=%s
                    """,
                    (str(task_id),),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                cols = [d[0] for d in cur.description]
        return _record_from_item(dict(zip(cols, row)))

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
                    f"""
                    SELECT {_SELECT_COLUMNS}
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
        return _dict_from_item(dict(zip(cols, row)))

    async def has_succeeded_task_for(
        self,
        *,
        tenant_key: str,
        task_type: str,
    ) -> bool:
        """Return whether this tenant has ever completed this task type."""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT 1
                    FROM nb_runtime_tasks
                    WHERE tenant_key=%s AND task_type=%s AND status='succeeded'
                    LIMIT 1
                    """,
                    (tenant_key, task_type),
                )
                row = await cur.fetchone()
        return row is not None

    async def find_latest_succeeded_task_for(
        self,
        *,
        tenant_key: str,
        task_type: str,
    ) -> dict[str, Any] | None:
        """按租户和 task_type 查询最近一条已成功完成的 runtime task。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT {_SELECT_COLUMNS}
                    FROM nb_runtime_tasks
                    WHERE tenant_key=%s AND task_type=%s AND status='succeeded'
                    ORDER BY completed_at DESC, updated_at DESC, created_at DESC
                    LIMIT 1
                    """,
                    (tenant_key, task_type),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                cols = [d[0] for d in cur.description]
        return _dict_from_item(dict(zip(cols, row)))

    async def find_latest_active_task_for(
        self,
        *,
        tenant_key: str,
        task_type: str,
        session_key: str | None = None,
    ) -> dict[str, Any] | None:
        """按租户、可选 session 和 task_type 查询最近一条 active task。"""
        tenant_key = str(tenant_key or "").strip()
        task_type = str(task_type or "").strip()
        session_key = str(session_key or "").strip()
        if not tenant_key or not task_type:
            return None

        statuses = ("queued", "running", *_WAIT_EXTERNAL_ALIASES)
        status_placeholders = ",".join(["%s"] * len(statuses))
        params: list[Any] = [tenant_key, task_type, *statuses]
        session_clause = ""
        if session_key:
            session_clause = " AND session_key=%s"
            params.append(session_key)

        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT {_SELECT_COLUMNS}
                    FROM nb_runtime_tasks
                    WHERE tenant_key=%s
                      AND task_type=%s
                      AND status IN ({status_placeholders})
                      {session_clause}
                    ORDER BY created_at DESC, updated_at DESC
                    LIMIT 1
                    """,
                    tuple(params),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                cols = [d[0] for d in cur.description]
        return _dict_from_item(dict(zip(cols, row)))

    async def list_active_obligation_tasks(
        self,
        *,
        tenant_key: str,
        action_keys: list[str] | tuple[str, ...] = (),
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List active runtime tasks dispatched from durable obligations."""
        tenant_key = str(tenant_key or "").strip()
        if not tenant_key:
            return []
        limit = max(1, min(int(limit or 20), 100))
        statuses = ("queued", "running", *_WAIT_EXTERNAL_ALIASES)
        params: list[Any] = [tenant_key, *statuses]
        status_placeholders = ",".join(["%s"] * len(statuses))
        action_clause = ""
        keys = [str(key).strip() for key in action_keys if str(key).strip()]
        if keys:
            action_placeholders = ",".join(["%s"] * len(keys))
            action_clause = (
                " AND JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.action_key')) "
                f"IN ({action_placeholders})"
            )
            params.extend(keys)
        params.append(limit)
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT {_SELECT_COLUMNS}
                    FROM nb_runtime_tasks
                    WHERE tenant_key=%s
                      AND status IN ({status_placeholders})
                      AND service_role='mojing:obligation-dispatch'
                      AND JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.source'))='obligation'
                      {action_clause}
                    ORDER BY created_at DESC, updated_at DESC
                    LIMIT %s
                    """,
                    tuple(params),
                )
                rows = await cur.fetchall()
                cols = [d[0] for d in cur.description]
        return [_dict_from_item(dict(zip(cols, row))) for row in rows]

    async def find_latest_by_scope_key(
        self,
        *,
        tenant_key: str,
        task_type: str,
        scope_key: str,
    ) -> dict[str, Any] | None:
        """按租户、task_type 和 scope_key 查询最近一条 runtime task。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT {_SELECT_COLUMNS}
                    FROM nb_runtime_tasks
                    WHERE tenant_key=%s AND task_type=%s AND scope_key=%s
                    ORDER BY created_at DESC, updated_at DESC
                    LIMIT 1
                    """,
                    (tenant_key, task_type, scope_key),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                cols = [d[0] for d in cur.description]
        return _dict_from_item(dict(zip(cols, row)))

    async def find_latest_by_source_task_id(
        self,
        *,
        tenant_key: str,
        task_type: str,
        source_task_id: str,
    ) -> dict[str, Any] | None:
        """按 payload.source_task_id 查询最近一条下游 runtime task。"""
        source_task_id = str(source_task_id or "").strip()
        if not source_task_id:
            return None
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT {_SELECT_COLUMNS}
                    FROM nb_runtime_tasks
                    WHERE tenant_key=%s
                      AND task_type=%s
                      AND JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.source_task_id'))=%s
                    ORDER BY created_at DESC, updated_at DESC
                    LIMIT 1
                    """,
                    (tenant_key, task_type, source_task_id),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                cols = [d[0] for d in cur.description]
        return _dict_from_item(dict(zip(cols, row)))

    async def find_latest_handoff_dispatch(
        self,
        *,
        tenant_key: str,
        session_key: str,
        action_key: str,
    ) -> dict[str, Any] | None:
        """Find the latest subagent handoff dispatch for a target subagent session."""
        tenant_key = str(tenant_key or "").strip()
        session_key = str(session_key or "").strip()
        action_key = str(action_key or "").strip()
        if not tenant_key or not session_key or not action_key:
            return None
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT {_SELECT_COLUMNS}
                    FROM nb_runtime_tasks
                    WHERE tenant_key=%s
                      AND session_key=%s
                      AND task_type='subagent_dispatch'
                      AND JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.action_key'))=%s
                      AND JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.handoff_contract.intent'))='handoff'
                    ORDER BY created_at DESC, updated_at DESC
                    LIMIT 1
                    """,
                    (tenant_key, session_key, action_key),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                cols = [d[0] for d in cur.description]
        return _dict_from_item(dict(zip(cols, row)))

    async def find_latest_by_parent_handoff_task_id(
        self,
        *,
        tenant_key: str,
        task_type: str,
        parent_handoff_task_id: str,
    ) -> dict[str, Any] | None:
        """Find the business task spawned by a specific subagent handoff dispatch."""
        parent_handoff_task_id = str(parent_handoff_task_id or "").strip()
        if not tenant_key or not task_type or not parent_handoff_task_id:
            return None
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT {_SELECT_COLUMNS}
                    FROM nb_runtime_tasks
                    WHERE tenant_key=%s
                      AND task_type=%s
                      AND JSON_UNQUOTE(JSON_EXTRACT(payload_json, '$.parent_handoff_task_id'))=%s
                    ORDER BY created_at DESC, updated_at DESC
                    LIMIT 1
                    """,
                    (tenant_key, task_type, parent_handoff_task_id),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                cols = [d[0] for d in cur.description]
        return _dict_from_item(dict(zip(cols, row)))

    async def list_recent(
        self,
        *,
        tenant_key: str = "",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """返回最近 N 条任务记录，供 admin 面板展示。"""
        limit = max(1, min(int(limit or 20), 200))
        sql = f"SELECT {_SELECT_COLUMNS} FROM nb_runtime_tasks"
        params: tuple[Any, ...] = ()
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
        return [_dict_from_item(dict(zip(cols, row))) for row in rows]

    async def list_recent_updates(
        self,
        *,
        tenant_key: str | None = None,
        session_key: str | None = None,
        since_ms: int | None = None,
        limit: int = 20,
    ) -> list[RuntimeTaskRecord]:
        """按 updated_at 返回最近状态变更。"""
        limit = max(1, min(int(limit or 20), 200))
        where: list[str] = []
        params: list[Any] = []
        if tenant_key:
            where.append("tenant_key=%s")
            params.append(tenant_key)
        if session_key:
            where.append("session_key=%s")
            params.append(session_key)
        if since_ms is not None:
            where.append("updated_at >= %s")
            params.append(datetime.utcfromtimestamp(int(since_ms) / 1000).strftime("%Y-%m-%d %H:%M:%S"))
        sql = f"SELECT {_SELECT_COLUMNS} FROM nb_runtime_tasks"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_at DESC LIMIT %s"
        params.append(limit)

        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, tuple(params))
                rows = await cur.fetchall()
                cols = [d[0] for d in cur.description]
        return [_record_from_item(dict(zip(cols, row))) for row in rows]

    async def record_evidence(
        self,
        task_id: str,
        evidence: RuntimeEvidence | list[RuntimeEvidence],
    ) -> list[RuntimeEvidence]:
        """当前 MySQL 表暂不单独存 evidence，保留协议入口。"""
        del task_id
        if isinstance(evidence, list):
            return evidence
        return [evidence]

    async def list_evidence(self, task_id: str) -> list[RuntimeEvidence]:
        del task_id
        return []

    async def _list_by_statuses(
        self,
        *,
        statuses: tuple[str, ...],
        task_types: list[str] | tuple[str, ...],
        limit: int,
        claimed_by_values: list[str] | tuple[str, ...] | None = None,
        claimed_by_hosts: list[str] | tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        types = [str(t) for t in task_types if str(t)]
        if not types:
            return []
        limit = max(1, min(int(limit or 100), 500))
        type_placeholders = ",".join(["%s"] * len(types))
        status_placeholders = ",".join(["%s"] * len(statuses))
        claimed_by_clause = ""
        claimed_by_params: tuple[Any, ...] = ()
        exact_claimed_by = tuple(str(v or "").strip() for v in claimed_by_values or () if str(v or "").strip())
        if exact_claimed_by:
            exact_placeholders = ",".join(["%s"] * len(exact_claimed_by))
            claimed_by_clause = f" AND claimed_by IN ({exact_placeholders})"
            claimed_by_params = exact_claimed_by
        else:
            claimed_by_patterns = _claimed_by_host_patterns(claimed_by_hosts)
            if claimed_by_patterns:
                claimed_by_clause = " AND (" + " OR ".join(["claimed_by LIKE %s"] * len(claimed_by_patterns)) + ")"
                claimed_by_params = tuple(claimed_by_patterns)
        sql = f"""
            SELECT {_SELECT_COLUMNS}
            FROM nb_runtime_tasks
            WHERE status IN ({status_placeholders}) AND task_type IN ({type_placeholders})
            {claimed_by_clause}
            ORDER BY created_at ASC
            LIMIT %s
        """
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, tuple(statuses) + tuple(types) + claimed_by_params + (limit,))
                rows = await cur.fetchall()
                cols = [d[0] for d in cur.description]
        return [_dict_from_item(dict(zip(cols, row))) for row in rows]


def _task_id(task: TaskEnvelope | str) -> str:
    return task.task_id if isinstance(task, TaskEnvelope) else str(task)


def _normalize_status(value: Any) -> str:
    status = str(value or "").strip().lower()
    if status in _WAIT_EXTERNAL_ALIASES:
        return "wait_external"
    if status == "noop":
        return "succeeded"
    return status or "queued"


def _initial_business_ref(task: TaskEnvelope) -> tuple[str | None, str | None]:
    payload = dict(task.payload or {})
    ref_type = _optional_str(payload.get("business_ref_type"))
    ref_id = _optional_str(payload.get("business_ref_id"))
    if ref_type or ref_id:
        return ref_type, ref_id
    if str(task.task_type or "").strip() == "image_analysis":
        job_id = _optional_str(payload.get("job_id"))
        if job_id:
            return "image_analysis_job", job_id
    return None, None


def _json_or_none(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _claimed_by_host_patterns(hosts: list[str] | tuple[str, ...] | None) -> list[str]:
    patterns: list[str] = []
    for host in hosts or ():
        text = str(host or "").strip()
        if text:
            patterns.append(f"%:{text}:%")
    return patterns


def _parse_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _dict_from_item(item: dict[str, Any]) -> dict[str, Any]:
    out = dict(item)
    out["status"] = _normalize_status(out.get("status"))
    out["payload"] = _parse_json(out.pop("payload_json", None))
    out["output"] = _parse_json(out.pop("output_json", None))
    for key in ("created_at", "updated_at", "completed_at"):
        value = out.get(key)
        if isinstance(value, datetime):
            out[key] = value.strftime("%Y-%m-%d %H:%M:%S")
    return out


def _record_from_item(item: dict[str, Any]) -> RuntimeTaskRecord:
    data = _dict_from_item(item)
    return RuntimeTaskRecord(
        task_id=str(data.get("task_id") or ""),
        task_type=str(data.get("task_type") or ""),
        status=_normalize_status(data.get("status")),  # type: ignore[arg-type]
        tenant_key=_optional_str(data.get("tenant_key")),
        session_key=_optional_str(data.get("session_key")),
        trace_id=_optional_str(data.get("trace_id")),
        tool_name=_optional_str(data.get("tool_name")),
        queue_message_id=_optional_str(data.get("queue_message_id")),
        external_job_id=_optional_str(data.get("external_job_id")),
        business_ref_type=_optional_str(data.get("business_ref_type")),
        business_ref_id=_optional_str(data.get("business_ref_id")),
        summary=_optional_str(data.get("summary")),
        error=_optional_str(data.get("last_error")),
        input_json=dict(data.get("payload") or {}),
        output_json=dict(data.get("output") or {}) or None,
        created_at_ms=_datetime_ms(data.get("created_at")),
        updated_at_ms=_datetime_ms(data.get("updated_at")),
    )


def _datetime_ms(value: Any) -> int:
    if isinstance(value, datetime):
        return int(value.timestamp() * 1000)
    text = str(value or "").strip()
    if not text:
        return 0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return int(datetime.strptime(text, fmt).timestamp() * 1000)
        except ValueError:
            continue
    return 0


def _optional_str(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
