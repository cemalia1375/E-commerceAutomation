"""highlight_start_select executor: Gemini picks highlight start points from merged shots.

Reads merged_shots_json from fc_highlight_batch, runs select_start_shots(),
validates picks against content_start, deduplicates, and creates
fc_highlight_stage rows for each valid candidate.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Awaitable, Callable

from simpleclaw.runtime.task_protocol import TaskEnvelope, TaskExecutionResult

from Flowcut.runtime.highlight_continuation import wake_highlight_batch
from Flowcut.services.gemini_video import select_start_shots
from Flowcut.services.clip_planner import (
    DEDUP_GAP_S,
    locate,
    score_start_candidate,
    validate_start_candidate,
)

logger = logging.getLogger(__name__)

_START_SELECT_RETRY_BACKOFF_S = tuple(
    float(v.strip())
    for v in os.getenv("FLOWCUT_START_SELECT_RETRY_BACKOFF_S", "5,15,45").split(",")
    if v.strip()
)
_START_SELECT_RETRYABLE_MARKERS = (
    "500",
    "502",
    "503",
    "504",
    "do_request_failed",
    "moyu_api_error",
    "服务暂时不可用",
    "请稍后重试",
    "UNAVAILABLE",
    "RESOURCE_EXHAUSTED",
    "429",
    "rate limit",
    "quota",
    "Timeout",
    "ReadTimeout",
    "ConnectTimeout",
    "ConnectionError",
    "ConnectError",
    "RemoteProtocolError",
    "Server disconnected",
    "SSL",
    "UNEXPECTED_EOF",
    "Connection reset",
    "WinError 10054",
    "WinError 10053",
)
_START_SELECT_NON_RETRYABLE_MARKERS = (
    "400 INVALID_ARGUMENT",
    "API key not valid",
    "API_KEY_INVALID",
    "PERMISSION_DENIED",
    "401",
    "403",
)


def _is_retryable_external_error(exc: BaseException) -> bool:
    if isinstance(exc, asyncio.TimeoutError):
        return True
    message = f"{type(exc).__name__}: {exc}"
    if any(marker in message for marker in _START_SELECT_NON_RETRYABLE_MARKERS):
        return False
    return any(marker in message for marker in _START_SELECT_RETRYABLE_MARKERS)


def _extract_request_id(error_text: str) -> str | None:
    match = re.search(r"request id:\s*([A-Za-z0-9_.:-]+)", error_text, re.I)
    if match:
        return match.group(1)
    match = re.search(r"request_id[\"']?\s*[:=]\s*[\"']?([A-Za-z0-9_.:-]+)", error_text, re.I)
    return match.group(1) if match else None


def _retry_delay_s(attempt: int) -> float:
    if not _START_SELECT_RETRY_BACKOFF_S:
        return 0.0
    return _START_SELECT_RETRY_BACKOFF_S[
        min(max(attempt, 0), len(_START_SELECT_RETRY_BACKOFF_S) - 1)
    ]


def _find_fallback_pick(head_shots: list[dict], content_start: float) -> dict | None:
    """Pick a local start when Gemini returns no usable starts."""
    best: dict | None = None
    best_score = -1.0
    for idx, shot in enumerate(head_shots):
        start = float(shot.get("start_time", 0.0))
        if start < content_start:
            continue
        quality = score_start_candidate(
            shot,
            head_shots,
            content_start,
            llm_hook_strength=float(shot.get("hook_strength") or 0.0),
        )
        if quality.is_rejected:
            continue
        score = quality.hook_score
        if score > best_score:
            best_score = score
            best = {
                "idx": idx,
                "hook_strength": max(float(shot.get("hook_strength") or 0.0), score),
                "hook_score": score,
                "candidate_quality": quality.to_dict(),
                "is_fallback": True,
                "reason": (
                    f"fallback: selected a valid dialogue segment after "
                    f"content_start={content_start:.1f}s"
                ),
            }
    return best


def _rank_pick(
    pick: dict,
    head_shots: list[dict],
    content_start: float,
) -> tuple[dict, dict]:
    """Attach code-side quality and return a sort-friendly pick."""
    idx = int(pick["idx"])
    shot = head_shots[idx]
    llm_hook = float(pick.get("hook_strength") or 0.0)
    quality = score_start_candidate(
        shot,
        head_shots,
        content_start,
        llm_hook_strength=llm_hook,
    )
    ranked = {
        **pick,
        "hook_score": quality.hook_score,
        "candidate_quality": quality.to_dict(),
        "is_fallback": bool(pick.get("is_fallback")),
    }
    skip = {
        "idx": idx,
        "start_time": round(float(shot.get("start_time", 0.0)), 2),
        "reasons": list(quality.reject_reasons),
        "quality": quality.to_dict(),
    }
    return ranked, skip


def make_start_select_executor(
    *,
    runtime,
    highlight_batch_repo,
) -> Callable[[TaskEnvelope], Awaitable[TaskExecutionResult]]:
    """Create an executor that runs Gemini start selection for one batch.

    Task payload:
        {
            "batch_id": str,
            "stage_id": int,
            "num_candidates": int,
        }
    """

    async def execute(task: TaskEnvelope) -> TaskExecutionResult:
        payload = task.payload
        batch_id = str(payload["batch_id"])
        stage_id = int(payload["stage_id"])
        num_candidates = int(payload.get("num_candidates", 3))
        tenant_key = str(payload.get("tenant_key", "flowcut"))
        session_key = str(payload.get("session_key", "highlight_plan"))

        batch = await highlight_batch_repo.get_batch(batch_id)
        if batch is None:
            return TaskExecutionResult.failed(error=f"batch {batch_id} not found")

        merged_shots_raw = batch.get("merged_shots_json")
        if not merged_shots_raw:
            await highlight_batch_repo.mark_stage_failed(
                stage_id, "no merged_shots_json in batch",
            )
            await wake_highlight_batch(
                runtime=runtime,
                batch_id=batch_id,
                tenant_key=tenant_key,
                session_key=session_key,
            )
            return TaskExecutionResult.failed(error="no merged_shots_json in batch")

        head_shots: list[dict] = (
            json.loads(merged_shots_raw)
            if isinstance(merged_shots_raw, str)
            else merged_shots_raw
        )

        state = batch.get("orchestrator_state_json") or {}
        if isinstance(state, str):
            state = json.loads(state)

        offsets_data: list[tuple[int, float]] = [
            (int(ep), float(off))
            for ep, off in (state.get("offsets") or [])
        ]
        durations: dict[int, float] = {
            int(k): float(v) for k, v in (state.get("durations") or {}).items()
        }
        content_start = float(state.get("content_start", 0.0))

        try:
            stage = await highlight_batch_repo.get_stage(stage_id)
            if stage and stage.get("status") in ("READY", "FAILED", "SKIPPED", "CANCELLED"):
                return TaskExecutionResult.noop(
                    summary=f"start_select stage {stage_id} already {stage.get('status')}"
                )
            if hasattr(highlight_batch_repo, "try_mark_stage_running"):
                claimed = await highlight_batch_repo.try_mark_stage_running(
                    stage_id, runtime_task_id=task.task_id,
                )
                if not claimed:
                    current = await highlight_batch_repo.get_stage(stage_id)
                    return TaskExecutionResult.wait_external(
                        summary=(
                            f"start_select stage {stage_id} is "
                            f"{(current or {}).get('status', 'not claimed')}"
                        )
                    )
            else:
                await highlight_batch_repo.mark_stage_running(
                    stage_id, runtime_task_id=task.task_id,
                )

            # Gemini picks start points
            picks = await select_start_shots(
                head_shots, top_n=num_candidates * 2,
            )
            if not picks:
                fallback = _find_fallback_pick(head_shots, content_start)
                if fallback is not None:
                    picks = [fallback]
                    logger.warning(
                        "start_select: Gemini returned no picks; using fallback "
                        "batch=%s idx=%s",
                        batch_id, fallback.get("idx"),
                    )

            # Validate, score, and filter. Gemini supplies a shortlist; code-side
            # rules make the final call so empty openings and weak dialogue do
            # not slip through just because a later shot has dialogue.
            validated: list[dict] = []
            skipped: list[dict] = []
            for p in picks:
                ranked_pick, skip_info = _rank_pick(p, head_shots, content_start)
                shot = head_shots[ranked_pick["idx"]]
                validation = validate_start_candidate(shot, head_shots, content_start)
                quality = ranked_pick["candidate_quality"]
                if validation.is_valid and not quality.get("reject_reasons"):
                    validated.append(ranked_pick)
                else:
                    reasons = list(skip_info["reasons"])
                    if validation.reason and validation.reason not in reasons:
                        reasons.append(validation.reason)
                    skip_info["reasons"] = reasons
                    skipped.append(skip_info)
                    logger.warning(
                        "start_select: skipping invalid pick batch=%s idx=%s reasons=%s",
                        batch_id, skip_info["idx"], reasons,
                    )

            if skipped:
                logger.info(
                    "start_select: batch=%s skipped %d/%d picks: %s",
                    batch_id, len(skipped), len(picks), json.dumps(skipped, ensure_ascii=False),
                )
            if not validated:
                fallback = _find_fallback_pick(head_shots, content_start)
                if fallback is not None:
                    validated = [fallback]
                    logger.warning(
                        "start_select: all picks filtered; using fallback "
                        "batch=%s idx=%s",
                        batch_id, fallback.get("idx"),
                    )
            validated.sort(
                key=lambda p: (
                    bool(p.get("is_fallback")),
                    -float(p.get("hook_score") or p.get("hook_strength") or 0.0),
                    float(
                        (p.get("candidate_quality") or {}).get("context_dependency")
                        or 0.0
                    ),
                )
            )

            # Map to (episode_no, local_start) and dedup
            candidates: list[dict] = []
            seen_global: list[float] = []
            for p in validated:
                shot = head_shots[p["idx"]]
                g = float(shot.get("start_time", 0.0))
                if any(abs(g - x) < DEDUP_GAP_S for x in seen_global):
                    continue
                loc = locate(g, offsets_data, durations)
                if loc is None:
                    continue
                ep_no, local_start = loc
                seen_global.append(g)
                quality = p.get("candidate_quality") or {}
                candidates.append({
                    "episode_no": ep_no,
                    "local_start": round(local_start, 2),
                    "global_start": round(g, 2),
                    "hook_strength": float(p.get("hook_strength", 0.0)),
                    "hook_score": float(p.get("hook_score") or p.get("hook_strength") or 0.0),
                    "candidate_quality": quality,
                    "is_fallback": bool(p.get("is_fallback")),
                    "reason": str(p.get("reason", "")),
                })
                if len(candidates) >= num_candidates:
                    break

            if not candidates:
                await highlight_batch_repo.mark_stage_failed(
                    stage_id,
                    f"no valid candidates after filtering {len(skipped)} picks",
                )
                return TaskExecutionResult.failed(
                    error=f"no valid candidates after filtering {len(skipped)} picks"
                )

            # Store candidates in orchestrator state
            state["candidates"] = candidates
            state["candidate_count"] = len(candidates)
            state["start_select_skipped"] = skipped
            await highlight_batch_repo.update_orchestrator_state(batch_id, state)

            # Create PENDING stage rows for each candidate
            for i, cand in enumerate(candidates):
                await highlight_batch_repo.create_stage(
                    batch_id=batch_id,
                    stage="span_plan",
                    candidate_idx=i,
                    input_json=cand,
                )

            result = {
                "candidate_count": len(candidates),
                "skipped_count": len(skipped),
                "skipped": skipped,
                "fallback_used": any(
                    bool(c.get("is_fallback"))
                    for c in candidates
                ),
                "candidates": candidates,
            }
            await highlight_batch_repo.mark_stage_ready(stage_id, result_json=result)

            logger.info(
                "start_select: done batch=%s candidates=%d skipped=%d",
                batch_id, len(candidates), len(skipped),
            )
            return TaskExecutionResult.succeeded(
                summary=f"start_select candidates={len(candidates)} skipped={len(skipped)}",
                details=result,
            )
        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}"
            next_attempt = int(task.attempt) + 1
            retryable = (
                _is_retryable_external_error(exc)
                and next_attempt < int(task.max_attempts)
            )
            request_id = _extract_request_id(error_text)
            state["start_select_retry"] = {
                "attempt": next_attempt,
                "max_attempts": int(task.max_attempts),
                "retryable": retryable,
                "request_id": request_id,
                "error": error_text[:1000],
            }
            await highlight_batch_repo.update_orchestrator_state(batch_id, state)
            if retryable:
                delay_s = _retry_delay_s(task.attempt)
                state["start_select_retry"]["next_delay_s"] = delay_s
                await highlight_batch_repo.update_orchestrator_state(batch_id, state)
                if hasattr(highlight_batch_repo, "mark_stage_retry_pending"):
                    await highlight_batch_repo.mark_stage_retry_pending(
                        stage_id,
                        (
                            f"retrying start_select attempt {next_attempt}/"
                            f"{int(task.max_attempts)} after {delay_s:.1f}s"
                            f"{' request_id=' + request_id if request_id else ''}: "
                            f"{error_text}"
                        ),
                    )
                logger.warning(
                    "start_select retryable failure batch=%s stage=%s attempt=%d/%d "
                    "delay=%.1fs request_id=%s error=%s",
                    batch_id, stage_id, next_attempt, int(task.max_attempts),
                    delay_s, request_id or "-", error_text,
                )
                if delay_s > 0:
                    await asyncio.sleep(delay_s)
                return TaskExecutionResult.failed(
                    error=error_text,
                    details={
                        "retryable": True,
                        "attempt": next_attempt,
                        "max_attempts": int(task.max_attempts),
                        "request_id": request_id,
                        "delay_s": delay_s,
                    },
                )
            await highlight_batch_repo.mark_stage_failed(stage_id, error_text)
            logger.error("start_select failed batch=%s: %s", batch_id, error_text)
            return TaskExecutionResult.failed(error=error_text)
        finally:
            await wake_highlight_batch(
                runtime=runtime,
                batch_id=batch_id,
                tenant_key=tenant_key,
                session_key=session_key,
            )

    return execute
