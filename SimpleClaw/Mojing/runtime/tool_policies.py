"""Reusable policies for tool-triggered runtime tasks."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from simpleclaw.tools.base import ToolResult
from Mojing.harness.readiness.base import ACTIVE_STATUSES, normalize_status
from Mojing.runtime.tool_results import tool_deduped
from Mojing.storage.runtime_task_repo import RuntimeTaskRepository


async def time_window_dedupe(
    *,
    runtime_task_repo: RuntimeTaskRepository,
    tenant_key: str,
    task_type: str,
    dedupe_window_s: int,
    estimated_total_min: int,
    in_progress_focus: Callable[[int, int], str],
) -> ToolResult | None:
    """触发型工具的 active-task dedupe 策略。

    返回 None 表示调用方继续正常派发；返回 ToolResult 表示命中 dedupe 并短路。

    这里只基于 RuntimeTask 的事实状态做去重：queued / running / wait_external
    说明同类任务已经在处理。窗口过期后不再短路，让调用方可以重新派发，避免
    monitor 异常时 active 任务永久挡住新请求。
    """
    if int(dedupe_window_s) <= 0:
        return None

    latest = await runtime_task_repo.find_latest_task_for(
        tenant_key=tenant_key,
        task_type=task_type,
    )
    if not latest:
        return None
    task_status = normalize_status(latest.get("status"))
    if task_status not in ACTIVE_STATUSES:
        return None

    created_at = latest.get("created_at")
    if isinstance(created_at, str):
        try:
            created_at = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            created_at = None
    elapsed_min = 0
    if isinstance(created_at, datetime):
        elapsed_s = max(0, (datetime.utcnow() - created_at).total_seconds())
        if elapsed_s >= int(dedupe_window_s):
            return None
        elapsed_min = max(1, int(elapsed_s // 60))
    remaining_min = max(0, int(estimated_total_min) - elapsed_min) if elapsed_min else 0

    return tool_deduped(
        reason="active_runtime_task",
        phase="in_progress",
        source="runtime_task_status",
        runtime_task_status=task_status,
        elapsed_minutes=elapsed_min,
        estimated_remaining_minutes=remaining_min,
        dedupe_window_seconds=int(dedupe_window_s),
        message_focus=in_progress_focus(elapsed_min, remaining_min),
    )
