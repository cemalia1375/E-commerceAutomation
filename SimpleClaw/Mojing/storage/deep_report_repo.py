"""Read-only repository for deep analysis report tables."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from Mojing.storage.database import Database


class DeepReportRepository:
    """只读封装：JOIN nb_deep_analysis_reports + nb_agent_field_reports。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def find_latest(self, tenant_key: str) -> dict[str, Any] | None:
        """返回该用户最新一份完成态报告。"""
        latest_full = await self.find_latest_full(tenant_key)
        if latest_full is not None:
            return latest_full
        return await self._fetch_one(
            """
            WHERE a.user_id=%s AND a.status='done' AND a.deleted=0
            ORDER BY a.create_time DESC
            LIMIT 1
            """,
            (tenant_key,),
        )

    async def has_done_since(
        self,
        *,
        tenant_key: str,
        since: str | datetime,
        trace_id: str | None = None,
        report_id: str | None = None,
        session_id: str | None = None,
    ) -> bool:
        """验证异步报告是否已经真正落库完成。

        runtime task 带 trace_id 时，只按 trace_id 精确匹配报告结果；
        不能退化为 session/time 猜测，否则容易把旧报告误判为当前任务完成。
        """
        return await self.find_done_since(
            tenant_key=tenant_key,
            since=since,
            trace_id=trace_id,
            report_id=report_id,
            session_id=session_id,
        ) is not None

    async def find_done_since(
        self,
        *,
        tenant_key: str,
        since: str | datetime,
        trace_id: str | None = None,
        report_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any] | None:
        """返回 runtime task 对应的完成态报告行。"""
        if trace_id:
            return await self._find_any_status_by_trace(
                tenant_key=tenant_key,
                trace_id=trace_id,
                status="done",
            )

        slow = await self._find_slow_report_for_runtime(
            tenant_key=tenant_key,
            since=since,
            report_id=report_id,
            session_id=session_id,
        )
        if slow is not None:
            return slow if str(slow.get("status") or "").strip().lower() == "done" else None

        return await self._find_legacy_status_since(
            tenant_key=tenant_key,
            since=since,
            status="done",
            trace_id=trace_id,
            report_id=report_id,
            session_id=session_id,
        )

    async def find_error_since(
        self,
        *,
        tenant_key: str,
        since: str | datetime,
        trace_id: str | None = None,
        report_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any] | None:
        """查找异步报告是否已写入 error 状态。"""
        if trace_id:
            return await self._find_any_status_by_trace(
                tenant_key=tenant_key,
                trace_id=trace_id,
                status="error",
            )

        slow = await self._find_slow_report_for_runtime(
            tenant_key=tenant_key,
            since=since,
            report_id=report_id,
            session_id=session_id,
        )
        if slow is not None:
            status = str(slow.get("status") or "").strip().lower()
            return slow if status == "error" else None

        return await self._find_legacy_status_since(
            tenant_key=tenant_key,
            since=since,
            status="error",
            trace_id=trace_id,
            report_id=report_id,
            session_id=session_id,
        )

    async def _find_slow_report_for_runtime(
        self,
        *,
        tenant_key: str,
        since: str | datetime,
        trace_id: str | None = None,
        report_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any] | None:
        """返回 runtime monitor 应该观察的 slow-report 兼容锚点报告行。

        trace_id 精确匹配优先使用 nb_deep_analysis_reports；这里仅用于
        无 trace_id 的旧任务兼容，避免旧数据完全失联。
        """
        if report_id:
            return await self._fetch_slow_report(
                ["s.user_id=%s", "s.deleted=0", "s.report_id=%s"],
                [tenant_key, report_id],
                order_sql="s.create_time DESC",
            )
        if trace_id:
            row = await self._fetch_slow_report(
                ["s.user_id=%s", "s.deleted=0", "s.trace_id=%s"],
                [tenant_key, trace_id],
                order_sql="s.create_time DESC",
            )
            if row is not None:
                return row
        if session_id:
            return await self._fetch_slow_report(
                ["s.user_id=%s", "s.deleted=0", "s.session_id=%s", "s.create_time >= %s"],
                [tenant_key, session_id, since],
                order_sql="s.create_time ASC",
            )
        return await self._fetch_slow_report(
            ["s.user_id=%s", "s.deleted=0", "s.create_time >= %s"],
            [tenant_key, since],
            order_sql="s.create_time ASC",
        )

    async def _fetch_slow_report(
        self,
        clauses: list[str],
        params: list[Any],
        *,
        order_sql: str,
    ) -> dict[str, Any] | None:
        sql = f"""
            SELECT
                s.report_id, s.user_id, s.session_id, s.status, s.trace_id,
                s.create_time, s.update_time
            FROM nb_slow_model_reports s
            WHERE {' AND '.join(clauses)}
            ORDER BY {order_sql}
            LIMIT 1
        """
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, tuple(params))
                row = await cur.fetchone()
                cols = [d[0] for d in cur.description] if cur.description else []
        if row is None:
            return None
        item = dict(zip(cols, row))
        for key in ("create_time", "update_time"):
            value = item.get(key)
            if isinstance(value, datetime):
                item[key] = value.strftime("%Y-%m-%d %H:%M:%S")
        return item

    async def _find_analysis_status_by_trace(
        self,
        *,
        tenant_key: str,
        trace_id: str,
        status: str,
    ) -> dict[str, Any] | None:
        """Find the canonical deep analysis result by the runtime trace id."""
        return await self._fetch_one(
            "WHERE a.user_id=%s AND a.status=%s AND a.deleted=0 AND a.trace_id=%s "
            "ORDER BY a.create_time DESC LIMIT 1",
            (tenant_key, status, trace_id),
            anchor="analysis",
        )

    async def _find_agent_status_by_trace(
        self,
        *,
        tenant_key: str,
        trace_id: str,
        status: str,
    ) -> dict[str, Any] | None:
        """Find the user-facing projection by runtime trace id as a fallback."""
        return await self._fetch_one(
            "WHERE f.user_id=%s AND f.status=%s AND f.deleted=0 AND f.trace_id=%s "
            "ORDER BY f.create_time DESC LIMIT 1",
            (tenant_key, status, trace_id),
            anchor="agent",
        )

    async def _find_any_status_by_trace(
        self,
        *,
        tenant_key: str,
        trace_id: str,
        status: str,
    ) -> dict[str, Any] | None:
        analysis = await self._find_analysis_status_by_trace(
            tenant_key=tenant_key,
            trace_id=trace_id,
            status=status,
        )
        if analysis is not None:
            return analysis
        slow = await self._fetch_slow_report(
            ["s.user_id=%s", "s.deleted=0", "s.status=%s", "s.trace_id=%s"],
            [tenant_key, status, trace_id],
            order_sql="s.create_time DESC",
        )
        if slow is not None:
            return slow
        return await self._find_agent_status_by_trace(
            tenant_key=tenant_key,
            trace_id=trace_id,
            status=status,
        )

    async def _find_legacy_status_since(
        self,
        *,
        tenant_key: str,
        since: str | datetime,
        status: str,
        trace_id: str | None = None,
        report_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any] | None:
        if report_id:
            return await self._fetch_one(
                "WHERE a.user_id=%s AND a.status=%s AND a.deleted=0 AND a.report_id=%s "
                "ORDER BY a.create_time DESC LIMIT 1",
                (tenant_key, status, report_id),
            )
        if trace_id:
            row = await self._fetch_one(
                "WHERE a.user_id=%s AND a.status=%s AND a.deleted=0 AND a.trace_id=%s "
                "ORDER BY a.create_time DESC LIMIT 1",
                (tenant_key, status, trace_id),
            )
            if row is not None:
                return row
        if session_id:
            return await self._fetch_one(
                "WHERE a.user_id=%s AND a.status=%s AND a.deleted=0 "
                "AND a.session_id=%s AND a.create_time >= %s "
                "ORDER BY a.create_time ASC LIMIT 1",
                (tenant_key, status, session_id, since),
            )

        return await self._fetch_one(
            "WHERE a.user_id=%s AND a.status=%s AND a.deleted=0 AND a.create_time >= %s "
            "ORDER BY a.create_time ASC LIMIT 1",
            (tenant_key, status, since),
        )

    async def find_by_report_id_full(
        self,
        tenant_key: str,
        report_id: str,
    ) -> dict[str, Any] | None:
        """三表 JOIN 按 reportId 查指定报告（双条件 user_id + report_id 强校验）。

        仅 status='done' 且 deleted=0；返回 slow_* / deep_* / agent_* 字段。
        跨租户报告会被静默过滤（user_id 不匹配返回 None）。
        """
        return await self._fetch_one_full(
            "AND s.report_id = %s LIMIT 1",
            (tenant_key, report_id),
        )

    async def find_latest_full(self, tenant_key: str) -> dict[str, Any] | None:
        """三表 JOIN 按 create_time DESC 查该用户最新一份完成态报告。"""
        return await self._fetch_one_full(
            "ORDER BY s.create_time DESC LIMIT 1",
            (tenant_key,),
        )

    async def _fetch_one_full(
        self,
        tail_sql: str,
        params: tuple[Any, ...],
    ) -> dict[str, Any] | None:
        """新方法专用：JOIN nb_slow_model_reports + deep + agent 三张表。

        字段命名：slow_* / deep_* / agent_*，与旧方法的 analysis_* / field_* 区分，
        避免误用。LEFT JOIN 让 deep / agent 缺段时不阻塞 slow 主体读取。
        """
        sql = f"""
            SELECT
                s.report_id, s.user_id, s.session_id, s.status,
                s.model_name, s.model_version, s.trace_id,
                s.summary, s.read_status, s.notified,
                s.overview_json AS slow_overview,
                s.decode_json   AS slow_decode,
                s.secret_json   AS slow_secret,
                s.track_json    AS slow_track,
                s.create_time, s.update_time,
                d.strategy_version,
                d.overview_json AS deep_overview,
                d.decode_json   AS deep_decode,
                d.secret_json   AS deep_secret,
                d.track_json    AS deep_track,
                a.overview_json AS agent_overview,
                a.decode_json   AS agent_decode,
                a.secret_json   AS agent_secret,
                a.track_json    AS agent_track
            FROM nb_slow_model_reports s
            LEFT JOIN nb_deep_analysis_reports d
              ON d.report_id = s.report_id AND d.deleted = 0 AND d.status = 'done'
            LEFT JOIN nb_agent_field_reports a
              ON a.report_id = s.report_id AND a.deleted = 0 AND a.status = 'done'
            WHERE s.user_id = %s AND s.deleted = 0 AND s.status = 'done'
            {tail_sql}
        """
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
                row = await cur.fetchone()
                cols = [d[0] for d in cur.description] if cur.description else []
        if row is None:
            return None
        item = dict(zip(cols, row))
        json_fields = (
            "slow_overview", "slow_decode", "slow_secret", "slow_track",
            "deep_overview", "deep_decode", "deep_secret", "deep_track",
            "agent_overview", "agent_decode", "agent_secret", "agent_track",
        )
        for key in json_fields:
            item[key] = self.parse_json_field(item.get(key))
        for key in ("create_time", "update_time"):
            value = item.get(key)
            if isinstance(value, datetime):
                item[key] = value.strftime("%Y-%m-%d %H:%M:%S")
        return item

    async def _fetch_one(
        self,
        where_sql: str,
        params: tuple[Any, ...],
        *,
        anchor: str = "analysis",
    ) -> dict[str, Any] | None:
        if anchor not in {"analysis", "agent"}:
            anchor = "analysis"
        from_sql = (
            "FROM nb_agent_field_reports f "
            "LEFT JOIN nb_deep_analysis_reports a "
            "  ON a.report_id = f.report_id AND a.deleted = 0"
            if anchor == "agent"
            else
            "FROM nb_deep_analysis_reports a "
            "LEFT JOIN nb_agent_field_reports f "
            "  ON f.report_id = a.report_id AND f.deleted = 0"
        )
        identity_sql = (
            """
                        f.report_id AS report_id,
                        f.user_id AS user_id,
                        f.session_id AS session_id,
                        f.status AS status,
                        a.strategy_version,
                        f.trace_id AS trace_id,
            """
            if anchor == "agent"
            else
            """
                        a.report_id AS report_id,
                        a.user_id AS user_id,
                        a.session_id AS session_id,
                        a.status AS status,
                        a.strategy_version,
                        a.trace_id AS trace_id,
            """
        )
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT
                        {identity_sql}
                        a.overview_json AS analysis_overview,
                        a.decode_json AS analysis_decode,
                        a.secret_json AS analysis_secret,
                        a.track_json AS analysis_track,
                        a.create_time, a.update_time,
                        f.overview_json AS field_overview,
                        f.decode_json AS field_decode,
                        f.secret_json AS field_secret,
                        f.track_json AS field_track
                    {from_sql}
                    {where_sql}
                    """,
                    params,
                )
                row = await cur.fetchone()
                cols = [d[0] for d in cur.description] if cur.description else []
        if row is None:
            return None
        item = dict(zip(cols, row))
        for key in ("analysis_overview", "analysis_decode", "analysis_secret", "analysis_track",
                    "field_overview", "field_decode", "field_secret", "field_track"):
            item[key] = self.parse_json_field(item.get(key))
        for key in ("create_time", "update_time"):
            value = item.get(key)
            if isinstance(value, datetime):
                item[key] = value.strftime("%Y-%m-%d %H:%M:%S")
        return item

    @staticmethod
    def parse_json_field(raw: Any) -> Any:
        if raw is None or isinstance(raw, (dict, list)):
            return raw
        try:
            return json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return raw
