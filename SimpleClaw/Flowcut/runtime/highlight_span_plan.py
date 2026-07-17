"""highlight_span_plan executor: per-candidate span decompose + clip plan.

Reads candidate info from fc_highlight_stage.input_json, downloads normalized
episodes, cuts the span, runs Gemini fine decompose, picks end boundary,
builds the clip plan, creates fc_creative, and submits highlight_compose.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
import uuid
from typing import Awaitable, Callable

from simpleclaw.runtime.task_protocol import TaskEnvelope, TaskExecutionResult

from Flowcut.runtime.highlight_continuation import wake_highlight_batch
from Flowcut.runtime.highlight_ffmpeg import (
    concat_clips,
    cut_clip,
    normalize_clip,
    probe_duration_seconds,
    write_concat_list,
)
from Flowcut.runtime.streams import FlowcutTaskStream
from Flowcut.services.gemini_video import analyze_video
from Flowcut.services.scene_align import detect_scene_cuts, align_timestamps
from Flowcut.services.asr_timeline import (
    build_span_asr_timeline,
    correct_start_to_sentence,
    pick_asr_end_boundary,
)
from Flowcut.services.clip_planner import (
    IDEAL,
    MAX_FORWARD_EPISODES,
    WINDOW,
    EpisodeRef,
    StartCandidate,
    build_clip_plan,
    expand_start_with_context,
    pick_end_boundary,
    resolve_real_end,
    timeline_from_shots,
)

logger = logging.getLogger(__name__)

_HIGHLIGHT_SPAN_PAD_S = 8.0
_IO_RETRY_BACKOFF_S = (0.0, 1.0, 3.0)
_GEMINI_RETRY_BACKOFF_S = (0.0, 5.0, 15.0)
_GEMINI_RETRYABLE_MARKERS = (
    "503",
    "UNAVAILABLE",
    "RemoteProtocolError",
    "Server disconnected",
    "ConnectionError",
    "ConnectError",
    "ReadError",
    "ReadTimeout",
    "Timeout",
    "Connection reset",
    "WinError 10054",
    "429",
    "RESOURCE_EXHAUSTED",
)


async def _run_blocking_with_retry(loop, func, *args, label: str):
    last_error: BaseException | None = None
    for attempt, delay in enumerate(_IO_RETRY_BACKOFF_S, start=1):
        if delay:
            await asyncio.sleep(delay)
        try:
            return await loop.run_in_executor(None, func, *args)
        except Exception as exc:  # noqa: BLE001 - retrying external IO/ffmpeg
            last_error = exc
            if attempt >= len(_IO_RETRY_BACKOFF_S):
                raise
            logger.warning(
                "%s attempt %d/%d failed, retrying: %s",
                label, attempt, len(_IO_RETRY_BACKOFF_S), exc,
            )
    if last_error is not None:
        raise last_error


def _json_object(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _episode_no_from_asset(asset: dict) -> int:
    """Return stored episode_no, falling back to the first number in the asset name."""
    try:
        episode_no = int(asset.get("episode_no") or 0)
    except (TypeError, ValueError):
        episode_no = 0
    if episode_no > 0:
        return episode_no
    match = re.search(r"\d+", str(asset.get("name") or ""))
    return int(match.group()) if match else 0


def _is_retryable_gemini_error(exc: BaseException) -> bool:
    if isinstance(exc, asyncio.TimeoutError):
        return True
    message = str(exc)
    return any(marker in message for marker in _GEMINI_RETRYABLE_MARKERS)


async def _analyze_video_with_retry(path: str) -> list[dict]:
    last_error: BaseException | None = None
    for attempt, delay in enumerate(_GEMINI_RETRY_BACKOFF_S, start=1):
        if delay:
            await asyncio.sleep(delay)
        try:
            return await analyze_video(path)
        except Exception as exc:  # noqa: BLE001 - retry gate below
            last_error = exc
            if attempt >= len(_GEMINI_RETRY_BACKOFF_S) or not _is_retryable_gemini_error(exc):
                raise
            logger.warning(
                "span_plan analyze attempt %d/%d failed, retrying: %s",
                attempt, len(_GEMINI_RETRY_BACKOFF_S), exc,
            )
    if last_error is not None:
        raise last_error
    return []


def make_span_plan_executor(
    *,
    runtime,
    oss_client,
    highlight_batch_repo,
    highlight_asset_repo,
    creative_repo,
) -> Callable[[TaskEnvelope], Awaitable[TaskExecutionResult]]:
    """Create an executor that plans one highlight candidate.

    Task payload:
        {
            "batch_id": str,
            "stage_id": int,        # fc_highlight_stage.id
            "candidate_idx": int,
            "tenant_key": str,
            "session_key": str,
            "connector_asset_id": int | None,
        }
    """

    async def execute(task: TaskEnvelope) -> TaskExecutionResult:
        payload = task.payload
        batch_id = str(payload["batch_id"])
        stage_id = int(payload["stage_id"])
        candidate_idx = int(payload.get("candidate_idx", 0))
        tenant_key = str(payload.get("tenant_key", "flowcut"))
        session_key = str(payload.get("session_key", "highlight_plan"))
        connector_asset_id_raw = payload.get("connector_asset_id")
        connector_asset_id: int | None = (
            int(connector_asset_id_raw) if connector_asset_id_raw is not None else None
        )

        # Read stage info
        stage = await highlight_batch_repo.get_stage(stage_id)
        if stage is None:
            return TaskExecutionResult.failed(error=f"stage {stage_id} not found")

        input_json = stage.get("input_json") or {}
        if isinstance(input_json, str):
            input_json = json.loads(input_json)

        cand_ep_no = int(input_json["episode_no"])
        cand_local_start = float(input_json["local_start"])
        hook_strength = float(input_json.get("hook_strength", 0.0))
        hook_score = float(input_json.get("hook_score", hook_strength))
        candidate_quality = _json_object(input_json.get("candidate_quality"))
        is_fallback = bool(input_json.get("is_fallback"))
        reason = str(input_json.get("reason", ""))

        # Read batch for orchestrator state
        batch = await highlight_batch_repo.get_batch(batch_id)
        if batch is None:
            return TaskExecutionResult.failed(error=f"batch {batch_id} not found")

        state = batch.get("orchestrator_state_json") or {}
        if isinstance(state, str):
            state = json.loads(state)
        drama_name = str(batch.get("drama_name", ""))
        content_start = float(state.get("content_start", 0.0))
        head_ep_nos = state.get("head_episode_nos") or []

        # Load episode index
        rows = await highlight_asset_repo.list_by_tenant(
            tenant_key, asset_type="episode_source",
            drama_name=drama_name, limit=500,
        )
        episodes = sorted(rows, key=_episode_no_from_asset)
        ep_index = {
            _episode_no_from_asset(a): a
            for a in episodes
            if _episode_no_from_asset(a) > 0
        }

        target_len = WINDOW[1] + _HIGHLIGHT_SPAN_PAD_S
        _merge_first_ep = int(head_ep_nos[0]) if head_ep_nos else 0

        # Apply pre-roll expansion
        _local_content_start = content_start if cand_ep_no == _merge_first_ep else 0.0
        expanded_start, expand_log = expand_start_with_context(
            cand_local_start, _local_content_start,
        )
        start_adjustment_reason: list[str] = []
        if expanded_start != cand_local_start:
            start_adjustment_reason.append("pre_roll")

        tmp_dir = tempfile.mkdtemp(prefix=f"flowcut_span_plan_{batch_id}_c{candidate_idx}_")
        try:
            stage_status = str(stage.get("status") or "PENDING")
            stage_task_id = str(stage.get("runtime_task_id") or "")
            if stage_status in ("READY", "FAILED", "SKIPPED", "CANCELLED"):
                return TaskExecutionResult.noop(
                    summary=f"span_plan stage {stage_id} already {stage_status}"
                )
            if stage_status == "PROCESSING" and stage_task_id == task.task_id:
                pass
            elif hasattr(highlight_batch_repo, "try_mark_stage_running"):
                claimed = await highlight_batch_repo.try_mark_stage_running(
                    stage_id, runtime_task_id=task.task_id,
                )
                if not claimed:
                    current = await highlight_batch_repo.get_stage(stage_id)
                    return TaskExecutionResult.wait_external(
                        summary=(
                            f"span_plan stage {stage_id} is "
                            f"{(current or {}).get('status', 'not claimed')}"
                        )
                    )
            else:
                await highlight_batch_repo.mark_stage_running(
                    stage_id, runtime_task_id=task.task_id,
                )
            loop = asyncio.get_running_loop()
            normalized_cache = _json_object(state.get("normalized_episodes"))
            asr_by_episode: dict[int, list[dict]] = {}
            for ep_key, ep_data in normalized_cache.items():
                ep_obj = _json_object(ep_data)
                try:
                    ep_no_key = int(ep_key)
                except (TypeError, ValueError):
                    continue
                asr_by_episode[ep_no_key] = list(ep_obj.get("asr_sentences") or [])

            asr_start_correction: dict | None = None
            corrected_start, asr_start_correction = correct_start_to_sentence(
                candidate_start=cand_local_start,
                current_start=expanded_start,
                content_start=_local_content_start,
                sentences=asr_by_episode.get(cand_ep_no, []),
            )
            if corrected_start != expanded_start:
                expand_log = (
                    f"{expand_log}; ASR sentence start correction "
                    f"{expanded_start:.1f}s -> {corrected_start:.1f}s"
                    if expand_log
                    else (
                        "ASR sentence start correction "
                        f"{expanded_start:.1f}s -> {corrected_start:.1f}s"
                    )
                )
                expanded_start = corrected_start
                start_adjustment_reason.append("inside_asr_sentence")

            # Build episode refs + segment specs
            ep_refs: list[EpisodeRef] = []
            seg_specs: list[tuple[str, float, float]] = []
            acc = 0.0
            ep_no = cand_ep_no
            steps = 0

            while ep_no in ep_index and steps < MAX_FORWARD_EPISODES:
                asset = ep_index[ep_no]
                oss_key = str(asset.get("oss_key") or asset.get("oss_url") or "")

                norm = os.path.join(tmp_dir, f"ep{ep_no}_norm.mp4")
                cached = _json_object(normalized_cache.get(str(ep_no)))
                if cached.get("normalized_oss_key"):
                    await _run_blocking_with_retry(
                        loop,
                        oss_client.download,
                        str(cached["normalized_oss_key"]),
                        norm,
                        label=f"span_plan download normalized ep={ep_no}",
                    )
                    dur = float(cached.get("duration") or 0.0)
                    if dur <= 0:
                        dur = await _run_blocking_with_retry(
                            loop, probe_duration_seconds, norm,
                            label=f"span_plan probe normalized ep={ep_no}",
                        )
                else:
                    raw = os.path.join(tmp_dir, f"ep{ep_no}_raw.mp4")
                    await _run_blocking_with_retry(
                        loop, oss_client.download, oss_key, raw,
                        label=f"span_plan download raw ep={ep_no}",
                    )
                    await _run_blocking_with_retry(
                        loop, normalize_clip, raw, norm,
                        label=f"span_plan normalize ep={ep_no}",
                    )
                    dur = await _run_blocking_with_retry(
                        loop, probe_duration_seconds, norm,
                        label=f"span_plan probe ep={ep_no}",
                    )

                base = expanded_start if ep_no == cand_ep_no else 0.0
                avail = dur - base
                if avail <= 0:
                    break
                take = min(avail, target_len - acc)
                seg_specs.append((norm, base, base + take))
                ep_refs.append(EpisodeRef(
                    asset_id=int(asset["id"]), episode_no=ep_no,
                    oss_key=oss_key, duration=dur,
                ))
                acc += take
                steps += 1
                if acc >= target_len:
                    break
                ep_no += 1

            capacity = acc
            if capacity < WINDOW[0]:
                await highlight_batch_repo.mark_stage_skipped(
                    stage_id, f"capacity {capacity:.1f}s < {WINDOW[0]}s"
                )
                return TaskExecutionResult.succeeded(
                    summary=f"span_plan skipped: capacity {capacity:.1f}s too short"
                )

            # Cut + concat span
            span_cut_paths: list[str] = []
            uid = f"c{candidate_idx}"
            for i, (src, cs, ce) in enumerate(seg_specs):
                out = os.path.join(tmp_dir, f"span_{uid}_{i}.mp4")
                await loop.run_in_executor(None, cut_clip, src, out, cs, ce)
                span_cut_paths.append(out)

            if len(span_cut_paths) == 1:
                span_path = span_cut_paths[0]
            else:
                span_path = os.path.join(tmp_dir, f"span_{uid}.mp4")
                sl = os.path.join(tmp_dir, f"span_{uid}_concat.txt")
                write_concat_list(sl, span_cut_paths)
                await loop.run_in_executor(None, concat_clips, sl, span_path)

            # Fine decompose + PySceneDetect
            span_cuts_task = asyncio.create_task(detect_scene_cuts(span_path))
            span_raw = await _analyze_video_with_retry(span_path)
            span_phys = await span_cuts_task
            span_shots = align_timestamps(list(span_raw), span_phys) if span_raw else []

            # Build expanded candidate
            expanded_cand = StartCandidate(
                episode_no=cand_ep_no,
                local_start=expanded_start,
                global_start=float(input_json.get("global_start", 0.0)),
                hook_strength=hook_strength,
                reason=reason,
            )

            # Pick end boundary
            asr_span_timeline = build_span_asr_timeline(
                start_episode_no=cand_ep_no,
                start_local=expanded_start,
                episode_refs=ep_refs,
                sentences_by_episode=asr_by_episode,
            )
            asr_end_pick = pick_asr_end_boundary(
                asr_span_timeline,
                window=WINDOW,
                ideal=IDEAL,
            )
            asr_end_correction: dict | None = None
            timeline = timeline_from_shots(span_shots)
            if asr_end_pick is not None:
                end = resolve_real_end(
                    expanded_start,
                    ep_refs,
                    float(asr_end_pick["cum_end"]),
                    boundary_type="asr_sentence",
                )
                asr_end_correction = {
                    "source": "asr_sentence",
                    "episode_no": int(asr_end_pick["episode_no"]),
                    "local_end": round(float(asr_end_pick["local_end"]), 3),
                    "cum_end": round(float(asr_end_pick["cum_end"]), 3),
                    "text": str(asr_end_pick.get("text") or ""),
                }
            elif timeline:
                eb = pick_end_boundary(timeline)
                end = resolve_real_end(
                    expanded_start, ep_refs, eb.cum_time,
                    boundary_type=eb.boundary_type,
                )
            else:
                end = resolve_real_end(expanded_start, ep_refs, min(IDEAL, capacity))

            plan = build_clip_plan(expanded_cand, end, ep_refs)
            if not plan.entries:
                await highlight_batch_repo.mark_stage_skipped(
                    stage_id, "empty clip plan"
                )
                return TaskExecutionResult.succeeded(summary="span_plan: empty clip plan")

            # Build clip plan dict
            clip_plan_dict = {
                "drama_name": drama_name,
                "boundary_type": plan.boundary_type,
                "total_duration": plan.total_duration,
                "start_episode_no": plan.start_episode_no,
                "start_local": plan.start_local,
                "entries": [
                    {"asset_id": e.asset_id, "episode_no": e.episode_no,
                     "cut_start": e.cut_start, "cut_end": e.cut_end,
                     "oss_key": e.oss_key}
                    for e in plan.entries
                ],
                "content_start": content_start,
                "original_start": cand_local_start,
                "original_gemini_start": cand_local_start,
                "fixed_pre_roll_start": round(cand_local_start - 3.0, 3),
                "asr_aligned_start": (
                    asr_start_correction.get("to")
                    if asr_start_correction else None
                ),
                "context_expanded_start": expanded_start,
                "start_adjustment_reason": start_adjustment_reason,
                "expanded_start": expanded_start,
                "pre_roll_applied_s": round(cand_local_start - expanded_start, 2),
                "hook_score": hook_score,
                "is_fallback": is_fallback,
                "candidate_quality": candidate_quality,
                "asr_start_correction": asr_start_correction,
                "asr_end_correction": asr_end_correction,
                "asr_span_sentence_count": len(asr_span_timeline),
                "correction_log": expand_log or None,
            }

            reason_dict = {
                "hook_strength": hook_strength,
                "hook_score": hook_score,
                "is_fallback": is_fallback,
                "candidate_quality": candidate_quality,
                "reason": reason,
                "boundary_type": plan.boundary_type,
                "content_start": content_start,
                "corrected": (
                    bool(expand_log)
                    or expanded_start != cand_local_start
                    or asr_end_correction is not None
                ),
            }

            # Create creative + submit compose
            creative = await creative_repo.create_cross_episode_job(
                tenant_key=tenant_key,
                session_key=session_key,
                script_id=None,
                batch_id=batch_id,
                source_asset_id=ep_index[cand_ep_no]["id"],
                clip_plan_json=json.dumps(clip_plan_dict, ensure_ascii=False),
                highlight_start=expanded_start,
                highlight_reason_json=json.dumps(reason_dict, ensure_ascii=False),
                connector_asset_id=connector_asset_id,
            )
            creative_id = int(creative["id"])

            # Immediately submit compose
            await creative_repo.update_status(creative_id, "PROCESSING")
            compose_env = TaskEnvelope(
                task_type="highlight_compose",
                payload={"creative_id": creative_id},
                stream=FlowcutTaskStream.VIDEO_COMPOSE,
                tenant_key=tenant_key,
                session_key=session_key or None,
                scope_key=f"highlight_compose:{creative_id}",
            )
            await runtime.submit_task(
                compose_env,
                tool_name="highlight_compose",
                summary=f"compose highlight creative {creative_id}",
            )

            result = {
                "creative_id": creative_id,
                "start_episode_no": plan.start_episode_no,
                "boundary_type": plan.boundary_type,
                "total_duration": plan.total_duration,
                "expanded_start": expanded_start,
                "expand_log": expand_log,
                "asr_start_correction": asr_start_correction,
                "asr_end_correction": asr_end_correction,
            }
            await highlight_batch_repo.mark_stage_ready(
                stage_id, creative_id=creative_id, result_json=result,
            )

            logger.info(
                "span_plan: done batch=%s candidate=%d creative=%d "
                "gemini_start=%.1fs expanded=%.1fs boundary=%s dur=%.1fs",
                batch_id, candidate_idx, creative_id,
                cand_local_start, expanded_start,
                plan.boundary_type, plan.total_duration,
            )
            return TaskExecutionResult.succeeded(
                summary=f"span_plan candidate={candidate_idx} creative={creative_id}",
                details=result,
            )
        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}"
            await highlight_batch_repo.mark_stage_failed(stage_id, error_text)
            logger.error(
                "span_plan failed batch=%s candidate=%d: %s",
                batch_id, candidate_idx, error_text,
            )
            return TaskExecutionResult.failed(error=error_text)
        finally:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
            await wake_highlight_batch(
                runtime=runtime,
                batch_id=batch_id,
                tenant_key=tenant_key,
                session_key=session_key,
            )

    return execute
