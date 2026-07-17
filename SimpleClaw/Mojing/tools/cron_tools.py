"""Cron 工具集 — 暴露给主 Agent LLM 的定时任务管理工具。

needs_followup 设计原则：
  - Add / Remove 类：False — Agent 先回复用户，工具后台写 DB，不阻塞流式输出
  - List 类：True  — Agent 需要读出任务列表内容才能回复，必须等结果

所有工具通过 set_context() 获取 tenant_key 和 session_key。
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from loguru import logger

from Mojing.utils.cron_time import next_cron_run, now_local, parse_datetime
from simpleclaw.tools.base import Tool, ToolResult
from Mojing.storage.cron_repo import CronRepository


class CronAddOnceTool(Tool):
    """Schedule a one-time task to run at a specific datetime."""

    name = "cron_add_once"
    description = (
        "Schedule a one-time task for the agent to run at a specific datetime. "
        "Use this when the user wants a reminder or action at a specific time. "
        "run_at must be a future ISO 8601 datetime computed from the current time in the system prompt."
    )
    parameters = {
        "type": "object",
        "properties": {
            "task":   {"type": "string", "description": "Instruction for the agent to execute when the time comes"},
            "run_at": {
                "type": "string",
                "description": "Future ISO 8601 datetime string based on the current time in the system prompt, e.g. '2026-05-01T08:00:00'",
            },
        },
        "required": ["task", "run_at"],
    }
    needs_followup = True
    tool_category = "sync_write"

    def __init__(self, cron_repo: CronRepository) -> None:
        self._repo = cron_repo
        self._tenant_key = "__default__"
        self._session_key = ""

    def set_context(self, *, tenant_key: str = "", session_key: str = "", **_) -> None:
        if tenant_key:
            self._tenant_key = tenant_key
        if session_key:
            self._session_key = session_key

    async def execute(self, task: str = "", run_at: str = "") -> ToolResult:
        dt = parse_datetime(run_at)
        if dt is None:
            return ToolResult(
                content=json.dumps({"ok": False, "error": f"无法解析时间：{run_at}"}, ensure_ascii=False),
                ok=False,
            )
        now = now_local()
        if dt <= now:
            return ToolResult(
                content=json.dumps(
                    {
                        "ok": False,
                        "error": (
                            f"run_at={run_at} 不是未来时间；当前时间（北京，UTC+8）是 "
                            f"{now.strftime('%Y-%m-%dT%H:%M:%S')}。请基于当前时间重新计算。"
                        ),
                        "current_time": now.strftime("%Y-%m-%dT%H:%M:%S"),
                        "timezone": "Asia/Shanghai",
                    },
                    ensure_ascii=False,
                ),
                ok=False,
            )
        job_id = await self._repo.add(
            tenant_key=self._tenant_key,
            session_key=self._session_key,
            cron_type="once",
            task=task,
            run_at=dt,
        )
        logger.info("cron_add_once: tenant={} run_at={} job_id={}", self._tenant_key, run_at, job_id)
        return ToolResult(
            content=json.dumps(
                {
                    "ok": True,
                    "action": "scheduled",
                    "run_at": run_at,
                    "message_focus": (
                        f"定时任务已设好，将在 {run_at} 触发。"
                        "请告诉用户具体触发时间和会做什么；不要重复第一轮的『我帮你设』。"
                    ),
                },
                ensure_ascii=False,
            )
        )


class CronAddIntervalTool(Tool):
    """Schedule a recurring task to run every N seconds."""

    name = "cron_add_interval"
    description = (
        "Schedule a recurring task to run every N seconds. "
        "Use this for regular check-ins or periodic reminders."
    )
    parameters = {
        "type": "object",
        "properties": {
            "task":       {"type": "string", "description": "Instruction for the agent to execute each time"},
            "interval_s": {"type": "integer", "description": "Interval in seconds between executions (e.g. 86400 for daily)"},
        },
        "required": ["task", "interval_s"],
    }
    needs_followup = True
    tool_category = "sync_write"

    def __init__(self, cron_repo: CronRepository) -> None:
        self._repo = cron_repo
        self._tenant_key = "__default__"
        self._session_key = ""

    def set_context(self, *, tenant_key: str = "", session_key: str = "", **_) -> None:
        if tenant_key:
            self._tenant_key = tenant_key
        if session_key:
            self._session_key = session_key

    async def execute(self, task: str = "", interval_s: int = 0) -> ToolResult:
        if interval_s <= 0:
            return ToolResult(content=json.dumps({"ok": False, "error": "interval_s 必须大于 0"}), ok=False)
        first_run = now_local() + timedelta(seconds=interval_s)
        job_id = await self._repo.add(
            tenant_key=self._tenant_key,
            session_key=self._session_key,
            cron_type="interval",
            task=task,
            run_at=first_run,
            interval_s=interval_s,
        )
        logger.info("cron_add_interval: tenant={} interval_s={} job_id={}", self._tenant_key, interval_s, job_id)
        return ToolResult(
            content=json.dumps(
                {
                    "ok": True,
                    "action": "scheduled",
                    "interval_s": interval_s,
                    "first_run": first_run.strftime("%Y-%m-%dT%H:%M:%S"),
                    "message_focus": (
                        f"周期提醒已设好，每隔 {interval_s} 秒触发一次，"
                        f"第一次会在 {first_run.strftime('%Y-%m-%dT%H:%M:%S')}。"
                        "请用自然的话告诉用户频率和首次触发时间。"
                    ),
                },
                ensure_ascii=False,
            )
        )


class CronAddCronTool(Tool):
    """Schedule a recurring task using a standard cron expression."""

    name = "cron_add_cron"
    description = (
        "Schedule a recurring task using a standard cron expression (5-field format). "
        "Examples: '0 8 * * *' = every day at 8am, '0 21 * * *' = every day at 9pm."
    )
    parameters = {
        "type": "object",
        "properties": {
            "task":      {"type": "string", "description": "Instruction for the agent to execute each time"},
            "cron_expr": {"type": "string", "description": "5-field cron expression, e.g. '0 8 * * *'"},
        },
        "required": ["task", "cron_expr"],
    }
    needs_followup = True
    tool_category = "sync_write"

    def __init__(self, cron_repo: CronRepository) -> None:
        self._repo = cron_repo
        self._tenant_key = "__default__"
        self._session_key = ""

    def set_context(self, *, tenant_key: str = "", session_key: str = "", **_) -> None:
        if tenant_key:
            self._tenant_key = tenant_key
        if session_key:
            self._session_key = session_key

    async def execute(self, task: str = "", cron_expr: str = "") -> ToolResult:
        first_run = next_cron_run(cron_expr)
        if first_run is None:
            return ToolResult(content=json.dumps({"ok": False, "error": f"无效的 cron 表达式：{cron_expr}"}), ok=False)
        job_id = await self._repo.add(
            tenant_key=self._tenant_key,
            session_key=self._session_key,
            cron_type="cron",
            task=task,
            run_at=first_run,
            cron_expr=cron_expr,
        )
        logger.info("cron_add_cron: tenant={} expr={} job_id={}", self._tenant_key, cron_expr, job_id)
        return ToolResult(
            content=json.dumps(
                {
                    "ok": True,
                    "action": "scheduled",
                    "cron_expr": cron_expr,
                    "next_run": first_run.strftime("%Y-%m-%dT%H:%M:%S"),
                    "message_focus": (
                        f"定时计划已设好，下一次会在 {first_run.strftime('%Y-%m-%dT%H:%M:%S')} 触发。"
                        "请告诉用户下一次触发时间，不要暴露 cron 表达式或 job_id。"
                    ),
                },
                ensure_ascii=False,
            )
        )


class CronListTool(Tool):
    """List all active scheduled tasks for the current user."""

    name = "cron_list"
    description = "List all active scheduled tasks for the current user."
    parameters = {"type": "object", "properties": {}, "required": []}
    needs_followup = True  # Agent 需要读出列表内容告诉用户
    tool_category = "sync_read"

    def __init__(self, cron_repo: CronRepository) -> None:
        self._repo = cron_repo
        self._tenant_key = "__default__"

    def set_context(self, *, tenant_key: str = "", **_) -> None:
        if tenant_key:
            self._tenant_key = tenant_key

    async def execute(self, **_) -> ToolResult:
        jobs = await self._repo.list_by_tenant(self._tenant_key)
        return ToolResult(content=json.dumps({"ok": True, "jobs": jobs}, ensure_ascii=False))


class CronRemoveTool(Tool):
    """Cancel a scheduled task by its job ID."""

    name = "cron_remove"
    description = "Cancel a scheduled task by its job ID. Get the ID from cron_list first."
    parameters = {
        "type": "object",
        "properties": {
            "job_id": {"type": "string", "description": "The job ID to cancel"},
        },
        "required": ["job_id"],
    }
    needs_followup = True
    tool_category = "sync_write"

    def __init__(self, cron_repo: CronRepository) -> None:
        self._repo = cron_repo
        self._tenant_key = "__default__"

    def set_context(self, *, tenant_key: str = "", **_) -> None:
        if tenant_key:
            self._tenant_key = tenant_key

    async def execute(self, job_id: str = "") -> ToolResult:
        ok = await self._repo.remove(job_id, self._tenant_key)
        return ToolResult(content=json.dumps({"ok": ok}, ensure_ascii=False))
