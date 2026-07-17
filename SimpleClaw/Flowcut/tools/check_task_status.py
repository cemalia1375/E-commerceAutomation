"""查后台任务进度。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from simpleclaw.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from Flowcut.storage.highlight_batch_repo import HighlightBatchRepository
    from Flowcut.storage.task_repo import RuntimeTaskRepository

from Flowcut.services.highlight_progress import build_highlight_batch_snapshot


class CheckTaskStatusTool(Tool):
    """查询后台任务的当前执行状态。"""

    name = "check_task_status"
    description = (
        "查询指定后台任务的执行进度和状态。"
        "适用于 decompose_video、compose_video、publish_to_qianchuan 等 durable 工具"
        "触发的异步任务。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "要查询的后台任务 ID",
            }
        },
        "required": ["task_id"],
    }
    execution_mode = "inline"
    needs_followup = True

    def __init__(
        self,
        *,
        task_repo: "RuntimeTaskRepository",
        highlight_batch_repo: "HighlightBatchRepository | None" = None,
    ) -> None:
        self._task_repo = task_repo
        self._highlight_batch_repo = highlight_batch_repo

    async def execute(self, task_id: str, **kwargs) -> ToolResult:
        """从 task_repo 查询任务状态并返回。"""
        task = await self._task_repo.find_by_task_id(task_id)
        batch_id = task_id.removeprefix("batch:")
        batch = None
        if task is None and self._highlight_batch_repo is not None:
            batch = await self._highlight_batch_repo.get_batch(batch_id)
        if batch is not None:
            snapshot = await build_highlight_batch_snapshot(
                self._highlight_batch_repo,
                batch,
            )
            progress = snapshot["progress"]
            message = (
                f"跨集高光《{progress['drama']}》当前进度 "
                f"{progress['progress_pct']}%，{progress['stage_label']}。"
            )
            if snapshot["last_error"]:
                message += f"\n最近错误：{snapshot['last_error']}"
            return ToolResult(
                content=json.dumps(
                    {
                        "ok": True,
                        "task_id": snapshot["task_id"],
                        "batch_id": snapshot["batch_id"],
                        "status": snapshot["status"],
                        "task_type": "highlight_batch",
                        "message": message,
                        "data": {
                            "business_status": snapshot["business_status"],
                            "details": progress,
                            "diagnostics": snapshot.get("diagnostics") or {},
                            "last_error": snapshot["last_error"],
                        },
                        "ui_hint": {"render_as": "none"},
                    },
                    ensure_ascii=False,
                ),
                ok=True,
            )
        if task is None:
            return ToolResult(
                content=json.dumps(
                    {
                        "ok": False,
                        "task_id": task_id,
                        "status": "not_found",
                        "message": f"任务 {task_id} 不存在",
                        "ui_hint": {"render_as": "none"},
                    },
                    ensure_ascii=False,
                ),
                ok=False,
            )

        status = task.get("status", "unknown")
        task_type = task.get("task_type", "")
        created_at = task.get("created_at", "")
        updated_at = task.get("updated_at", "")
        last_error = task.get("last_error")
        details = task.get("result_details") or {}

        if status == "succeeded":
            content = f"任务 {task_id} 已完成。\n类型：{task_type}\n完成时间：{updated_at}（北京 UTC+8）"
        elif status == "failed":
            content = f"任务 {task_id} 失败。\n类型：{task_type}\n错误：{last_error or '未知'}"
        elif status == "running":
            content = f"任务 {task_id} 正在执行中…\n类型：{task_type}\n开始时间：{updated_at}（北京 UTC+8）"
        elif status == "queued":
            content = f"任务 {task_id} 排队等待中。\n类型：{task_type}\n提交时间：{created_at}（北京 UTC+8）"
        elif status == "triggered":
            content = f"任务 {task_id} 已触发，等待业务完成。\n类型：{task_type}"
        else:
            content = f"任务 {task_id} 状态：{status}\n类型：{task_type}"

        return ToolResult(
            content=json.dumps(
                {
                    "ok": True,
                    "task_id": task_id,
                    "status": status,
                    "task_type": task_type,
                    "message": content,
                    "data": {
                        "created_at": created_at,
                        "updated_at": updated_at,
                        "last_error": last_error,
                        "details": details,
                    },
                    "ui_hint": {"render_as": "none"},
                },
                ensure_ascii=False,
            ),
            ok=True,
        )
