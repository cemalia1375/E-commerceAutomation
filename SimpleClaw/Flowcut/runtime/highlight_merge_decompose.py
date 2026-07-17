"""highlight_merge_decompose executor: merge normalized episodes + coarse decompose.

Takes the normalized episodes produced by episode_prepare, concatenates them,
runs Gemini analyze_video + PySceneDetect for coarse decompose, and stores
the results (head_shots + content_start) in fc_highlight_batch.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
from typing import Awaitable, Callable

from simpleclaw.runtime.task_protocol import TaskEnvelope, TaskExecutionResult

from Flowcut.runtime.highlight_continuation import wake_highlight_batch
from Flowcut.runtime.highlight_ffmpeg import (
    probe_duration_seconds,
)
from Flowcut.services.gemini_video import analyze_video
from Flowcut.services.scene_align import detect_scene_cuts, align_timestamps
from Flowcut.services.clip_planner import detect_content_start

logger = logging.getLogger(__name__)

_MERGE_DECOMPOSE_EP_TIMEOUT_S = float(
    os.getenv("FLOWCUT_MERGE_DECOMPOSE_EP_TIMEOUT_S", "300")
)
_MERGE_DECOMPOSE_EP_CONCURRENCY = max(
    1,
    int(os.getenv("FLOWCUT_MERGE_DECOMPOSE_EP_CONCURRENCY", "1")),
)
_GEMINI_RETRY_BACKOFF_S = (0.0, 5.0, 15.0)
_IO_RETRY_BACKOFF_S = (0.0, 1.0, 3.0)
_GEMINI_RETRYABLE_MARKERS = (
    "503",
    "UNAVAILABLE",
    "RemoteProtocolError",
    "Server disconnected",
    "ConnectionError",
    "ConnectError",
    "SSL",
    "UNEXPECTED_EOF",
    "ReadError",
    "ReadTimeout",
    "Timeout",
    "Connection reset",
    "forcibly closed",
    "WinError 10054",
    "WinError 10053",
    "429",
    "RESOURCE_EXHAUSTED",
    "quota",
)


def _is_retryable_error(exc: BaseException) -> bool:
    if isinstance(exc, asyncio.TimeoutError):
        return True
    message = str(exc)
    return any(marker in message for marker in _GEMINI_RETRYABLE_MARKERS)


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


def make_merge_decompose_executor(
    *,
    runtime,
    oss_client,
    highlight_batch_repo,
) -> Callable[[TaskEnvelope], Awaitable[TaskExecutionResult]]:
    """Create an executor that merges normalized episodes + runs coarse decompose.

    Task payload:
        {
            "batch_id": str,
            "stage_id": int,
            "tenant_key": str,
            "drama_name": str,
        }
    """

    async def execute(task: TaskEnvelope) -> TaskExecutionResult:
        payload = task.payload
        batch_id = str(payload["batch_id"])
        stage_id = int(payload["stage_id"])
        tenant_key = str(payload.get("tenant_key", "flowcut"))
        drama_name = str(payload.get("drama_name", ""))

        batch = await highlight_batch_repo.get_batch(batch_id)
        if batch is None:
            return TaskExecutionResult.failed(error=f"batch {batch_id} not found")

        # Read episode_prepare results
        ep_stages = await highlight_batch_repo.list_stages(
            batch_id, stage="episode_prepare", status="READY",
        )
        if not ep_stages:
            await highlight_batch_repo.mark_stage_failed(
                stage_id, "no completed episode_prepare stages",
            )
            await wake_highlight_batch(
                runtime=runtime,
                batch_id=batch_id,
                tenant_key=tenant_key,
            )
            return TaskExecutionResult.failed(error="no completed episode_prepare stages")

        # Sort by episode_no
        ep_stages.sort(key=lambda s: (s.get("episode_no") or 0))
        ep_results: list[dict] = []
        for s in ep_stages:
            rj = s.get("result_json")
            if isinstance(rj, str):
                rj = json.loads(rj)
            if rj:
                ep_results.append(rj)

        tmp_dir = tempfile.mkdtemp(prefix=f"flowcut_merge_decompose_{batch_id}_")
        try:
            stage = await highlight_batch_repo.get_stage(stage_id)
            if stage and stage.get("status") in ("READY", "FAILED", "SKIPPED", "CANCELLED"):
                return TaskExecutionResult.noop(
                    summary=f"merge_decompose stage {stage_id} already {stage.get('status')}"
                )
            if hasattr(highlight_batch_repo, "try_mark_stage_running"):
                claimed = await highlight_batch_repo.try_mark_stage_running(
                    stage_id, runtime_task_id=task.task_id,
                )
                if not claimed:
                    current = await highlight_batch_repo.get_stage(stage_id)
                    return TaskExecutionResult.wait_external(
                        summary=(
                            f"merge_decompose stage {stage_id} is "
                            f"{(current or {}).get('status', 'not claimed')}"
                        )
                    )
            else:
                await highlight_batch_repo.mark_stage_running(
                    stage_id, runtime_task_id=task.task_id,
                )
            loop = asyncio.get_running_loop()

            # Download all normalized episodes
            norm_paths: list[str] = []
            offsets: list[tuple[int, float]] = []
            durations: dict[int, float] = {}
            cum = 0.0

            for ep in ep_results:
                ep_no = int(ep["episode_no"])
                norm_key = str(ep["normalized_oss_key"])
                norm_path = os.path.join(tmp_dir, f"ep{ep_no}_norm.mp4")
                await _run_blocking_with_retry(
                    loop, oss_client.download, norm_key, norm_path,
                    label=f"merge_decompose download ep={ep_no}",
                )
                dur = await _run_blocking_with_retry(
                    loop, probe_duration_seconds, norm_path,
                    label=f"merge_decompose probe ep={ep_no}",
                )
                if dur <= 0:
                    dur = float(ep.get("duration") or 0.0)
                if dur <= 0:
                    raise RuntimeError(f"episode {ep_no} normalized duration is 0")

                norm_paths.append(norm_path)
                offsets.append((ep_no, cum))
                durations[ep_no] = dur
                cum += dur

            state = batch.get("orchestrator_state_json") or {}
            if isinstance(state, str):
                state = json.loads(state)
            state["merge_decompose_progress"] = {
                "total": len(norm_paths),
                "completed": 0,
                "episodes": {},
            }
            await highlight_batch_repo.update_orchestrator_state(batch_id, state)

            async def _analyze_video_with_retry(path: str, ep_no: int) -> list[dict]:
                last_error: BaseException | None = None
                for attempt, delay in enumerate(_GEMINI_RETRY_BACKOFF_S, start=1):
                    if delay > 0:
                        await asyncio.sleep(delay)
                    try:
                        return await analyze_video(
                            path,
                            timeout_s=_MERGE_DECOMPOSE_EP_TIMEOUT_S,
                        )
                    except Exception as exc:  # noqa: BLE001 - retry gate below
                        last_error = exc
                        if attempt >= len(_GEMINI_RETRY_BACKOFF_S) or not _is_retryable_error(exc):
                            raise
                        logger.warning(
                            "merge_decompose: batch=%s ep=%d attempt %d/%d failed, retrying: %s",
                            batch_id, ep_no, attempt, len(_GEMINI_RETRY_BACKOFF_S), exc,
                        )
                if last_error is not None:
                    raise last_error
                return []

            # Analyze each episode independently. Keep Gemini video requests
            # conservatively serialized by default: relay providers tend to
            # queue or time out when several video requests from one batch are
            # submitted at once.
            decompose_semaphore = asyncio.Semaphore(_MERGE_DECOMPOSE_EP_CONCURRENCY)

            async def _decompose_episode(
                index: int,
                path: str,
                ep_no: int,
                offset: float,
            ) -> dict:
                started = time.perf_counter()
                async with decompose_semaphore:
                    cuts_task = asyncio.create_task(detect_scene_cuts(path))
                    try:
                        raw_shots = await _analyze_video_with_retry(path, ep_no)
                        cuts = await cuts_task
                        aligned = align_timestamps(list(raw_shots), cuts) if raw_shots else []
                        shots = [
                            {
                                **shot,
                                "start_time": float(shot.get("start_time", 0.0)) + offset,
                                "end_time": float(shot.get("end_time", 0.0)) + offset,
                            }
                            for shot in aligned
                        ]
                        return {
                            "index": index,
                            "episode_no": ep_no,
                            "shots": shots,
                            "error": None,
                            "elapsed_s": round(time.perf_counter() - started, 2),
                        }
                    except Exception as exc:  # noqa: BLE001 - recorded as per-episode diagnostic
                        if not cuts_task.done():
                            cuts_task.cancel()
                        try:
                            await cuts_task
                        except BaseException:
                            pass
                        return {
                            "index": index,
                            "episode_no": ep_no,
                            "shots": [],
                            "error": f"{type(exc).__name__}: {exc}",
                            "elapsed_s": round(time.perf_counter() - started, 2),
                        }

            tasks = [
                asyncio.create_task(
                    _decompose_episode(index, path, offsets[index][0], offsets[index][1])
                )
                for index, path in enumerate(norm_paths)
            ]
            episode_shots: list[list[dict]] = [[] for _ in norm_paths]
            decompose_diagnostics: list[dict] = []
            completed = 0
            for done_task in asyncio.as_completed(tasks):
                result = await done_task
                completed += 1
                ep_no = int(result["episode_no"])
                shots = list(result.get("shots") or [])
                episode_shots[int(result["index"])] = shots
                ep_state = {
                    "status": "FAILED" if result.get("error") else "READY",
                    "shot_count": len(shots),
                    "elapsed_s": result.get("elapsed_s"),
                }
                if result.get("error"):
                    ep_state["error"] = str(result["error"])[:500]
                    decompose_diagnostics.append({
                        "episode_no": ep_no,
                        "error": str(result["error"]),
                    })
                state["merge_decompose_progress"] = {
                    "total": len(norm_paths),
                    "completed": completed,
                    "episodes": {
                        **(state.get("merge_decompose_progress") or {}).get("episodes", {}),
                        str(ep_no): ep_state,
                    },
                }
                await highlight_batch_repo.update_orchestrator_state(batch_id, state)
                logger.info(
                    "merge_decompose: batch=%s ep=%d done shots=%d error=%s",
                    batch_id, ep_no, len(shots), bool(result.get("error")),
                )
            head_shots = [
                shot
                for shots in episode_shots
                for shot in shots
            ]
            if not head_shots:
                error_text = "empty decompose result"
                if decompose_diagnostics:
                    error_text += ": " + "; ".join(
                        f"ep{d['episode_no']} {d['error']}" for d in decompose_diagnostics
                    )
                await highlight_batch_repo.mark_stage_failed(stage_id, error_text)
                return TaskExecutionResult.failed(error=f"merge_decompose: {error_text}")

            content_start = detect_content_start(head_shots)

            # Store results in batch
            await highlight_batch_repo.set_merged_shots(batch_id, head_shots)

            # Update orchestrator state
            state["offsets"] = [(ep, round(off, 2)) for ep, off in offsets]
            state["durations"] = {str(k): round(v, 2) for k, v in durations.items()}
            state["normalized_episodes"] = {
                str(int(ep["episode_no"])): {
                    "asset_id": int(ep["asset_id"]),
                    "normalized_oss_key": str(ep["normalized_oss_key"]),
                    "duration": round(float(ep.get("duration") or durations[int(ep["episode_no"])]), 2),
                    "asr_sentences": list(ep.get("asr_sentences") or []),
                    **(
                        {"asr_error": str(ep.get("asr_error"))[:500]}
                        if ep.get("asr_error") else {}
                    ),
                }
                for ep in ep_results
            }
            state["content_start"] = round(content_start, 2)
            state["head_episode_nos"] = [int(ep["episode_no"]) for ep in ep_results]
            state["merge_decompose_progress"]["completed"] = len(norm_paths)
            await highlight_batch_repo.update_orchestrator_state(batch_id, state)

            result = {
                "total_shots": len(head_shots),
                "content_start": round(content_start, 2),
                "merged_duration": round(cum, 2),
                "episode_count": len(ep_results),
                "failed_episodes": decompose_diagnostics,
            }
            await highlight_batch_repo.mark_stage_ready(stage_id, result_json=result)

            logger.info(
                "merge_decompose: done batch=%s shots=%d content_start=%.1fs",
                batch_id, len(head_shots), content_start,
            )
            return TaskExecutionResult.succeeded(
                summary=f"merge_decompose shots={len(head_shots)} content_start={content_start:.1f}s",
                details=result,
            )
        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}"
            await highlight_batch_repo.mark_stage_failed(stage_id, error_text)
            logger.error("merge_decompose failed batch=%s: %s", batch_id, error_text)
            return TaskExecutionResult.failed(error=error_text)
        finally:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
            await wake_highlight_batch(
                runtime=runtime,
                batch_id=batch_id,
                tenant_key=tenant_key,
            )

    return execute
