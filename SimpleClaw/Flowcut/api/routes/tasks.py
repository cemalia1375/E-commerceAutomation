"""任务状态查询：/flowcut/tasks/{task_id}

供前端轮询长任务（如 EXPORT_PACKAGE）的状态与产物 URL。
result_url 由 task_repo 从 nb_runtime_tasks.result_details_json 解析得到，
对应 executor 调用 TaskExecutionResult.succeeded(details={"result_url": ...}) 时写入。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from Flowcut.api.deps import require_tenant
from Flowcut.services.highlight_progress import build_highlight_batch_snapshot

router = APIRouter(prefix="/flowcut/tasks", tags=["flowcut-tasks"])


@router.get("/{task_id}")
async def get_task(
    request: Request,
    task_id: str,
    tenant_key: str = Depends(require_tenant),
) -> dict:
    c = request.app.state.container
    if task_id.startswith("batch:"):
        batch_id = task_id.removeprefix("batch:")
        batch = await c.highlight_batch_repo.get_batch(batch_id)
        if batch is None or batch.get("tenant_key") != tenant_key:
            raise HTTPException(404, f"batch {batch_id} not found")
        snapshot = await build_highlight_batch_snapshot(
            c.highlight_batch_repo, batch,
        )
        return {
            "ok": True,
            "task_id": snapshot["task_id"],
            "status": snapshot["status"],
            "task_type": "highlight_batch",
            "result_url": None,
            "details": snapshot["progress"],
            "last_error": snapshot["last_error"],
            "created_at": str(batch.get("created_at")),
            "updated_at": str(batch.get("updated_at")),
        }
    task = await c.task_repo.find_by_task_id(task_id)
    if task is None or task.get("tenant_key") != tenant_key:
        raise HTTPException(404, f"task {task_id} not found")
    return {
        "ok": True,
        "task_id": task_id,
        "status": task.get("status"),
        "task_type": task.get("task_type"),
        "result_url": task.get("result_url"),
        "details": task.get("result_details") or {},
        "last_error": task.get("last_error"),
        "created_at": str(task.get("created_at")),
        "updated_at": str(task.get("updated_at")),
    }
