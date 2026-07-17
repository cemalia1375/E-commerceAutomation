"""高光批量管道 API：查询、取消、重试。"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from Flowcut.api.deps import require_tenant
from Flowcut.runtime.streams import FlowcutTaskStream
from simpleclaw.runtime.task_protocol import TaskEnvelope

router = APIRouter(prefix="/highlight-batches", tags=["highlight-batches"])


class CreateBatchRequest(BaseModel):
    drama_name: str
    num_candidates: int = 3
    connector_asset_id: int | None = None


@router.post("")
async def create_batch(
    body: CreateBatchRequest,
    request: Request,
    tenant_key: str = Depends(require_tenant),
) -> dict[str, Any]:
    """Create a new highlight batch for a drama. Returns batch_id."""
    container = request.app.state.container
    repo = container.highlight_batch_repo
    runtime = container.runtime

    batch_id = uuid.uuid4().hex

    await repo.create_batch(
        tenant_key=tenant_key,
        drama_name=body.drama_name,
        num_candidates=body.num_candidates,
        batch_id=batch_id,
    )

    # Submit orchestrator task
    await runtime.submit_task(
        TaskEnvelope(
            task_type="highlight_batch",
            payload={
                "batch_id": batch_id,
                "tenant_key": tenant_key,
                "session_key": "api",
                "connector_asset_id": body.connector_asset_id,
            },
            stream=FlowcutTaskStream.HIGHLIGHT_BATCH,
            tenant_key=tenant_key,
            scope_key=batch_id,
        ),
        tool_name="highlight_batch",
        summary=f"start batch {batch_id} for {body.drama_name}",
    )

    return {"ok": True, "batch_id": batch_id, "drama_name": body.drama_name}


@router.get("/{batch_id}")
async def get_batch(
    batch_id: str,
    request: Request,
    tenant_key: str = Depends(require_tenant),
) -> dict[str, Any]:
    """Get batch status and progress."""
    container = request.app.state.container
    repo = container.highlight_batch_repo

    batch = await repo.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"batch {batch_id} not found")

    progress = await repo.get_stage_progress(batch_id)

    return {
        "batch_id": batch["batch_id"],
        "drama_name": batch["drama_name"],
        "status": batch["status"],
        "num_candidates": batch["num_candidates"],
        "progress": progress,
        "summary": batch.get("summary_json"),
        "created_at": str(batch.get("created_at", "")),
        "updated_at": str(batch.get("updated_at", "")),
    }


@router.post("/{batch_id}/cancel")
async def cancel_batch(
    batch_id: str,
    request: Request,
    tenant_key: str = Depends(require_tenant),
) -> dict[str, Any]:
    """Cancel a running batch. PENDING sub-tasks will be skipped."""
    container = request.app.state.container
    repo = container.highlight_batch_repo

    batch = await repo.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"batch {batch_id} not found")

    await repo.update_status(batch_id, "CANCELLED")
    cancelled = await repo.cancel_pending_stages(batch_id)

    return {"ok": True, "batch_id": batch_id, "cancelled_stages": cancelled}


@router.post("/{batch_id}/retry")
async def retry_batch(
    batch_id: str,
    request: Request,
    tenant_key: str = Depends(require_tenant),
) -> dict[str, Any]:
    """Retry a failed batch. Only FAILED stages are retried."""
    container = request.app.state.container
    repo = container.highlight_batch_repo
    runtime = container.runtime

    batch = await repo.get_batch(batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"batch {batch_id} not found")

    status = str(batch.get("status", ""))
    if status not in ("FAILED", "PARTIAL"):
        raise HTTPException(
            status_code=400,
            detail=f"batch {batch_id} is {status}, can only retry FAILED or PARTIAL",
        )

    reset_stages = await repo.reset_stages_for_retry(batch_id, stages=("span_plan",))

    # Reset to first phase and re-submit orchestrator. Earlier terminal stages
    # remain reusable; retryable span_plan stages are reset above.
    await repo.update_status(batch_id, "EPISODE_PREP")

    await runtime.submit_task(
        TaskEnvelope(
            task_type="highlight_batch",
            payload={
                "batch_id": batch_id,
                "tenant_key": tenant_key,
                "session_key": "api",
            },
            stream=FlowcutTaskStream.HIGHLIGHT_BATCH,
            tenant_key=tenant_key,
            scope_key=batch_id,
        ),
        tool_name="highlight_batch",
        summary=f"retry batch {batch_id}",
    )

    return {
        "ok": True,
        "batch_id": batch_id,
        "status": "EPISODE_PREP",
        "reset_stages": reset_stages,
    }


@router.get("")
async def list_batches(
    request: Request,
    tenant_key: str = Depends(require_tenant),
    drama_name: str | None = None,
) -> dict[str, Any]:
    """List highlight batches, optionally filtered by drama."""
    container = request.app.state.container
    repo = container.highlight_batch_repo

    if drama_name:
        batches = await repo.list_by_drama(tenant_key, drama_name)
    else:
        batches = await repo.list_active(tenant_key)

    return {
        "batches": [
            {
                "batch_id": b["batch_id"],
                "drama_name": b["drama_name"],
                "status": b["status"],
                "num_candidates": b["num_candidates"],
                "created_at": str(b.get("created_at", "")),
            }
            for b in batches
        ]
    }
