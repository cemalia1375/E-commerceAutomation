"""highlight_episode_prepare executor: per-episode download + normalize + OSS cache.

Each task downloads ONE episode, normalizes it to 720x1280, uploads the normalized
version to OSS for reuse by later stages, and records the result in fc_highlight_stage.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Awaitable, Callable

from simpleclaw.runtime.task_protocol import TaskEnvelope, TaskExecutionResult

from Flowcut.runtime.highlight_continuation import wake_highlight_batch
from Flowcut.runtime.highlight_ffmpeg import normalize_clip, probe_duration_seconds, run_ffmpeg
from Flowcut.services.asr_timeline import words_to_sentences

logger = logging.getLogger(__name__)

_IO_RETRY_DELAYS = (0.0, 1.0, 3.0)


def _extract_asr_wav(video_path: str, wav_path: str) -> None:
    """Extract mono 16k PCM WAV for ASR."""
    run_ffmpeg(
        [
            "-i", video_path,
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            wav_path,
        ],
        timeout=300,
    )


def _asr_config_present() -> bool:
    return bool(
        (os.getenv("FLOWCUT_ASR_APP_ID") or os.getenv("FLOWCUT_ASR_APP_KEY"))
        and (
            os.getenv("FLOWCUT_ASR_ACCESS_KEY_ID")
            or os.getenv("FLOWCUT_ASR_ACCESS_KEY")
        )
    )


async def _build_asr_sentences(video_path: str, wav_path: str) -> tuple[list[dict], str | None]:
    """Return sentence-level ASR timeline; failures are non-fatal to episode prep."""
    if not _asr_config_present():
        return [], "ASR credentials not configured"
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _extract_asr_wav, video_path, wav_path)
        # Reuse the existing ByteDance ASR client without making this module own
        # provider transport details.
        from Flowcut.runtime.executors import _call_asr_websocket_with_words

        _transcript, words = await _call_asr_websocket_with_words(wav_path)
        return words_to_sentences(words), None
    except Exception as exc:  # noqa: BLE001 - ASR is an optional boundary aid.
        logger.warning("episode_prepare ASR failed: %s", exc)
        return [], f"{type(exc).__name__}: {exc}"


async def _run_blocking_with_retry(loop, func, *args, label: str):
    last_error: BaseException | None = None
    for attempt, delay in enumerate(_IO_RETRY_DELAYS, start=1):
        if delay:
            await asyncio.sleep(delay)
        try:
            return await loop.run_in_executor(None, func, *args)
        except Exception as exc:  # noqa: BLE001 - retrying external IO/ffmpeg
            last_error = exc
            if attempt >= len(_IO_RETRY_DELAYS):
                raise
            logger.warning(
                "%s attempt %d/%d failed, retrying: %s",
                label, attempt, len(_IO_RETRY_DELAYS), exc,
            )
    if last_error is not None:
        raise last_error


def make_episode_prepare_executor(
    *,
    runtime,
    oss_client,
    highlight_batch_repo,
) -> Callable[[TaskEnvelope], Awaitable[TaskExecutionResult]]:
    """Create an executor that downloads + normalizes one episode.

    Task payload:
        {
            "batch_id": str,
            "stage_id": int,         # fc_highlight_stage.id
            "asset_id": int,         # fc_highlight_asset.id
            "episode_no": int,
            "oss_key": str,          # source video OSS key
            "tenant_key": str,
        }
    """

    async def execute(task: TaskEnvelope) -> TaskExecutionResult:
        payload = task.payload
        batch_id = str(payload["batch_id"])
        stage_id = int(payload["stage_id"])
        asset_id = int(payload["asset_id"])
        episode_no = int(payload["episode_no"])
        oss_key = str(payload.get("oss_key") or payload.get("oss_url") or "")
        tenant_key = str(payload.get("tenant_key", "flowcut"))
        session_key = str(payload.get("session_key", "highlight_plan"))

        if not oss_key:
            await highlight_batch_repo.mark_stage_failed(stage_id, "empty oss_key")
            await wake_highlight_batch(
                runtime=runtime,
                batch_id=batch_id,
                tenant_key=tenant_key,
                session_key=session_key,
            )
            return TaskExecutionResult.failed(
                error=f"episode_prepare: asset_id={asset_id} has no oss_key"
            )

        tmp_dir = tempfile.mkdtemp(prefix=f"flowcut_ep_prep_{batch_id}_ep{episode_no}_")
        try:
            stage = await highlight_batch_repo.get_stage(stage_id)
            if stage and stage.get("status") in ("READY", "FAILED", "SKIPPED", "CANCELLED"):
                return TaskExecutionResult.noop(
                    summary=f"episode_prepare stage {stage_id} already {stage.get('status')}"
                )
            if hasattr(highlight_batch_repo, "try_mark_stage_running"):
                claimed = await highlight_batch_repo.try_mark_stage_running(
                    stage_id, runtime_task_id=task.task_id,
                )
                if not claimed:
                    current = await highlight_batch_repo.get_stage(stage_id)
                    return TaskExecutionResult.wait_external(
                        summary=(
                            f"episode_prepare stage {stage_id} is "
                            f"{(current or {}).get('status', 'not claimed')}"
                        )
                    )
            else:
                await highlight_batch_repo.mark_stage_running(
                    stage_id, runtime_task_id=task.task_id,
                )

            loop = asyncio.get_running_loop()
            raw_path = os.path.join(tmp_dir, "raw.mp4")
            norm_path = os.path.join(tmp_dir, "norm.mp4")
            wav_path = os.path.join(tmp_dir, "asr.wav")

            # Download
            await _run_blocking_with_retry(
                loop, oss_client.download, oss_key, raw_path,
                label=f"episode_prepare download ep={episode_no}",
            )

            # Normalize
            await _run_blocking_with_retry(
                loop, normalize_clip, raw_path, norm_path,
                label=f"episode_prepare normalize ep={episode_no}",
            )

            # Probe duration
            duration = await loop.run_in_executor(None, probe_duration_seconds, norm_path)
            if duration <= 0:
                raise RuntimeError(
                    "normalized video duration probe returned 0.0s; "
                    "check ffprobe/ffmpeg availability or source video validity"
                )

            # Upload normalized version to OSS cache
            norm_oss_key = f"normalized/{tenant_key}/{asset_id}/norm.mp4"
            await _run_blocking_with_retry(
                loop, oss_client.upload, norm_path, norm_oss_key,
                label=f"episode_prepare upload ep={episode_no}",
            )

            asr_sentences, asr_error = await _build_asr_sentences(norm_path, wav_path)

            result = {
                "normalized_oss_key": norm_oss_key,
                "duration": round(duration, 2),
                "episode_no": episode_no,
                "asset_id": asset_id,
                "asr_sentences": asr_sentences,
            }
            if asr_error:
                result["asr_error"] = asr_error[:500]
            await highlight_batch_repo.mark_stage_ready(
                stage_id, result_json=result,
            )
            logger.info(
                "episode_prepare: done batch=%s ep=%d duration=%.1fs "
                "asr_sentences=%d oss=%s",
                batch_id, episode_no, duration, len(asr_sentences), norm_oss_key,
            )
            return TaskExecutionResult.succeeded(
                summary=f"episode_prepare ep={episode_no} dur={duration:.1f}s",
                details=result,
            )
        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}"
            await highlight_batch_repo.mark_stage_failed(stage_id, error_text)
            logger.error("episode_prepare failed batch=%s ep=%d: %s", batch_id, episode_no, error_text)
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
