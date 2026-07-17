"""highlight_batch orchestrator: state machine for cross-episode highlight pipeline.

This is a lightweight orchestrator that manages the lifecycle of one drama's
highlight batch. It DOES NOT do heavy video/LLM work — it only submits sub-tasks
to other streams and polls their completion.

Design:
  - Re-entrant: can be called multiple times. Each invocation checks DB state,
    advances one step, and returns.
  - On completion of sub-tasks, the orchestrator resubmits ITSELF to the queue
    to continue the pipeline.
  - scope_key = batch_id prevents concurrent orchestration of the same batch.

State machine:
  EPISODE_PREP → MERGE_DECOMPOSE → START_SELECT → SPAN_PLANNING → COMPOSING → READY/PARTIAL
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Awaitable, Callable

from simpleclaw.runtime.task_protocol import TaskEnvelope, TaskExecutionResult
from simpleclaw.runtime.task_protocol import BACKGROUND_STREAM

from Flowcut.runtime.streams import FlowcutTaskStream

logger = logging.getLogger(__name__)

# Polling intervals (seconds) between re-checking sub-task completion
_POLL_INTERVAL_S = 3.0
# Maximum wall-clock before giving up on a phase
_PHASE_TIMEOUT_S = 1800.0  # 30 min

# Valid status transitions
_VALID_STATUSES = {
    "EPISODE_PREP",
    "MERGE_DECOMPOSE",
    "START_SELECT",
    "SPAN_PLANNING",
    "COMPOSING",
    "READY",
    "PARTIAL",
    "FAILED",
    "CANCELLED",
}


def _episode_no_from_asset(asset: dict) -> int:
    """Return stored episode_no, falling back to the first number in the file name."""
    try:
        episode_no = int(asset.get("episode_no") or 0)
    except (TypeError, ValueError):
        episode_no = 0
    if episode_no > 0:
        return episode_no

    match = re.search(r"\d+", str(asset.get("name") or ""))
    return int(match.group()) if match else 0


def make_highlight_batch_executor(
    *,
    runtime,
    highlight_batch_repo,
    highlight_asset_repo,
    creative_repo=None,
    connector_asset_id: int | None = None,
) -> Callable[[TaskEnvelope], Awaitable[TaskExecutionResult]]:
    """Create the batch orchestrator executor.

    Task payload:
        {
            "batch_id": str,        # fc_highlight_batch.batch_id
            "tenant_key": str,
            "session_key": str,
        }
    """

    async def execute(task: TaskEnvelope) -> TaskExecutionResult:
        payload = task.payload
        batch_id = str(payload["batch_id"])
        tenant_key = str(payload.get("tenant_key", "flowcut"))
        session_key = str(payload.get("session_key", "highlight_plan"))

        batch = await highlight_batch_repo.get_batch(batch_id)
        if batch is None:
            return TaskExecutionResult.failed(error=f"batch {batch_id} not found")

        status = str(batch.get("status", "EPISODE_PREP"))
        drama_name = str(batch.get("drama_name", ""))
        num_candidates = int(batch.get("num_candidates", 3))
        state = batch.get("orchestrator_state_json") or {}
        if isinstance(state, str):
            state = json.loads(state)
        start_episode = max(1, int(state.get("start_episode") or 1))
        end_episode_raw = state.get("end_episode")
        end_episode = int(end_episode_raw) if end_episode_raw is not None else None
        connector_raw = state.get("connector_asset_id")
        requested_connector_id = (
            int(connector_raw) if connector_raw is not None else connector_asset_id
        )
        session_key = str(
            payload.get("session_key")
            or state.get("session_key")
            or "highlight_plan"
        )

        if status == "CANCELLED":
            return TaskExecutionResult.noop(summary=f"batch {batch_id} was cancelled")

        if status in ("READY", "PARTIAL", "FAILED"):
            return TaskExecutionResult.noop(summary=f"batch {batch_id} already terminal: {status}")

        logger.info(
            "highlight_batch: orchestrating batch=%s drama=%s status=%s",
            batch_id, drama_name, status,
        )

        try:
            if status == "EPISODE_PREP":
                return await _handle_episode_prep(
                    batch_id, drama_name, tenant_key, session_key,
                    start_episode, end_episode,
                    runtime, highlight_batch_repo, highlight_asset_repo,
                )
            elif status == "MERGE_DECOMPOSE":
                return await _handle_merge_decompose(
                    batch_id, tenant_key,
                    runtime, highlight_batch_repo,
                )
            elif status == "START_SELECT":
                return await _handle_start_select(
                    batch_id, num_candidates,
                    runtime, highlight_batch_repo,
                )
            elif status == "SPAN_PLANNING":
                return await _handle_span_planning(
                    batch_id, tenant_key, session_key,
                    num_candidates, requested_connector_id,
                    runtime, highlight_batch_repo,
                )
            elif status == "COMPOSING":
                return await _handle_composing(
                    batch_id, tenant_key, session_key,
                    runtime, highlight_batch_repo,
                    creative_repo,
                )
            else:
                return TaskExecutionResult.failed(
                    error=f"unknown batch status: {status}"
                )
        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}"
            logger.error("highlight_batch orchestrator error batch=%s: %s", batch_id, error_text)
            await highlight_batch_repo.update_status(batch_id, "FAILED")
            return TaskExecutionResult.failed(error=error_text)


# ── Phase handlers ──────────────────────────────────────────────────────

    return execute


async def _handle_episode_prep(
    batch_id: str,
    drama_name: str,
    tenant_key: str,
    session_key: str,
    start_episode: int,
    end_episode: int | None,
    runtime,
    highlight_batch_repo,
    highlight_asset_repo,
) -> TaskExecutionResult:
    """Phase 1: Submit episode_prepare tasks for first 3 episodes."""

    # Check if stages already exist (re-entrant safety)
    existing = await highlight_batch_repo.list_stages(
        batch_id, stage="episode_prepare",
    )
    if not existing:
        # Query episodes and create stages
        rows = await highlight_asset_repo.list_by_tenant(
            tenant_key, asset_type="episode_source",
            drama_name=drama_name, limit=500,
        )
        from Flowcut.services.clip_planner import match_drama_episodes
        episodes = sorted(rows, key=_episode_no_from_asset)
        if not episodes:
            all_rows = await highlight_asset_repo.list_by_tenant(
                tenant_key, asset_type="episode_source", limit=500,
            )
            episodes = match_drama_episodes(all_rows, drama_name)
        episodes = [
            episode
            for episode in episodes
            if _episode_no_from_asset(episode) >= start_episode
            and (
                end_episode is None
                or _episode_no_from_asset(episode) <= end_episode
            )
        ]
        episodes = sorted(episodes, key=_episode_no_from_asset)
        if not episodes:
            await highlight_batch_repo.set_summary(
                batch_id,
                {
                    "error": (
                        f"no episodes found for drama '{drama_name}' "
                        f"in range {start_episode}-{end_episode or 'latest'}"
                    ),
                    "hint": "Check episode_source assets and episode_no parsing.",
                },
            )
            await highlight_batch_repo.update_status(batch_id, "FAILED")
            return TaskExecutionResult.failed(
                error=(
                    f"no episodes found for drama '{drama_name}' "
                    f"in range {start_episode}-{end_episode or 'latest'}"
                )
            )

        head = episodes[:3]
        for asset in head:
            episode_no = _episode_no_from_asset(asset)
            s = await highlight_batch_repo.create_stage(
                batch_id=batch_id,
                stage="episode_prepare",
                episode_no=episode_no,
                input_json={
                    "asset_id": int(asset["id"]),
                    "episode_no": episode_no,
                    "oss_key": str(asset.get("oss_key") or asset.get("oss_url") or ""),
                },
            )
            # Submit sub-task
            await runtime.submit_task(
                TaskEnvelope(
                    task_type="episode_prepare",
                    payload={
                        "batch_id": batch_id,
                        "stage_id": int(s["id"]),
                        "asset_id": int(asset["id"]),
                        "episode_no": episode_no,
                        "oss_key": str(asset.get("oss_key") or asset.get("oss_url") or ""),
                        "tenant_key": tenant_key,
                        "session_key": session_key,
                    },
                    stream=FlowcutTaskStream.HIGHLIGHT_EPISODE_PREPARE,
                    tenant_key=tenant_key,
                    session_key=session_key,
                ),
                tool_name="episode_prepare",
                summary=f"prepare ep {episode_no} for {drama_name}",
            )
            logger.info(
                "highlight_batch: submitted episode_prepare ep=%d batch=%s",
                episode_no, batch_id,
            )

    # Check completion
    ep_stages = await highlight_batch_repo.list_stages(
        batch_id, stage="episode_prepare",
    )
    ready_count = sum(1 for s in ep_stages if s.get("status") == "READY")
    failed_count = sum(1 for s in ep_stages if s.get("status") == "FAILED")
    total = len(ep_stages)

    if ready_count + failed_count >= total:
        if failed_count > 0:
            logger.warning(
                "highlight_batch: %d/%d episode_prepare failed for batch=%s",
                failed_count, total, batch_id,
            )
        # Advance
        await highlight_batch_repo.update_status(batch_id, "MERGE_DECOMPOSE")
        return await _resubmit_self(batch_id, tenant_key, session_key, runtime)

    # Not yet done — wait and retry
    return TaskExecutionResult.wait_external(
        summary=f"episode_prepare: {ready_count}/{total} ready, waiting",
        details={"ready": ready_count, "total": total},
    )


async def _handle_merge_decompose(
    batch_id: str,
    tenant_key: str,
    runtime,
    highlight_batch_repo,
) -> TaskExecutionResult:
    """Phase 2: Submit merge_decompose task."""

    existing = await highlight_batch_repo.list_stages(
        batch_id, stage="merge_decompose",
    )
    if not existing:
        s = await highlight_batch_repo.create_stage(
            batch_id=batch_id,
            stage="merge_decompose",
            input_json={"batch_id": batch_id},
        )
        batch = await highlight_batch_repo.get_batch(batch_id)
        await runtime.submit_task(
            TaskEnvelope(
                task_type="merge_decompose",
                payload={
                    "batch_id": batch_id,
                    "stage_id": int(s["id"]),
                    "tenant_key": tenant_key,
                    "drama_name": str(batch.get("drama_name", "")),
                },
                stream=FlowcutTaskStream.HIGHLIGHT_MERGE_DECOMPOSE,
                tenant_key=tenant_key,
            ),
            tool_name="merge_decompose",
            summary=f"merge+decompose for batch {batch_id}",
        )
        logger.info("highlight_batch: submitted merge_decompose batch=%s", batch_id)
        return TaskExecutionResult.wait_external(
            summary="merge_decompose submitted, waiting",
        )

    # Check completion
    stage = existing[0]
    if stage.get("status") == "READY":
        await highlight_batch_repo.update_status(batch_id, "START_SELECT")
        batch = await highlight_batch_repo.get_batch(batch_id)
        return await _resubmit_self(
            batch_id,
            str(batch.get("tenant_key", tenant_key)),
            "highlight_plan",
            runtime,
        )
    elif stage.get("status") == "FAILED":
        error = f"merge_decompose failed: {stage.get('error', '')}"
        await highlight_batch_repo.set_summary(batch_id, {"error": error})
        await highlight_batch_repo.update_status(batch_id, "FAILED")
        return TaskExecutionResult.failed(error=error)

    return TaskExecutionResult.wait_external(summary="merge_decompose in progress")


async def _handle_start_select(
    batch_id: str,
    num_candidates: int,
    runtime,
    highlight_batch_repo,
) -> TaskExecutionResult:
    """Phase 3: Submit start_select task."""

    existing = await highlight_batch_repo.list_stages(
        batch_id, stage="start_select",
    )
    if not existing:
        s = await highlight_batch_repo.create_stage(
            batch_id=batch_id,
            stage="start_select",
            input_json={"num_candidates": num_candidates},
        )
        batch = await highlight_batch_repo.get_batch(batch_id)
        await runtime.submit_task(
            TaskEnvelope(
                task_type="start_select",
                payload={
                    "batch_id": batch_id,
                    "stage_id": int(s["id"]),
                    "num_candidates": num_candidates,
                    "tenant_key": str(batch.get("tenant_key", "flowcut")),
                    "session_key": "highlight_plan",
                },
                stream=FlowcutTaskStream.HIGHLIGHT_START_SELECT,
                tenant_key=str(batch.get("tenant_key", "flowcut")),
            ),
            tool_name="start_select",
            summary=f"select starts for batch {batch_id}",
        )
        logger.info("highlight_batch: submitted start_select batch=%s", batch_id)
        return TaskExecutionResult.wait_external(summary="start_select submitted")

    stage = existing[0]
    if stage.get("status") == "READY":
        await highlight_batch_repo.update_status(batch_id, "SPAN_PLANNING")
        batch = await highlight_batch_repo.get_batch(batch_id)
        return await _resubmit_self(
            batch_id,
            str(batch.get("tenant_key", "flowcut")),
            "highlight_plan",
            runtime,
        )
    elif stage.get("status") == "FAILED":
        error = f"start_select failed: {stage.get('error', '')}"
        await highlight_batch_repo.set_summary(batch_id, {"error": error})
        await highlight_batch_repo.update_status(batch_id, "FAILED")
        return TaskExecutionResult.failed(error=error)

    return TaskExecutionResult.wait_external(summary="start_select in progress")


async def _handle_span_planning(
    batch_id: str,
    tenant_key: str,
    session_key: str,
    num_candidates: int,
    connector_asset_id: int | None,
    runtime,
    highlight_batch_repo,
) -> TaskExecutionResult:
    """Phase 4: Submit span_plan tasks for each candidate (already created as PENDING stages)."""

    stages = await highlight_batch_repo.list_stages(
        batch_id, stage="span_plan",
    )
    pending = [s for s in stages if s.get("status") == "PENDING"]
    processing = [s for s in stages if s.get("status") == "PROCESSING"]
    ready = [s for s in stages if s.get("status") == "READY"]
    failed = [s for s in stages if s.get("status") == "FAILED"]
    skipped = [s for s in stages if s.get("status") in ("SKIPPED", "CANCELLED")]

    # Submit pending stages
    for s in pending:
        input_json = s.get("input_json") or {}
        if isinstance(input_json, str):
            input_json = json.loads(input_json)
        envelope = TaskEnvelope(
            task_type="span_plan",
            payload={
                "batch_id": batch_id,
                "stage_id": int(s["id"]),
                "candidate_idx": int(s.get("candidate_idx") or 0),
                "tenant_key": tenant_key,
                "session_key": session_key,
                "connector_asset_id": connector_asset_id,
            },
            stream=FlowcutTaskStream.HIGHLIGHT_SPAN_PLAN,
            tenant_key=tenant_key,
            session_key=session_key,
        )
        await highlight_batch_repo.mark_stage_running(
            int(s["id"]),
            runtime_task_id=envelope.task_id,
        )
        await runtime.submit_task(
            envelope,
            tool_name="span_plan",
            summary=f"span plan candidate {s.get('candidate_idx')} for batch {batch_id}",
        )
        logger.info(
            "highlight_batch: submitted span_plan candidate=%d batch=%s",
            s.get("candidate_idx"), batch_id,
        )

    terminal = len(ready) + len(failed) + len(skipped)
    total = len(stages)

    if terminal >= total:
        # All candidates processed
        if len(failed) > 0:
            await highlight_batch_repo.update_status(batch_id, "PARTIAL")
        else:
            await highlight_batch_repo.update_status(batch_id, "COMPOSING")
        batch = await highlight_batch_repo.get_batch(batch_id)
        return await _resubmit_self(
            batch_id, tenant_key, session_key, runtime,
        )

    in_flight = len(pending) + len(processing)
    return TaskExecutionResult.wait_external(
        summary=f"span_plan: {len(ready)}/{total} ready, {in_flight} queued",
        details={"ready": len(ready), "failed": len(failed), "total": total},
    )


async def _handle_composing(
    batch_id: str,
    tenant_key: str,
    session_key: str,
    runtime,
    highlight_batch_repo,
    creative_repo,
) -> TaskExecutionResult:
    """Phase 5: Wait for compose tasks to complete, then finalize."""

    # Check if all span_plan stages have associated compose results
    stages = await highlight_batch_repo.list_stages(
        batch_id, stage="span_plan",
    )
    creative_ids = [
        s.get("creative_id")
        for s in stages
        if s.get("creative_id") and s.get("status") == "READY"
    ]

    summary = {
        "total_candidates": len(stages),
        "total_created": len(creative_ids),
        "creative_ids": creative_ids,
    }
    await highlight_batch_repo.set_summary(batch_id, summary)
    if not creative_ids:
        summary["error"] = "all candidates skipped or failed; no creative produced"
        await highlight_batch_repo.set_summary(batch_id, summary)
        await highlight_batch_repo.update_status(batch_id, "FAILED")
        logger.warning("highlight_batch: FAILED batch=%s no creatives produced", batch_id)
        return TaskExecutionResult.failed(
            error=summary["error"],
            details=summary,
        )

    if creative_repo is None:
        await highlight_batch_repo.update_status(batch_id, "READY")
        logger.info(
            "highlight_batch: READY batch=%s creatives=%d",
            batch_id, len(creative_ids),
        )
        return TaskExecutionResult.succeeded(
            summary=f"highlight_batch READY: {len(creative_ids)} creatives",
            details=summary,
        )

    creative_rows = []
    for creative_id in creative_ids:
        row = await creative_repo.get(int(creative_id))
        if row is not None:
            creative_rows.append(row)

    ready_ids = [
        int(row["id"])
        for row in creative_rows
        if row.get("status") == "READY" and row.get("oss_url")
    ]
    failed_ids = [
        int(row["id"])
        for row in creative_rows
        if row.get("status") == "FAILED"
    ]
    terminal_count = len(ready_ids) + len(failed_ids)
    summary.update({
        "compose_total": len(creative_ids),
        "compose_ready": len(ready_ids),
        "compose_failed": len(failed_ids),
        "compose_pending": max(0, len(creative_ids) - terminal_count),
        "ready_creative_ids": ready_ids,
        "failed_creative_ids": failed_ids,
    })
    await highlight_batch_repo.set_summary(batch_id, summary)

    if terminal_count < len(creative_ids):
        await asyncio.sleep(_POLL_INTERVAL_S)
        return await _resubmit_self(batch_id, tenant_key, session_key, runtime)

    if ready_ids and failed_ids:
        await highlight_batch_repo.update_status(batch_id, "PARTIAL")
        logger.warning(
            "highlight_batch: PARTIAL batch=%s ready=%d failed=%d",
            batch_id, len(ready_ids), len(failed_ids),
        )
        return TaskExecutionResult.succeeded(
            summary=f"highlight_batch PARTIAL: {len(ready_ids)} ready, {len(failed_ids)} failed",
            details=summary,
        )

    if failed_ids and not ready_ids:
        summary["error"] = "all compose tasks failed; no ready creative produced"
        await highlight_batch_repo.set_summary(batch_id, summary)
        await highlight_batch_repo.update_status(batch_id, "FAILED")
        logger.warning("highlight_batch: FAILED batch=%s all compose failed", batch_id)
        return TaskExecutionResult.failed(error=summary["error"], details=summary)

    await highlight_batch_repo.update_status(batch_id, "READY")

    logger.info(
        "highlight_batch: READY batch=%s creatives=%d",
        batch_id, len(ready_ids),
    )
    return TaskExecutionResult.succeeded(
        summary=f"highlight_batch READY: {len(ready_ids)} creatives",
        details=summary,
    )


# ── Helpers ─────────────────────────────────────────────────────────────

async def _resubmit_self(
    batch_id: str,
    tenant_key: str,
    session_key: str,
    runtime,
) -> TaskExecutionResult:
    """Re-submit the batch task to itself so it continues processing."""
    await runtime.submit_task(
        TaskEnvelope(
            task_type="highlight_batch",
            payload={
                "batch_id": batch_id,
                "tenant_key": tenant_key,
                "session_key": session_key,
            },
            stream=FlowcutTaskStream.HIGHLIGHT_BATCH,
            tenant_key=tenant_key,
            session_key=session_key,
            scope_key=batch_id,
        ),
        tool_name="highlight_batch",
        summary=f"continue batch {batch_id}",
    )
    return TaskExecutionResult.wait_external(
        summary=f"batch {batch_id} advancing to next phase",
        details={"batch_id": batch_id},
    )
