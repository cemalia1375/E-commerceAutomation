"""Progress snapshots for the cross-episode highlight batch pipeline."""
from __future__ import annotations

import json
from typing import Any


_BASE_PROGRESS = {
    "EPISODE_PREP": (5, "正在准备原片"),
    "MERGE_DECOMPOSE": (30, "正在并行拆镜"),
    "START_SELECT": (55, "正在选择高光起点"),
    "SPAN_PLANNING": (70, "正在规划候选片段"),
    "COMPOSING": (95, "正在生成候选成片"),
    "READY": (100, "生成完成"),
    "PARTIAL": (100, "部分候选已生成"),
    "FAILED": (100, "生成失败"),
    "CANCELLED": (100, "已取消"),
}


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


async def build_highlight_batch_snapshot(
    highlight_batch_repo,
    batch: dict[str, Any],
) -> dict[str, Any]:
    batch_id = str(batch["batch_id"])
    business_status = str(batch.get("status") or "EPISODE_PREP")
    stage_progress = await highlight_batch_repo.get_stage_progress(batch_id)
    base_pct, stage_label = _BASE_PROGRESS.get(
        business_status, (0, business_status),
    )
    progress_pct = base_pct
    state = _json_object(batch.get("orchestrator_state_json"))
    summary = _json_object(batch.get("summary_json"))

    if business_status == "EPISODE_PREP":
        item = stage_progress.get("episode_prepare") or {}
        total = int(item.get("total") or 0)
        terminal = int(item.get("done") or 0) + int(item.get("failed") or 0)
        if total:
            progress_pct = 5 + round(20 * terminal / total)
    elif business_status == "MERGE_DECOMPOSE":
        merge_progress = _json_object(state.get("merge_decompose_progress"))
        total = int(merge_progress.get("total") or 0)
        completed = int(merge_progress.get("completed") or 0)
        if total:
            progress_pct = 30 + round(20 * min(completed, total) / total)
        item = stage_progress.get("merge_decompose") or {}
        if int(item.get("done") or 0) > 0:
            progress_pct = 50
    elif business_status == "START_SELECT":
        item = stage_progress.get("start_select") or {}
        if int(item.get("done") or 0) > 0:
            progress_pct = 65
    elif business_status == "SPAN_PLANNING":
        item = stage_progress.get("span_plan") or {}
        total = int(item.get("total") or 0)
        terminal = int(item.get("done") or 0) + int(item.get("failed") or 0)
        if total:
            progress_pct = 70 + round(20 * terminal / total)
    elif business_status == "COMPOSING":
        compose_total = int(summary.get("compose_total") or summary.get("total_created") or 0)
        compose_done = int(summary.get("compose_ready") or 0) + int(
            summary.get("compose_failed") or 0
        )
        if compose_total:
            progress_pct = 90 + round(9 * min(compose_done, compose_total) / compose_total)

    stages = await highlight_batch_repo.list_stages(batch_id)
    failed_stages = [
        {
            "stage": str(stage.get("stage") or ""),
            "episode_no": stage.get("episode_no"),
            "candidate_idx": stage.get("candidate_idx"),
            "error": str(stage.get("error") or "")[:500],
        }
        for stage in stages
        if stage.get("status") == "FAILED" and stage.get("error")
    ]
    last_error = next(
        (
            str(stage.get("error"))
            for stage in reversed(stages)
            if stage.get("error")
        ),
        None,
    )
    if not last_error and summary.get("error"):
        last_error = str(summary["error"])

    runtime_status = "running"
    if business_status in ("READY", "PARTIAL"):
        runtime_status = "succeeded"
    elif business_status in ("FAILED", "CANCELLED"):
        runtime_status = "failed"

    progress = {
        "stage": business_status.lower(),
        "stage_label": stage_label,
        "progress_pct": min(100, max(0, progress_pct)),
        "drama": str(batch.get("drama_name") or ""),
        "candidate_count": int(state.get("candidate_count") or 0),
        "created_count": int(summary.get("total_created") or 0),
    }
    if business_status == "COMPOSING" or summary.get("compose_total") is not None:
        progress.update({
            "compose_total": int(summary.get("compose_total") or 0),
            "compose_ready": int(summary.get("compose_ready") or 0),
            "compose_failed": int(summary.get("compose_failed") or 0),
            "compose_pending": int(summary.get("compose_pending") or 0),
        })

    return {
        "task_id": f"batch:{batch_id}",
        "batch_id": batch_id,
        "status": runtime_status,
        "business_status": business_status,
        "last_error": last_error,
        "progress": progress,
        "diagnostics": {
            "current_step": business_status,
            "stage_progress": stage_progress,
            "merge_decompose_progress": _json_object(
                state.get("merge_decompose_progress")
            ),
            "failed_stages": failed_stages[:10],
        },
    }
