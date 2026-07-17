"""FlowCut 任务执行器工厂。

每个 make_*_executor() 返回一个异步可调用对象（executor function），
签名为 async def executor(task: TaskEnvelope) -> TaskExecutionResult。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import struct
import subprocess
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Awaitable, Callable

import aiohttp

logger = logging.getLogger(__name__)

from simpleclaw.runtime.task_protocol import TaskEnvelope, TaskExecutionResult
from Flowcut.storage.material_repo import MaterialRepository
from Flowcut.storage.oss_client import build_oss_client
from Flowcut.storage.reference_video_repo import ReferenceVideoRepository
from Flowcut.storage.script_repo import ScriptRepository
from Flowcut.storage.vector_store import VectorStore
from Flowcut.services.embedding import EmbeddingService
from Flowcut.services.gemini_video import (
    analyze_highlight_video,
    analyze_video,
    select_start_shots,
)
from Flowcut.services.scene_align import detect_scene_cuts, align_timestamps
from Flowcut.runtime.streams import FlowcutTaskStream
from Flowcut.runtime.highlight_ffmpeg import probe_duration_seconds as _probe_highlight_duration_seconds
from Flowcut.services.clip_planner import (
    DEDUP_GAP_S,
    DEFAULT_CANDIDATES,
    IDEAL,
    MIN_DIALOGUE_CHARS,
    PRE_ROLL_S,
    ContentValidation,
    EpisodeRef,
    MAX_FORWARD_EPISODES,
    START_SEARCH_EPISODES,
    StartCandidate,
    WINDOW,
    build_clip_plan,
    detect_content_start,
    expand_start_with_context,
    locate,
    match_drama_episodes,
    pick_end_boundary,
    resolve_real_end,
    timeline_from_shots,
    validate_start_candidate,
)

# Stage C 的 span 比理想收尾窗口 (WINDOW[1]) 多取一点缓冲，保证窗口内有可选切点。
_HIGHLIGHT_SPAN_PAD_S = 8.0

# Gemini 调用硬超时（秒）。Stage A/C 的视频分析是 pipeline 瓶颈，
# None 意味着默认 1800s（30min），对 2-10min 的单集视频过于宽松。
# 设 300s（5min）作为安全上限 — 正常 2min 内完成，超时说明 Gemini 异常，
# 由 _gemini_retry 兜底重试。可通过 FLOWCUT_GEMINI_HARD_TIMEOUT_S 覆盖。
_GEMINI_HARD_TIMEOUT_S = float(os.getenv("FLOWCUT_GEMINI_HARD_TIMEOUT_S", "300"))


class EmptyDecomposeResultError(RuntimeError):
    """Raised when Gemini returns no usable scene segments for a video."""


VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".aac", ".flac", ".ogg", ".m4a"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}

# Hard wall-clock cap for one material's whole _process_video pipeline
# (OSS download + ffmpeg + Gemini + ASR). Beyond this, the task fails.
_MATERIAL_PROCESS_TIMEOUT_S = float(os.getenv("FLOWCUT_MATERIAL_PROCESS_TIMEOUT_S", "900"))
# Hard cap on the ASR WebSocket session.
_ASR_TIMEOUT_S = float(os.getenv("FLOWCUT_ASR_TIMEOUT_S", "180"))


_ASR_WS_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"
_ASR_RESOURCE_ID = "volc.bigasr.sauc.duration"
_ASR_CHUNK_BYTES = 5120  # 160ms @ 16kHz 16-bit mono


def _get_asr_config() -> tuple[str, str]:
    """从环境变量读取 ASR 凭据。"""
    app_key = os.getenv("FLOWCUT_ASR_APP_ID") or os.getenv("FLOWCUT_ASR_APP_KEY")
    access_key = (
        os.getenv("FLOWCUT_ASR_ACCESS_KEY_ID")
        or os.getenv("FLOWCUT_ASR_ACCESS_KEY")
    )
    if not app_key or not access_key:
        raise KeyError(
            "FLOWCUT_ASR_APP_ID/FLOWCUT_ASR_APP_KEY and "
            "FLOWCUT_ASR_ACCESS_KEY_ID/FLOWCUT_ASR_ACCESS_KEY are required"
        )
    return app_key, access_key


def _ws_frame(msg_type: int, payload: bytes, *, json_serial: bool = False) -> bytes:
    """构造豆包 ASR WebSocket 二进制帧。"""
    serial_byte = 0x10 if json_serial else 0x00
    header = struct.pack(">BBBB", 0x11, msg_type, serial_byte, 0x00)
    return header + struct.pack(">I", len(payload)) + payload


async def _call_asr_websocket(wav_path: str) -> str:
    """通过 WebSocket 流式 ASR 转录本地 WAV 文件，返回完整文本。"""
    app_key, access_key = _get_asr_config()

    # 跳过 44 字节 WAV 文件头，发送原始 PCM
    with open(wav_path, "rb") as f:
        f.seek(44)
        pcm_data = f.read()

    headers = {
        "X-Api-App-Key": app_key,
        "X-Api-Access-Key": access_key,
        "X-Api-Resource-Id": _ASR_RESOURCE_ID,
        "X-Api-Connect-Id": uuid.uuid4().hex,
    }
    config_payload = json.dumps({
        "user": {"uid": "flowcut"},
        "audio": {"format": "pcm", "rate": 16000, "bits": 16, "channel": 1, "codec": "raw"},
        "request": {"model_name": "bigmodel", "enable_punc": True, "enable_itn": True},
    }).encode()

    last_text = ""

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(_ASR_WS_URL, headers=headers) as ws:
            # 发送初始配置帧 (FullClientRequest = 0x10, JSON序列化)
            await ws.send_bytes(_ws_frame(0x10, config_payload, json_serial=True))

            # 按实时节奏分块发送 PCM 音频（160ms/帧）
            offset = 0
            while offset < len(pcm_data):
                chunk = pcm_data[offset: offset + _ASR_CHUNK_BYTES]
                offset += _ASR_CHUNK_BYTES
                is_last = offset >= len(pcm_data)
                msg_type = 0x22 if is_last else 0x20
                await ws.send_bytes(_ws_frame(msg_type, chunk))
                await asyncio.sleep(0.16)

            # 接收结果直到 is_final 或连接关闭
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    raw = msg.data
                    start = raw.find(b"{")
                    if start == -1:
                        continue
                    try:
                        obj = json.loads(raw[start:])
                    except Exception:
                        continue
                    result = obj.get("result") or {}
                    text = result.get("text", "")
                    if text:
                        last_text = text
                    if result.get("is_final"):
                        break
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break

    return last_text


def _build_asr_request_payload() -> bytes:
    """构造 ByteDance bigmodel ASR 请求 JSON payload。

    必须开启 show_utterances=True，否则 response 不会返回 utterances[].words[]，
    导致拆镜段无法切出 copy 字段。
    """
    return json.dumps({
        "user": {"uid": "flowcut"},
        "audio": {
            "format": "pcm", "rate": 16000, "bits": 16,
            "channel": 1, "codec": "raw",
        },
        "request": {
            "model_name": "bigmodel",
            "enable_punc": True,
            "enable_itn": True,
            "show_utterances": True,
        },
    }).encode()


async def _call_asr_websocket_with_words(wav_path: str) -> tuple[str, list[dict]]:
    """通过 WebSocket 流式 ASR 转录本地 WAV 文件，返回 (完整文本, 词级时间戳列表)。

    返回的 words 每项形如：{"text": "你好", "start_time": 0, "end_time": 120}（毫秒）。
    若 ASR 不返回 utterances/words，words 列表为空。
    """
    app_key, access_key = _get_asr_config()

    with open(wav_path, "rb") as f:
        f.seek(44)
        pcm_data = f.read()

    headers = {
        "X-Api-App-Key": app_key,
        "X-Api-Access-Key": access_key,
        "X-Api-Resource-Id": _ASR_RESOURCE_ID,
        "X-Api-Connect-Id": uuid.uuid4().hex,
    }
    config_payload = _build_asr_request_payload()

    last_text = ""
    last_words: list[dict] = []

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(_ASR_WS_URL, headers=headers) as ws:
            await ws.send_bytes(_ws_frame(0x10, config_payload, json_serial=True))

            offset = 0
            while offset < len(pcm_data):
                chunk = pcm_data[offset: offset + _ASR_CHUNK_BYTES]
                offset += _ASR_CHUNK_BYTES
                is_last = offset >= len(pcm_data)
                msg_type = 0x22 if is_last else 0x20
                await ws.send_bytes(_ws_frame(msg_type, chunk))
                await asyncio.sleep(0.16)

            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    raw = msg.data
                    start = raw.find(b"{")
                    if start == -1:
                        continue
                    try:
                        obj = json.loads(raw[start:])
                    except Exception:
                        continue
                    result = obj.get("result") or {}
                    text = result.get("text", "")
                    if text:
                        last_text = text
                    utterances = result.get("utterances") or []
                    if utterances:
                        merged: list[dict] = []
                        for utt in utterances:
                            for w in (utt.get("words") or []):
                                merged.append(w)
                        if merged:
                            last_words = merged
                    if result.get("is_final"):
                        break
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break

    return last_text, last_words


def _slice_words_for_segment(
    words: list[dict], start_sec: float, end_sec: float,
) -> str:
    """从词级时间戳列表中截取落在 [start_sec, end_sec] 时间窗内的词，拼成字符串。"""
    if not words:
        return ""
    start_ms = start_sec * 1000.0
    end_ms = end_sec * 1000.0
    parts: list[str] = []
    for w in words:
        try:
            ws_ = float(w.get("start_time", 0))
            we_ = float(w.get("end_time", 0))
        except (TypeError, ValueError):
            continue
        if ws_ >= start_ms and we_ <= end_ms:
            parts.append(str(w.get("text", "")))
    return "".join(parts)


async def _download_file(url: str, dest: str) -> None:
    """流式下载文件到本地路径，避免整文件读入内存。"""
    async with aiohttp.ClientSession() as session:
        async with session.get(url, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=300, connect=30)) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                async for chunk in resp.content.iter_chunked(65536):
                    f.write(chunk)


def _extract_audio_ffmpeg(video_path: str, wav_path: str) -> None:
    """从视频中提取单声道 16kHz WAV 音频。"""
    result = subprocess.run(
        [
            "ffmpeg", "-i", video_path,
            "-vn", "-acodec", "pcm_s16le",
            "-ar", "16000", "-ac", "1",
            "-y", wav_path,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg extract failed: {result.stderr.strip()}")


def _probe_duration_seconds(video_path: str) -> float:
    """ffprobe 拿视频时长（秒）。失败返回 0.0，不抛异常以免阻塞主流程。"""
    duration = _probe_highlight_duration_seconds(video_path)
    if duration <= 0:
        logger.warning("video duration probe returned %.1fs for %s", duration, video_path)
    return duration


def _extract_cover_ffmpeg(video_path: str, cover_path: str, at_seconds: float = 0.5) -> None:
    """从视频中提取一帧作为封面图。"""
    result = subprocess.run(
        [
            "ffmpeg", "-ss", str(at_seconds), "-i", video_path,
            "-vframes", "1", "-q:v", "2",
            "-y", cover_path,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg cover extract failed: {result.stderr.strip()}")



async def _process_video(oss_key: str, oss_url: str) -> tuple[str, str, str, float]:
    """下载视频 → 并行抽封面 + Gemini 视觉分析 + ffprobe 拿时长。

    Gemini 单次调用同时返回 visual（画面描述）和 copy（口播逐字），
    不再走字节 ASR——少一份外部依赖、少一份失败面、整条耗时从 ~视频时长 缩到 ~Gemini RT。

    Returns:
        (transcript, cover_oss_url, description, duration_seconds)
        - transcript：各段 copy 字段拼接
        - description：各段 visual 字段拼接
        - duration_seconds：视频时长（秒），ffprobe 失败时为 0.0
        前三者均可能为空。
    """
    loop = asyncio.get_running_loop()

    video_path: str | None = None
    cover_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            video_path = tmp.name
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            cover_path = tmp.name

        if oss_key:
            await loop.run_in_executor(None, build_oss_client().download, oss_key, video_path)
        elif oss_url:
            await _download_file(oss_url, video_path)
        else:
            raise ValueError("No download URL available: oss_key and oss_url are both empty")

        # 并行：封面提取 + Gemini 拆镜 + ffprobe 时长
        def _cover_pipeline() -> str:
            _extract_cover_ffmpeg(video_path, cover_path)
            cover_oss_key = _make_cover_oss_key(oss_key)
            _upload_to_oss(cover_path, cover_oss_key)
            return cover_oss_key

        cover_future = loop.run_in_executor(None, _cover_pipeline)
        duration_future = loop.run_in_executor(None, _probe_duration_seconds, video_path)
        gemini_future = asyncio.create_task(analyze_video(video_path))

        # 封面失败不影响主流程
        cover_oss_url = ""
        try:
            cover_oss_url = await cover_future
        except Exception as cover_err:
            logger.warning("cover extract failed (non-fatal): %s", cover_err)

        description = ""
        transcript = ""
        try:
            segments = await gemini_future
            if segments:
                description = " ".join(
                    (seg.get("visual") or seg.get("content", "")) for seg in segments
                ).strip()
                transcript = " ".join(
                    seg.get("copy", "") for seg in segments if seg.get("copy")
                ).strip()
        except Exception as gem_err:
            logger.warning("Gemini analyze failed: %s", gem_err)

        # ffprobe 失败不影响主流程，回落 0.0 由调用方决定是否更新
        duration_seconds = 0.0
        try:
            duration_seconds = await duration_future
        except Exception as probe_err:
            logger.warning("ffprobe duration future failed (non-fatal): %s", probe_err)

        return transcript, cover_oss_url, description, duration_seconds
    finally:
        for p in (video_path, cover_path):
            if p and os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass


def _make_cover_oss_key(video_oss_key: str) -> str:
    """从视频 OSS key 推导封面 OSS key，同目录、同主名、.jpg 扩展名。"""
    p = Path(video_oss_key)
    return str(p.with_suffix(".jpg"))


def _upload_to_oss(local_path: str, oss_key: str) -> None:
    """将本地文件上传到 OSS。"""
    client = build_oss_client()
    client.upload(local_path, oss_key)


# ── 工厂函数 ────────────────────────────────────────────────────────


def make_material_process_executor(
    material_repo: MaterialRepository,
    embedding_service: EmbeddingService,
    vector_store: VectorStore,
) -> Callable[[TaskEnvelope], Awaitable[TaskExecutionResult]]:
    """素材 ASR + Gemini description + 缩略图 + 向量索引。

    VIDEO 素材：下载 → FFmpeg 提取音频 → 字节 ASR 转录 + Gemini 视觉描述 → 写入 → Qdrant。
    AUDIO / IMAGE 素材：直接标记 READY（无 description，不写入向量索引）。
    """

    async def execute(task: TaskEnvelope) -> TaskExecutionResult:
        payload = task.payload
        material_id = int(payload["material_id"])
        oss_key = str(payload.get("oss_key", ""))
        oss_url = str(payload.get("oss_url", ""))
        ext = Path(oss_key).suffix.lower()

        try:
            if ext in VIDEO_EXTENSIONS:
                transcript, cover_url, description, duration_seconds = await asyncio.wait_for(
                    _process_video(oss_key, oss_url),
                    timeout=_MATERIAL_PROCESS_TIMEOUT_S,
                )
                await material_repo.update_status(
                    material_id, "READY",
                    transcript=transcript,
                    description=description or None,
                    thumbnail_url=cover_url or None,
                    duration=duration_seconds if duration_seconds > 0 else None,
                )

                # Embed + Qdrant upsert
                if description:
                    try:
                        desc_vec = await embedding_service.embed(description)
                        transcript_vec = (
                            await embedding_service.embed(transcript)
                            if transcript else None
                        )
                        material = await material_repo.get(material_id)
                        payload = {
                            "tenant_key": material["tenant_key"],
                            "product": material.get("product"),
                            "scene_role": material.get("scene_role"),
                            "status": "READY",
                            "has_transcript": bool(transcript),
                        }
                        await vector_store.upsert(
                            material_id, desc_vec, transcript_vec, payload,
                        )
                        await material_repo.mark_vector_indexed(material_id)
                    except Exception as vec_err:
                        logger.warning("vector upsert failed for material %d: %s",
                                       material_id, vec_err)

                return TaskExecutionResult.succeeded(
                    summary=(
                        f"material_id={material_id} transcript_len={len(transcript)}"
                        f" description_len={len(description)}"
                    ),
                    details={"material_id": material_id, "status": "READY",
                             "has_description": bool(description)},
                )

            if ext in IMAGE_EXTENSIONS:
                # 图片自身就是缩略图 / 预览图；填进去 list 路由会自动转 presigned URL
                await material_repo.update_status(
                    material_id, "READY",
                    thumbnail_url=oss_key,
                    preview_url=oss_key,
                )
                return TaskExecutionResult.succeeded(
                    summary=f"material_id={material_id} marked READY (image)",
                    details={"material_id": material_id, "status": "READY"},
                )

            if ext in AUDIO_EXTENSIONS:
                await material_repo.update_status(material_id, "READY")
                return TaskExecutionResult.succeeded(
                    summary=f"material_id={material_id} marked READY (audio)",
                    details={"material_id": material_id, "status": "READY"},
                )

            # 不支持的文件类型
            error_msg = f"Unsupported file extension: {ext}"
            await material_repo.update_status(material_id, "FAILED", transcript=error_msg)
            return TaskExecutionResult.failed(
                error=error_msg,
                summary=f"material_id={material_id} unsupported extension",
            )

        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}"
            try:
                await material_repo.update_status(
                    material_id, "FAILED", transcript=error_text[:2000],
                )
            except Exception:
                pass
            return TaskExecutionResult.failed(error=error_text)

    return execute


def make_scene_decompose_executor(
    material_repo: MaterialRepository,
    ref_video_repo: ReferenceVideoRepository,
    embedding_service: EmbeddingService,
    vector_store: VectorStore,
    script_repo: ScriptRepository,
    creative_repo=None,
) -> Callable[[TaskEnvelope], Awaitable[TaskExecutionResult]]:
    """爆款视频拆镜：Gemini 语义分段 + PySceneDetect 时间修正 → fc_reference_video → 子片段 → Qdrant。

    同时产出：
    - 抽音轨 MP3 上传 OSS，回填 fc_reference_video.audio_oss_key
    - ASR 词级时间戳按场景切片，写入 scene_data[i].copy
    - 落库 fc_script (source=decomposed) 并回填 fc_reference_video.script_id
    """

    async def execute(task: TaskEnvelope) -> TaskExecutionResult:
        import time as _time

        payload = task.payload
        ref_video_id = int(payload["ref_video_id"])
        oss_key = str(payload.get("oss_key", ""))
        oss_url = str(payload.get("oss_url", ""))
        tenant_key = str(payload.get("tenant_key", ""))
        workflow_type = str(payload.get("workflow_type") or "reference_video")
        continuation_type = str(payload.get("continuation_type") or "unspecified")
        connector_ref_video_id = payload.get("connector_ref_video_id")
        creative_id = payload.get("creative_id")
        source_asset_id = payload.get("source_asset_id")
        connector_asset_id = payload.get("connector_asset_id")

        ref_video = await ref_video_repo.get(ref_video_id)
        if ref_video is None:
            return TaskExecutionResult.failed(
                error=f"ref_video_id={ref_video_id} not found",
                summary=f"ref_video_id={ref_video_id} not found",
            )

        # 上层（POST /upload 或 /decompose）已预建 fc_script(PROCESSING) 并回填
        # ref_video.script_id。executor 走 UPDATE 路径。
        # 老数据兼容：若 script_id 为空，下文成功路径会 fallback create。
        prebuilt_script_id: int | None = ref_video.get("script_id")

        video_path: str | None = None
        connector_video_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                video_path = tmp.name

            loop = asyncio.get_running_loop()
            if oss_key:
                await loop.run_in_executor(None, build_oss_client().download, oss_key, video_path)
            elif oss_url:
                await _download_file(oss_url, video_path)
            else:
                raise ValueError("No download URL: oss_key and oss_url are both empty")

            # Verify downloaded file is a valid video
            file_size = os.path.getsize(video_path)
            if file_size == 0:
                raise RuntimeError(f"Downloaded file is empty: oss_key={oss_key}")
            logger.info("scene_decompose: downloaded video path=%s size=%d",
                        video_path, file_size)

            if workflow_type == "highlight_extract" and connector_ref_video_id:
                connector_ref_video = await ref_video_repo.get(int(connector_ref_video_id))
                if connector_ref_video is None:
                    raise ValueError(f"connector_ref_video_id={connector_ref_video_id} not found")
                with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                    connector_video_path = tmp.name
                connector_oss_key = str(connector_ref_video.get("oss_key") or "")
                connector_oss_url = str(connector_ref_video.get("oss_url") or "")
                if connector_oss_key:
                    await loop.run_in_executor(
                        None,
                        build_oss_client().download,
                        connector_oss_key,
                        connector_video_path,
                    )
                elif connector_oss_url:
                    await _download_file(connector_oss_url, connector_video_path)
                else:
                    raise ValueError("connector video has no oss_key or oss_url")
                connector_size = os.path.getsize(connector_video_path)
                if connector_size == 0:
                    raise RuntimeError(
                        f"Downloaded connector video is empty: ref_video_id={connector_ref_video_id}"
                    )
                logger.info(
                    "scene_decompose: downloaded connector video path=%s size=%d",
                    connector_video_path,
                    connector_size,
                )

            cuts_task = asyncio.create_task(detect_scene_cuts(video_path))
            if workflow_type == "highlight_extract":
                highlight_task = asyncio.create_task(
                    analyze_highlight_video(
                        video_path,
                        connector_video_path=connector_video_path,
                        continuation_type=continuation_type,
                    )
                )
                highlight_analysis, cuts = await asyncio.gather(highlight_task, cuts_task)
                segments = list(highlight_analysis.get("segments") or [])
            else:
                gemini_task = asyncio.create_task(analyze_video(video_path))
                segments, cuts = await asyncio.gather(gemini_task, cuts_task)

            if not segments:
                if prebuilt_script_id is not None:
                    try:
                        await script_repo.update_status(prebuilt_script_id, "FAILED")
                    except Exception:
                        pass
                await ref_video_repo.update_status(ref_video_id, "FAILED")
                return TaskExecutionResult.failed(
                    error="Gemini returned empty segments",
                    summary=f"ref_video_id={ref_video_id} decompose empty",
                )

            aligned = align_timestamps(segments, cuts)

            # === 抽音轨 MP3 并上传 OSS（导出 zip 仍需要 audio.mp3；失败不阻塞主流程） ===
            audio_mp3_path = video_path + ".audio.mp3"
            try:
                try:
                    subprocess.run(
                        ["ffmpeg", "-y", "-i", video_path,
                         "-vn", "-acodec", "libmp3lame", "-q:a", "4",
                         audio_mp3_path],
                        check=True, capture_output=True, timeout=180,
                    )
                    audio_key = f"reference_videos/{tenant_key}/{ref_video_id}/audio.mp3"
                    await loop.run_in_executor(
                        None, build_oss_client().upload, audio_mp3_path, audio_key,
                    )
                    await ref_video_repo.set_audio(ref_video_id, audio_key)
                except Exception as exc:
                    logger.warning("scene_decompose: 抽音轨/上传失败: %s", exc)
            finally:
                if os.path.exists(audio_mp3_path):
                    try:
                        os.unlink(audio_mp3_path)
                    except OSError:
                        pass

            # === 产出 fc_script（visual + copy 均来自 Gemini 多模态输出）===
            script_segments = [
                {
                    **seg,
                    "idx": i,
                    "start_time": float(seg.get("start_time", 0)),
                    "end_time": float(seg.get("end_time", 0)),
                    "visual": seg.get("visual") or seg.get("content", ""),
                    "copy": seg.get("copy", ""),
                    "category": seg.get("category", "产品展示"),
                }
                for i, seg in enumerate(aligned)
            ]
            if prebuilt_script_id is not None:
                # 主路径：UPDATE 预建的 PROCESSING 脚本 → DRAFT
                await script_repo.update_segments_and_status(
                    prebuilt_script_id, script_segments, "DRAFT",
                )
                final_script_id = prebuilt_script_id
            else:
                # 兼容老数据：上层未预建 script，executor 回退到 INSERT
                logger.warning(
                    "scene_decompose: ref_video=%d has no prebuilt script_id, "
                    "falling back to INSERT", ref_video_id,
                )
                script_record = await script_repo.create(
                    tenant_key=tenant_key,
                    source="decomposed",
                    reference_video_id=ref_video_id,
                    product=ref_video.get("product"),
                    segments=script_segments,
                )
                final_script_id = script_record["id"]
                await ref_video_repo.set_script_id(ref_video_id, final_script_id)

            # 不再写 scene_data_json；段落数据以 fc_script.segments_json 为准
            await ref_video_repo.update_status(ref_video_id, "READY")

            if (
                workflow_type == "highlight_extract"
                and creative_repo is not None
                and creative_id is not None
            ):
                best_segment = _pick_highlight_segment(script_segments)
                if best_segment is not None:
                    highlight_start = float(best_segment.get("start_time", 0))
                    highlight_end = float(best_segment.get("end_time", 0))
                    compose_strategy = (
                        "original_starts_with_highlight_no_duplicate"
                        if continuation_type == "original" and highlight_start <= 0.35
                        else "frontload_highlight_then_followup"
                    )
                    compose_plan = {
                        "workflow_type": workflow_type,
                        "continuation_type": continuation_type,
                        "source_asset_id": source_asset_id,
                        "connector_asset_id": connector_asset_id,
                        "ref_video_id": ref_video_id,
                        "script_id": final_script_id,
                        "stage": "analysis_ready",
                        "next_step": "compose_video_pending",
                        "compose_strategy": compose_strategy,
                    }
                    highlight_reason = {
                        "idx": best_segment.get("idx"),
                        "candidate_use": best_segment.get("candidate_use"),
                        "hook_strength": best_segment.get("hook_strength"),
                        "ending_connectability": best_segment.get("ending_connectability"),
                        "context_dependency": best_segment.get("context_dependency"),
                        "continuity_risk": best_segment.get("continuity_risk"),
                        "ending_state": best_segment.get("ending_state"),
                        "open_question": best_segment.get("open_question"),
                        "bridge_text": best_segment.get("bridge_text"),
                        "reason": best_segment.get("reason"),
                        "followup_fit": best_segment.get("followup_fit"),
                        "frontload_recommendation": (
                            "原片开头已是高光，生成高光+原片时不重复拼接，直接输出原片。"
                            if compose_strategy == "original_starts_with_highlight_no_duplicate"
                            else "该高光适合前置，再接回原片或指定衔接素材。"
                        ),
                    }
                    await creative_repo.mark_highlight_ready(
                        int(creative_id),
                        highlight_start=highlight_start,
                        highlight_end=highlight_end,
                        highlight_reason=highlight_reason,
                        compose_plan=compose_plan,
                    )
                else:
                    await creative_repo.mark_highlight_ready(
                        int(creative_id),
                        highlight_start=None,
                        highlight_end=None,
                        highlight_reason={"reason": "未找到可前置的高光候选段"},
                        compose_plan={
                            "workflow_type": workflow_type,
                            "continuation_type": continuation_type,
                            "source_asset_id": source_asset_id,
                            "connector_asset_id": connector_asset_id,
                            "ref_video_id": ref_video_id,
                            "script_id": final_script_id,
                            "stage": "analysis_ready_no_candidate",
                        },
                    )

            logger.info(
                "scene_decompose done: ref_video=%d segments=%d script=%d READY",
                ref_video_id, len(aligned), final_script_id,
            )

            return TaskExecutionResult.succeeded(
                summary=f"ref_video_id={ref_video_id} segments={len(aligned)} script={final_script_id}",
                details={
                    "ref_video_id": ref_video_id,
                    "segment_count": len(aligned),
                    "script_id": final_script_id,
                },
            )

        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}"
            # 失败路径：把预建脚本也标记为 FAILED（如果有）
            if prebuilt_script_id is not None:
                try:
                    await script_repo.update_status(prebuilt_script_id, "FAILED")
                except Exception as inner:
                    logger.warning(
                        "scene_decompose: 更新 script.status=FAILED 失败 "
                        "script_id=%d err=%s", prebuilt_script_id, inner,
                    )
            try:
                await ref_video_repo.update_status(ref_video_id, "FAILED")
            except Exception:
                pass
            if creative_repo is not None and creative_id is not None:
                try:
                    await creative_repo.mark_highlight_failed(int(creative_id), error=error_text)
                except Exception:
                    pass
            return TaskExecutionResult.failed(error=error_text)

        finally:
            if video_path and os.path.exists(video_path):
                try:
                    os.unlink(video_path)
                except OSError:
                    pass
            if connector_video_path and os.path.exists(connector_video_path):
                try:
                    os.unlink(connector_video_path)
                except OSError:
                    pass

    return execute


def _score_highlight_segment(seg: dict) -> float:
    def num(key: str) -> float:
        try:
            return float(seg.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    candidate_bonus = {
        "primary_hook": 100.0,
        "secondary_hook": 60.0,
        "context_only": 0.0,
        "reject": -100.0,
    }.get(str(seg.get("candidate_use") or ""), 0.0)
    return (
        candidate_bonus
        + num("hook_strength") * 10
        + num("ending_connectability") * 6
        - num("context_dependency") * 4
        - num("continuity_risk") * 5
    )


def _pick_highlight_segment(segments: list[dict]) -> dict | None:
    candidates = [
        seg
        for seg in segments
        if str(seg.get("candidate_use") or "") in {"primary_hook", "secondary_hook"}
    ]
    if not candidates:
        candidates = [seg for seg in segments if float(seg.get("hook_strength") or 0) >= 7]
    if not candidates:
        return None
    return max(candidates, key=_score_highlight_segment)


def _find_fallback_pick(head_shots: list[dict], content_start: float) -> dict | None:
    """当所有 Gemini 推荐起点都被片头校验过滤后，找一个兜底起点。

    策略：在 content_start 之后找第一个有对白（>= MIN_DIALOGUE_CHARS 字符）
    且 hook_strength 尽量高的段。
    """
    from Flowcut.services.clip_planner import MIN_DIALOGUE_CHARS
    best: dict | None = None
    best_score = -1.0
    for i, shot in enumerate(head_shots):
        start = float(shot.get("start_time", 0.0))
        if start < content_start:
            continue
        copy_text = str(shot.get("copy", "")).strip()
        if len(copy_text) < MIN_DIALOGUE_CHARS:
            continue
        # 综合评分：对白长度 + hook_strength（若有）
        score = len(copy_text) * 0.01 + float(shot.get("hook_strength") or 0.0)
        if score > best_score:
            best_score = score
            best = {"idx": i, "hook_strength": float(shot.get("hook_strength") or 5.0),
                    "reason": f"兜底：content_start={content_start:.1f}s 后第一个有效对白段"}
    return best


async def _decompose_single_episode(
    norm_path: str,
    ep_no: int,
    label: str,
    _gemini_retry_fn,
    loop,
) -> list[dict]:
    """对单集独立拆镜：Gemini + PySceneDetect 并行 + align 吸附。

    每集独立调用 Gemini，视频只有原来的 1/3 大小，不会触发 413。
    Stage A 中串行调用此函数，每完成一集上报一次子进度。
    """
    cuts_task = asyncio.create_task(detect_scene_cuts(norm_path))

    async def _analyze_nonempty() -> list[dict]:
        raw = await analyze_video(norm_path, timeout_s=_GEMINI_HARD_TIMEOUT_S)
        if not raw:
            raise EmptyDecomposeResultError(
                f"StageA-analyze-{label}: Gemini returned empty segments"
            )
        return raw

    try:
        raw_shots = await _gemini_retry_fn(_analyze_nonempty, f"StageA-analyze-{label}")
    except Exception:
        if not cuts_task.done():
            cuts_task.cancel()
        try:
            await cuts_task
        except BaseException:
            pass
        raise
    phys_cuts = await cuts_task
    shots = align_timestamps(list(raw_shots), phys_cuts) if raw_shots else []
    for s in shots:
        s["episode_no"] = ep_no
    return shots


def _merge_multi_episode_shots(
    ep_shots_list: list[list[dict]],
    offsets: list[tuple[int, float]],
) -> list[dict]:
    """将多集各自拆镜的 segment 列表合并为一份全局列表。

    每段的 start_time/end_time 从集内秒换算为全局累计秒，
    产出与旧合并模式完全兼容的 head_shots，Stage B/C 无需任何改动。
    """
    merged: list[dict] = []
    for (ep_no, offset), shots in zip(offsets, ep_shots_list):
        for seg in shots:
            merged.append({
                **seg,
                "start_time": float(seg.get("start_time", 0.0)) + offset,
                "end_time": float(seg.get("end_time", 0.0)) + offset,
            })
    return merged


def make_highlight_plan_executor(
    *,
    runtime,
    highlight_asset_repo,
    creative_repo,
    oss_client,
    task_state_store=None,
) -> Callable[[TaskEnvelope], Awaitable[TaskExecutionResult]]:
    """跨集高光切片规划（两阶段拆镜）：

    Stage A 合并前3集 → 轻量整体拆镜（找起点用，分镜可粗）。
    Stage B Gemini 在分镜列表上**专门挑**高光起点（纯文本判断，不上传视频）。
    Stage C 对每个起点向后取约 1 分钟连续原片 → **重新细拆** → 在分镜/句末边界上选
            逻辑收尾切点，再按真实集时长换算成逐集裁切计划。
    起点与收尾都落在真实镜头切点上，不再恒定硬切 60s。

    阶段进度通过 task_state_store.update_progress() 实时上报，
    前端可轮询 /flowcut/tasks/{task_id} 获取 details.progress 展示分阶段进度条。
    """

    async def _ensure_episode(asset: dict, cache: dict, tmp_dir: str, loop):
        """下载并归一化一集，返回 (norm_path, duration)；按 asset_id 缓存复用。

        下载和 ffmpeg 归一化均放入线程池执行，避免阻塞事件循环。
        并行 asyncio.gather 调用时 3 集同时下载+转码，而非串行等待。
        OSS 下载带 2 次重试（间歇性网络超时可恢复）。
        """
        aid = int(asset["id"])
        if aid in cache:
            return cache[aid]
        raw = os.path.join(tmp_dir, f"ep_{aid}.mp4")

        # OSS 下载：间歇性网络超时重试 2 次（5s / 10s）
        for attempt, delay in enumerate((0, 5.0, 10.0)):
            try:
                if delay > 0:
                    logger.warning("OSS download retry %d/2 for asset %d", attempt, aid)
                    await asyncio.sleep(delay)
                await loop.run_in_executor(
                    None, oss_client.download,
                    str(asset.get("oss_key") or asset.get("oss_url") or ""), raw,
                )
                break
            except Exception:
                if attempt == 2:
                    raise
        norm = os.path.join(tmp_dir, f"ep_{aid}_norm.mp4")
        # ffmpeg normalize 必须放入线程池：subprocess.run 会阻塞调用线程，
        # 直接在 async 函数里调用会卡死事件循环，导致并行下载失效。
        await loop.run_in_executor(None, _ffmpeg_normalize_clip, raw, norm)
        dur = await loop.run_in_executor(None, _probe_duration_seconds, norm)
        cache[aid] = (norm, float(dur))
        return cache[aid]

    async def execute(task: TaskEnvelope) -> TaskExecutionResult:
        payload = task.payload
        task_id = task.task_id
        tenant_key = str(payload.get("tenant_key") or task.tenant_key or "flowcut")
        session_key = str(payload.get("session_key") or "highlight_plan")
        num_candidates = int(payload.get("num_candidates") or DEFAULT_CANDIDATES)
        batch_id = str(payload.get("batch_id") or uuid.uuid4().hex)
        connector_asset_id_raw = payload.get("connector_asset_id")
        connector_asset_id: int | None = int(connector_asset_id_raw) if connector_asset_id_raw is not None else None

        async def _report(stage: str, label: str, pct: int, extra: dict | None = None):
            if task_state_store is None:
                return
            try:
                await task_state_store.update_progress(task_id, {
                    "stage": stage,
                    "stage_label": label,
                    "progress_pct": pct,
                    **(extra or {}),
                })
            except Exception:
                pass

        # Gemini 瞬态错误重试：503 / RemoteProtocolError 为 Google 服务端瞬时过载，
        # 指数退避重试 3 次通常可恢复。视频重新上传的开销可接受（~10-30s）。
        _GEMINI_RETRY_BACKOFF = (5.0, 15.0, 30.0)
        _GEMINI_RETRYABLE = ("503", "UNAVAILABLE", "RemoteProtocolError",
                             "Server disconnected", "ConnectionError",
                             "ConnectError", "SSL", "UNEXPECTED_EOF",
                             "ReadError", "ReadTimeout", "TimeoutException",
                             "Connection reset", "forcibly closed",
                             "WinError 10054", "WinError 10053",
                             "远程主机强迫关闭", "软件中止",
                             "429", "RESOURCE_EXHAUSTED", "quota")

        async def _gemini_retry(factory, label: str):
            """用指数退避重试 Gemini 调用（最多 3 次），仅对瞬态错误重试。"""
            last_err = None
            for attempt, delay in enumerate(_GEMINI_RETRY_BACKOFF):
                try:
                    return await factory()
                except Exception as exc:
                    last_err = exc
                    msg = str(exc)
                    retryable = (
                        isinstance(exc, EmptyDecomposeResultError)
                        or any(kw in msg for kw in _GEMINI_RETRYABLE)
                    )
                    if not retryable:
                        raise  # 非瞬态错误（401/403/404）不重试
                    if attempt < len(_GEMINI_RETRY_BACKOFF) - 1:
                        logger.warning(
                            "highlight_plan: %s attempt %d/3 failed (retryable), "
                            "retrying in %.0fs: %s",
                            label, attempt + 1, delay, msg[:200],
                        )
                        await asyncio.sleep(delay)
            raise last_err  # 3 次全失败

        # 未指定数字人时自动选库中最新的 READY 数字人
        if connector_asset_id is None:
            dh_rows = await highlight_asset_repo.list_by_tenant(
                tenant_key, asset_type="digital_human_connector", limit=20,
            )
            ready_dh = [r for r in dh_rows if r.get("status") == "READY"]
            if ready_dh:
                connector_asset_id = int(ready_dh[0]["id"])

        # 支持多剧名：drama_names 列表优先，单 drama_name 向后兼容
        raw_names: list = list(payload.get("drama_names") or [])
        single = str(payload.get("drama_name") or "").strip()
        if not raw_names and single:
            raw_names = [single]
        drama_names_list = [str(d).strip() for d in raw_names if d and str(d).strip()]
        if not drama_names_list:
            return TaskExecutionResult.failed(error="highlight_plan: drama_name / drama_names 均为空")

        await _report("starting", "开始规划", 0, {"drama_count": len(drama_names_list)})

        async def _plan_one_drama(drama_name_: str) -> dict:
            """为一个剧名跑完整 Stage A→B→C，返回 {drama_name, created, error?}。"""
            rows = await highlight_asset_repo.list_by_tenant(
                tenant_key, asset_type="episode_source", drama_name=drama_name_, limit=500,
            )
            episodes = sorted(rows, key=lambda r: int(r.get("episode_no") or 0))
            if not episodes:
                # 精确剧名落空 → 回退按子串模糊匹配（剧名常差「被」等前缀）
                all_rows = await highlight_asset_repo.list_by_tenant(
                    tenant_key, asset_type="episode_source", limit=500,
                )
                episodes = match_drama_episodes(all_rows, drama_name_)
            if not episodes:
                return {"drama_name": drama_name_, "created": [],
                        "error": f"没有在原片库找到「{drama_name_}」"}

            # 按用户指定的集数范围过滤
            start_ep = payload.get("start_episode") or 1
            end_ep = payload.get("end_episode")
            if start_ep > 1 or end_ep is not None:
                episodes = [
                    e for e in episodes
                    if int(e.get("episode_no") or 0) >= start_ep
                    and (end_ep is None or int(e.get("episode_no") or 0) <= end_ep)
                ]
            if not episodes:
                range_desc = f"第{start_ep}集" + (f"到第{end_ep}集" if end_ep else "之后")
                return {"drama_name": drama_name_, "created": [],
                        "error": f"「{drama_name_}」在{range_desc}范围内没有剧集"}

            ep_index = {int(a["episode_no"] or 0): a for a in episodes}
            tmp_dir = tempfile.mkdtemp(prefix=f"flowcut_highlight_plan_{batch_id}_")
            norm_cache: dict[int, tuple] = {}
            timings: dict[str, object] = {}
            try:
                loop = asyncio.get_running_loop()

                # —— Stage A：逐集串行拆镜 ——
                # 旧方案：3 集 ffmpeg concat → 1 次 Gemini 大视频 → 413 Request Entity Too Large
                # 新方案：每集独立 Gemini + PySceneDetect，串行执行。
                #   单集视频 2-10min，远低于 Gemini Files API 大小上限，不会触发 413。
                #   拆完后纯文本合并 segment 列表（ms 级），Stage B/C 数据结构完全不变。
                t_stage_a = time.perf_counter()
                t_dl = time.perf_counter()
                head = episodes[:START_SEARCH_EPISODES]
                head_episode_nos = [int(a.get("episode_no") or 0) for a in head]
                if head_episode_nos:
                    if head_episode_nos[0] == head_episode_nos[-1]:
                        head_desc = f"第{head_episode_nos[0]}集"
                    else:
                        head_desc = (
                            f"第{head_episode_nos[0]}-{head_episode_nos[-1]}集"
                            f"（共{len(head_episode_nos)}集）"
                        )
                else:
                    head_desc = "选定范围内剧集"
                await _report("stage_a_download", "下载视频中", 5,
                              {"drama": drama_name_, "episode_count": len(head)})
                # 并行下载+归一化：3 集同时处理，CPU/IO 重叠
                norm_results = await asyncio.gather(
                    *[_ensure_episode(asset, norm_cache, tmp_dir, loop) for asset in head]
                )
                offsets: list[tuple[int, float]] = []
                durations: dict[int, float] = {}
                cum = 0.0
                for asset, (_, dur) in zip(head, norm_results):
                    ep_no = int(asset["episode_no"] or 0)
                    offsets.append((ep_no, cum))
                    durations[ep_no] = dur
                    cum += dur

                timings["stage_a_download_ffmpeg_s"] = round(time.perf_counter() - t_dl, 3)

                # 串行逐集拆镜：Ep1 → Ep2 → Ep3
                # 每集独立 Gemini 调用 + PySceneDetect + align
                t_gemini_a = time.perf_counter()
                ordered_shots: list[list[dict]] = []
                decompose_diagnostics: list[dict] = []
                total_eps = len(head)
                for i, (asset, (norm, _)) in enumerate(zip(head, norm_results)):
                    ep_no = int(asset["episode_no"] or 0)
                    label = f"{drama_name_}_ep{ep_no}"
                    sub_pct = 8 + int((i + 1) / total_eps * 7)  # 8%→10%→13%→15%
                    await _report("stage_a_gemini",
                                  f"AI 分析视频中 ({i + 1}/{total_eps})",
                                  sub_pct, {"drama": drama_name_, "episode": ep_no})
                    try:
                        shots = await _decompose_single_episode(
                            norm, ep_no, label, _gemini_retry, loop,
                        )
                    except Exception as exc:
                        error_text = f"{type(exc).__name__}: {exc}"
                        logger.exception(
                            "highlight_plan: StageA ep=%d decompose failed", ep_no,
                        )
                        shots = []
                        decompose_diagnostics.append({
                            "episode_no": ep_no,
                            "asset_id": asset.get("id"),
                            "oss_key": asset.get("oss_key") or asset.get("oss_url") or "",
                            "error": error_text,
                        })
                    ordered_shots.append(shots)
                    logger.info(
                        "highlight_plan: drama=%s ep=%d decompose done shots=%d",
                        drama_name_, ep_no, len(shots),
                    )

                timings["stage_a_gemini_analyze_s"] = round(
                    time.perf_counter() - t_gemini_a, 3)
                # 合并：集内秒 → 全局累计秒
                head_shots = _merge_multi_episode_shots(ordered_shots, offsets)

                if not head_shots:
                    if decompose_diagnostics:
                        reason_tail = "；".join(
                            f"ep{d.get('episode_no')}: {d.get('error')}"
                            for d in decompose_diagnostics
                        )
                    else:
                        reason_tail = "Gemini 返回空 segments，未生成可用分镜"
                    return {"drama_name": drama_name_, "created": [],
                            "decompose_diagnostics": decompose_diagnostics,
                            "error": (
                                f"「{drama_name_}」{head_desc}拆镜为空，无法选起点；"
                                f"{reason_tail}"
                            )}

                # ── 片头检测：计算 content_start ──
                content_start = detect_content_start(head_shots)
                logger.info(
                    "highlight_plan: drama=%s content_start=%.1fs total_shots=%d mode=per_episode",
                    drama_name_, content_start, len(head_shots),
                )

                timings["stage_a_total_s"] = round(time.perf_counter() - t_stage_a, 3)
                await _report("stage_a_done", "逐集拆镜完成", 25,
                              {"drama": drama_name_, "stage_a_s": timings["stage_a_total_s"]})

                # —— Stage B：Gemini 在分镜列表上专门挑起点（纯文本判断）——
                # 多要 2 倍，让去重后仍能凑够 num_candidates
                t_stage_b = time.perf_counter()
                picks = await _gemini_retry(
                    lambda: select_start_shots(head_shots, top_n=num_candidates * 2,
                                               timeout_s=_GEMINI_HARD_TIMEOUT_S),
                    f"StageB-select-{drama_name_}",
                )
                timings["stage_b_gemini_select_s"] = round(time.perf_counter() - t_stage_b, 3)
                await _report("stage_b_done", "已选出高光起点", 35,
                              {"drama": drama_name_, "stage_b_s": timings["stage_b_gemini_select_s"]})

                # ── 片头校验 & 对白有效性检查 ──
                validated_picks: list[dict] = []
                skipped_reasons: list[str] = []
                for p in picks:
                    shot = head_shots[p["idx"]]
                    validation = validate_start_candidate(shot, head_shots, content_start)
                    if validation.is_valid:
                        validated_picks.append(p)
                    else:
                        skipped_reasons.append(
                            f"idx={p['idx']} start={float(shot.get('start_time', 0)):.1f}s: "
                            f"{validation.reason}"
                        )
                        logger.warning(
                            "highlight_plan: 跳过无效高光起点 drama=%s idx=%d "
                            "start=%.1fs reason=%s suggested=%.1f",
                            drama_name_, p["idx"],
                            float(shot.get("start_time", 0)),
                            validation.reason,
                            validation.suggested_start or -1.0,
                        )
                if skipped_reasons:
                    logger.info(
                        "highlight_plan: drama=%s 跳过了 %d/%d 个 Gemini 推荐的起点: %s",
                        drama_name_, len(skipped_reasons), len(picks),
                        "; ".join(skipped_reasons),
                    )

                # 如果所有 pick 都被过滤，尝试用 content_start 后的第一个有效段作为兜底
                if not validated_picks and content_start > 0:
                    fallback = _find_fallback_pick(head_shots, content_start)
                    if fallback is not None:
                        validated_picks.append(fallback)
                        logger.info(
                            "highlight_plan: drama=%s 所有 Gemini 推荐均被过滤，"
                            "使用兜底起点 idx=%d start=%.1fs",
                            drama_name_, fallback["idx"],
                            float(head_shots[fallback["idx"]].get("start_time", 0)),
                        )

                candidates: list[StartCandidate] = []
                seen: list[float] = []
                for p in validated_picks:
                    shot = head_shots[p["idx"]]
                    g = float(shot.get("start_time") or 0.0)
                    if any(abs(g - x) < DEDUP_GAP_S for x in seen):
                        continue
                    loc = locate(g, offsets, durations)
                    if loc is None:
                        continue
                    ep_no, local = loc
                    seen.append(g)
                    candidates.append(StartCandidate(
                        episode_no=ep_no, local_start=local, global_start=g,
                        hook_strength=float(p.get("hook_strength") or 0.0),
                        reason=str(p.get("reason") or ""),
                    ))
                    if len(candidates) >= num_candidates:
                        break
                if not candidates:
                    return {"drama_name": drama_name_, "created": [],
                            "error": f"「{drama_name_}」未选出可用高光起点（已过滤 {len(skipped_reasons)} 个片头/无对白候选）"}

                await _report("stage_c", "逐候选细拆规划中", 45,
                              {"drama": drama_name_, "candidate_count": len(candidates)})
                # —— Stage C 预下载：把所有候选可能用到的集提前 normalize 好，
                #    避免并行协程同时下载同一集导致缓存竞争。
                #    needed_ep_nos 已去重，asyncio.gather 并行预下载。 ——
                t_stage_c = time.perf_counter()
                target_len = WINDOW[1] + _HIGHLIGHT_SPAN_PAD_S
                needed_ep_nos: set[int] = set()
                for cand in candidates:
                    ep_no = cand.episode_no
                    for _ in range(MAX_FORWARD_EPISODES):
                        if ep_no not in ep_index:
                            break
                        needed_ep_nos.add(ep_no)
                        ep_no += 1
                await asyncio.gather(
                    *[_ensure_episode(ep_index[ep_no], norm_cache, tmp_dir, loop)
                      for ep_no in sorted(needed_ep_nos)],
                )

                # —— Stage C 并行：每个起点独立细拆 + 建 creative ——
                # cand_idx 作为文件名唯一前缀，避免多个候选 episode_no 相同时互相覆盖
                # 合并的第一集 episode_no，用于判断 content_start 是否适用于当前候选
                _merge_first_ep = int(head[0].get("episode_no") or 0) if head else 0

                async def _process_candidate(cand: StartCandidate, cand_idx: int) -> dict | None:
                    t_cand = time.perf_counter()
                    uid = f"c{cand_idx}"

                    # ── 上下文前扩展：防止对白开头被截断 ──
                    # content_start 是全局时间（合并视频中），仅当候选在合并第一集时才适用
                    _local_content_start = (
                        content_start if cand.episode_no == _merge_first_ep else 0.0
                    )
                    expanded_start, expand_log = expand_start_with_context(
                        cand.local_start, _local_content_start,
                    )
                    if expand_log:
                        logger.info(
                            "highlight_plan: drama=%s candidate=%d %s",
                            drama_name_, cand_idx, expand_log,
                        )

                    ep_refs: list[EpisodeRef] = []
                    seg_specs: list[tuple[str, float, float]] = []
                    acc = 0.0
                    ep_no = cand.episode_no
                    steps = 0
                    while ep_no in ep_index and steps < MAX_FORWARD_EPISODES:
                        asset = ep_index[ep_no]
                        norm, dur = await _ensure_episode(asset, norm_cache, tmp_dir, loop)
                        base = expanded_start if ep_no == cand.episode_no else 0.0
                        avail = dur - base
                        if avail <= 0:
                            break
                        take = min(avail, target_len - acc)
                        seg_specs.append((norm, base, base + take))
                        ep_refs.append(EpisodeRef(
                            asset_id=int(asset["id"]), episode_no=ep_no,
                            oss_key=str(asset.get("oss_key") or asset.get("oss_url") or ""),
                            duration=dur,
                        ))
                        acc += take
                        steps += 1
                        if acc >= target_len:
                            break
                        ep_no += 1

                    capacity = acc
                    if capacity < WINDOW[0]:
                        logger.info("highlight_plan: 起点 ep=%d 真实可用 %.1fs 不足 %ss，跳过",
                                    cand.episode_no, capacity, WINDOW[0])
                        return None

                    # 拼出 span → 重新细拆 + PySceneDetect 物理切点（并行）
                    span_cut_paths: list[str] = []
                    for i, (src, cs, ce) in enumerate(seg_specs):
                        out = os.path.join(tmp_dir, f"span_{uid}_{i}.mp4")
                        _ffmpeg_cut_clip(src, out, cs, ce)
                        span_cut_paths.append(out)
                    if len(span_cut_paths) == 1:
                        span_path = span_cut_paths[0]
                    else:
                        span_path = os.path.join(tmp_dir, f"span_{uid}.mp4")
                        sl = os.path.join(tmp_dir, f"span_{uid}_concat.txt")
                        _write_concat_list(sl, span_cut_paths)
                        _ffmpeg_concat(sl, span_path)

                    span_cuts_task = asyncio.create_task(detect_scene_cuts(span_path))
                    async def _analyze_span_nonempty() -> list[dict]:
                        raw = await analyze_video(
                            span_path,
                            timeout_s=_GEMINI_HARD_TIMEOUT_S,
                        )
                        if not raw:
                            raise EmptyDecomposeResultError(
                                f"StageC-analyze-{drama_name_}-c{cand_idx}: "
                                "Gemini returned empty segments"
                            )
                        return raw

                    try:
                        span_raw = await _gemini_retry(
                            _analyze_span_nonempty,
                            f"StageC-analyze-{drama_name_}-c{cand_idx}",
                        )
                    except Exception:
                        if not span_cuts_task.done():
                            span_cuts_task.cancel()
                        try:
                            await span_cuts_task
                        except BaseException:
                            pass
                        raise
                    span_phys = await span_cuts_task
                    span_shots = align_timestamps(list(span_raw), span_phys) if span_raw else []

                    timeline = timeline_from_shots(span_shots)
                    # 使用扩展后的起点构建 clip plan，确保 cut_start 反映前扩展
                    expanded_cand = StartCandidate(
                        episode_no=cand.episode_no,
                        local_start=expanded_start,
                        global_start=cand.global_start,
                        hook_strength=cand.hook_strength,
                        reason=cand.reason,
                    )
                    if timeline:
                        eb = pick_end_boundary(timeline)
                        end = resolve_real_end(
                            expanded_start, ep_refs, eb.cum_time,
                            boundary_type=eb.boundary_type,
                        )
                    else:
                        end = resolve_real_end(expanded_start, ep_refs, min(IDEAL, capacity))
                    plan = build_clip_plan(expanded_cand, end, ep_refs)
                    if not plan.entries:
                        return None

                    clip_plan_dict = {
                        "drama_name": drama_name_,
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
                        # ── 片头过滤 & 上下文扩展元数据 ──
                        "content_start": content_start,
                        "original_gemini_start": cand.local_start,
                        "expanded_start": expanded_start,
                        "pre_roll_applied_s": round(cand.local_start - expanded_start, 2),
                        "correction_log": expand_log or None,
                    }
                    reason_dict = {
                        "hook_strength": cand.hook_strength,
                        "reason": cand.reason,
                        "boundary_type": plan.boundary_type,
                        "content_start": content_start,
                        "corrected": bool(expand_log) or expanded_start != cand.local_start,
                    }
                    # ── 高光规划日志：记录关键决策 ──
                    logger.info(
                        "highlight_plan: 落库 creative drama=%s candidate=%d "
                        "gemini_start=%.1fs expanded_start=%.1fs content_start=%.1fs "
                        "boundary=%s total_duration=%.1fs "
                        "corrected=%s log=%s",
                        drama_name_, cand_idx,
                        cand.local_start, expanded_start, content_start,
                        plan.boundary_type, plan.total_duration,
                        bool(expand_log) or expanded_start != cand.local_start,
                        expand_log or "无修正",
                    )
                    creative = await creative_repo.create_cross_episode_job(
                        tenant_key=tenant_key,
                        session_key=session_key,
                        script_id=None,
                        batch_id=batch_id,
                        source_asset_id=ep_index[cand.episode_no]["id"],
                        clip_plan_json=json.dumps(clip_plan_dict, ensure_ascii=False),
                        highlight_start=expanded_start,
                        highlight_reason_json=json.dumps(reason_dict, ensure_ascii=False),
                        connector_asset_id=connector_asset_id,
                    )
                    creative_id = int(creative["id"])
                    # 规划即合成：创建后立即置 PROCESSING 并提交合成任务
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
                    return {"creative_id": creative_id,
                            "start_episode_no": cand.episode_no,
                            "boundary_type": plan.boundary_type,
                            "elapsed_s": round(time.perf_counter() - t_cand, 3)}

                # as_completed 替代 gather：
                # - 快的候选（30s）立即落库入队，不用等慢的（180s）
                # - 每完成一个候选上报子进度：45%→65%→85% 渐变，消除黑盒感
                total_candidates = len(candidates)
                stage_c_base_pct = 45
                stage_c_range = 40  # 45%→85%

                async def _run_candidate(c: StartCandidate, i: int) -> tuple[int, object]:
                    try:
                        return i, await _process_candidate(c, i)
                    except Exception as exc:
                        return i, exc

                futures = [
                    asyncio.ensure_future(_run_candidate(c, i))
                    for i, c in enumerate(candidates)
                ]
                results_map: dict[int, object] = {}
                completed_count = 0
                for fut in asyncio.as_completed(futures):
                    idx, r = await fut
                    if isinstance(r, Exception):
                        logger.warning(
                            "highlight_plan: candidate %d failed: %s", idx, r,
                        )
                    results_map[idx] = r
                    completed_count += 1

                    if r is not None and not isinstance(r, Exception):
                        logger.info(
                            "highlight_plan: drama=%s candidate %d/%d done "
                            "elapsed=%.1fs creative_id=%s",
                            drama_name_, completed_count, total_candidates,
                            r.get("elapsed_s", 0), r.get("creative_id"),
                        )

                    sub_pct = stage_c_base_pct + int(
                        completed_count / total_candidates * stage_c_range
                    )
                    await _report("stage_c",
                                  f"逐候选细拆规划中 ({completed_count}/{total_candidates})",
                                  sub_pct, {"drama": drama_name_})

                # 恢复原始顺序
                results = [results_map[i] for i in range(total_candidates)]

                timings["stage_c_total_s"] = round(time.perf_counter() - t_stage_c, 3)
                timings["stage_c_candidates"] = total_candidates
                timings["total_s"] = round(time.perf_counter() - t_stage_a, 3)
                await _report("stage_c_done", "规划完成，已入队合成", 85,
                              {"drama": drama_name_, "stage_c_s": timings["stage_c_total_s"],
                               "created_count": len([r for r in results if r is not None and not isinstance(r, Exception)])})
                created: list[dict] = []
                for r in results:
                    if isinstance(r, Exception):
                        logger.warning("highlight_plan: candidate failed: %s", r)
                    elif r is not None:
                        created.append(r)

                if not created:
                    return {"drama_name": drama_name_, "created": [],
                            "timings": timings,
                            "error": f"「{drama_name_}」所有候选都无法凑到 {WINDOW[0]}s"}
                return {"drama_name": drama_name_, "created": created,
                        "timings": timings}

            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

        all_results: list[dict] = []
        errors: list[str] = []
        t_total = time.perf_counter()
        results_list = await asyncio.gather(
            *[_plan_one_drama(dn) for dn in drama_names_list],
            return_exceptions=True,
        )
        for i, result_or_exc in enumerate(results_list):
            dn = drama_names_list[i]
            if isinstance(result_or_exc, Exception):
                err = f"{type(result_or_exc).__name__}: {result_or_exc}"
                logger.error("highlight_plan: drama=%s failed: %s", dn, err)
                errors.append(f"[{dn}] {err}")
                all_results.append({"drama_name": dn, "created": [], "error": err})
            elif isinstance(result_or_exc, dict):
                all_results.append(result_or_exc)
                if result_or_exc.get("error"):
                    errors.append(result_or_exc["error"])

        total_created = sum(len(r.get("created") or []) for r in all_results)
        wall_clock_s = round(time.perf_counter() - t_total, 3)
        # 收集失败剧名和原因，供前端展示
        failed_dramas = [
            {"drama": r.get("drama_name", "?"), "error": r.get("error", "未知错误")}
            for r in all_results
            if r.get("error") and not r.get("created")
        ]
        await _report("done", "全部完成", 100,
                      {"created_count": total_created, "wall_clock_s": wall_clock_s,
                       "failed_dramas": failed_dramas,
                       "drama_count": len(drama_names_list)})
        if total_created == 0:
            return TaskExecutionResult.failed(
                error="; ".join(errors) if errors else "所有剧名均未产出高光",
                details={"results": all_results, "batch_id": batch_id,
                         "wall_clock_s": wall_clock_s},
            )
        return TaskExecutionResult.succeeded(
            summary=f"highlight_plan dramas={len(drama_names_list)} created={total_created} wall_clock={wall_clock_s}s",
            details={"results": all_results, "batch_id": batch_id,
                     "wall_clock_s": wall_clock_s, "parallel_dramas": len(drama_names_list)},
        )

    return execute


def make_video_compose_executor(
    *,
    creative_repo,
    script_repo: ScriptRepository,
    ref_video_repo: ReferenceVideoRepository,
    highlight_asset_repo,
    oss_client,
) -> Callable[[TaskEnvelope], Awaitable[TaskExecutionResult]]:
    """高光成片合成：裁出高光片段，拼接原片或数字人，上传 OSS。"""

    async def execute(task: TaskEnvelope) -> TaskExecutionResult:
        creative_id = int(task.payload["creative_id"])
        creative = await creative_repo.get(creative_id)
        if creative is None:
            return TaskExecutionResult.failed(error=f"creative_id={creative_id} not found")
        if str(creative.get("status") or "") == "READY" and creative.get("oss_key"):
            return TaskExecutionResult.noop(
                summary=f"creative_id={creative_id} already READY",
                details={"creative_id": creative_id, "oss_key": creative.get("oss_key")},
            )

        raw_plan = creative.get("clip_plan_json")
        if raw_plan:
            plan = json.loads(raw_plan) if isinstance(raw_plan, str) else raw_plan
            entries = plan.get("entries") or []
            if not entries:
                return TaskExecutionResult.failed(
                    error=f"creative_id={creative_id} clip_plan 无 entries")
            tmp_dir = tempfile.mkdtemp(prefix=f"flowcut_cross_compose_{creative_id}_")
            try:
                await creative_repo.update_status(creative_id, "PROCESSING")
                loop = asyncio.get_running_loop()
                cut_paths: list[str] = []
                for i, entry in enumerate(entries):
                    src = os.path.join(tmp_dir, f"src_{i}.mp4")
                    await loop.run_in_executor(None, oss_client.download,
                                               str(entry["oss_key"]), src)
                    cut = os.path.join(tmp_dir, f"cut_{i}.mp4")
                    _ffmpeg_cut_clip(src, cut, float(entry["cut_start"]),
                                     float(entry["cut_end"]))
                    cut_paths.append(cut)
                output_path = os.path.join(tmp_dir, "output.mp4")
                if len(cut_paths) == 1:
                    output_path = cut_paths[0]
                else:
                    concat_list = os.path.join(tmp_dir, "concat.txt")
                    _write_concat_list(concat_list, cut_paths)
                    _ffmpeg_concat(concat_list, output_path)

                connector_asset_id = creative.get("connector_asset_id")
                connector_appended = False
                if connector_asset_id is not None:
                    connector_asset = await highlight_asset_repo.get(int(connector_asset_id))
                    if connector_asset is None:
                        return TaskExecutionResult.failed(
                            error=f"connector_asset_id={connector_asset_id} not found"
                        )
                    connector_src = os.path.join(tmp_dir, "connector.mp4")
                    await loop.run_in_executor(
                        None,
                        oss_client.download,
                        str(connector_asset.get("oss_key") or connector_asset.get("oss_url") or ""),
                        connector_src,
                    )
                    highlight_norm = os.path.join(tmp_dir, "highlight_norm.mp4")
                    connector_norm = os.path.join(tmp_dir, "connector_norm.mp4")
                    _ffmpeg_normalize_clip(output_path, highlight_norm)
                    _ffmpeg_normalize_clip(connector_src, connector_norm)
                    connector_concat = os.path.join(tmp_dir, "connector_concat.txt")
                    _write_concat_list(connector_concat, [highlight_norm, connector_norm])
                    output_path = os.path.join(tmp_dir, "output_with_connector.mp4")
                    _ffmpeg_concat(connector_concat, output_path)
                    connector_appended = True

                tenant_key = str(creative.get("tenant_key") or task.tenant_key or "flowcut")
                oss_key = f"creatives/{tenant_key}/highlight/{creative_id}/{uuid.uuid4().hex}.mp4"
                await loop.run_in_executor(None, oss_client.upload, output_path, oss_key)
                await creative_repo.update_status(creative_id, "READY",
                                                  oss_key=oss_key, oss_url=oss_key)
                return TaskExecutionResult.succeeded(
                    summary=f"cross-episode creative composed creative_id={creative_id}",
                    details={"creative_id": creative_id, "oss_key": oss_key,
                             "entries": len(entries),
                             "boundary_type": plan.get("boundary_type"),
                             "connector_appended": connector_appended},
                )
            except Exception as exc:
                try:
                    await creative_repo.update_status(creative_id, "FAILED")
                except Exception:
                    pass
                return TaskExecutionResult.failed(error=f"{type(exc).__name__}: {exc}")
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)

        script_id = creative.get("script_id")
        if script_id is None:
            return TaskExecutionResult.failed(error=f"creative_id={creative_id} has no script_id")
        script = await script_repo.get(int(script_id))
        if script is None:
            return TaskExecutionResult.failed(error=f"script_id={script_id} not found")

        ref_video_id = script.get("reference_video_id")
        if ref_video_id is None:
            return TaskExecutionResult.failed(error=f"script_id={script_id} has no reference_video_id")
        ref_video = await ref_video_repo.get(int(ref_video_id))
        if ref_video is None:
            return TaskExecutionResult.failed(error=f"ref_video_id={ref_video_id} not found")

        start = creative.get("highlight_start")
        end = creative.get("highlight_end")
        if start is None or end is None:
            return TaskExecutionResult.failed(error="creative has no highlight_start/highlight_end")
        start_f = float(start)
        end_f = float(end)
        if end_f <= start_f:
            return TaskExecutionResult.failed(error="invalid highlight time range")

        tmp_dir = tempfile.mkdtemp(prefix=f"flowcut_highlight_compose_{creative_id}_")
        source_path = os.path.join(tmp_dir, "source.mp4")
        followup_path = os.path.join(tmp_dir, "followup.mp4")
        highlight_path = os.path.join(tmp_dir, "highlight.mp4")
        followup_norm_path = os.path.join(tmp_dir, "followup_norm.mp4")
        concat_list_path = os.path.join(tmp_dir, "concat.txt")
        output_path = os.path.join(tmp_dir, "output.mp4")

        try:
            await creative_repo.update_status(creative_id, "PROCESSING")
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                oss_client.download,
                str(ref_video.get("oss_key") or ref_video.get("oss_url") or ""),
                source_path,
            )

            creative_type = str(creative.get("creative_type") or "")
            frontload_threshold = 0.35
            compose_strategy = "frontload_highlight_then_followup"
            if creative_type == "highlight_digital_human":
                connector_asset_id = creative.get("connector_asset_id")
                if connector_asset_id is None:
                    return TaskExecutionResult.failed(error="digital human creative has no connector_asset_id")
                connector_asset = await highlight_asset_repo.get(int(connector_asset_id))
                if connector_asset is None:
                    return TaskExecutionResult.failed(error=f"connector_asset_id={connector_asset_id} not found")
                await loop.run_in_executor(
                    None,
                    oss_client.download,
                    str(connector_asset.get("oss_key") or connector_asset.get("oss_url") or ""),
                    followup_path,
                )
            else:
                followup_path = source_path

            if creative_type == "highlight_original" and start_f <= frontload_threshold:
                # 原片开头本身就是高光时，重复拼成「开头高光 + 原片开头」
                # 会产生明显拖沓。此时直接输出高质量标准化后的原片。
                compose_strategy = "original_starts_with_highlight_no_duplicate"
                _ffmpeg_normalize_clip(source_path, output_path)
            else:
                _ffmpeg_cut_clip(source_path, highlight_path, start_f, end_f)
                _ffmpeg_normalize_clip(followup_path, followup_norm_path)
                _write_concat_list(concat_list_path, [highlight_path, followup_norm_path])
                _ffmpeg_concat(concat_list_path, output_path)

            tenant_key = str(creative.get("tenant_key") or task.tenant_key or "flowcut")
            oss_key = f"creatives/{tenant_key}/highlight/{creative_id}/{uuid.uuid4().hex}.mp4"
            await loop.run_in_executor(None, oss_client.upload, output_path, oss_key)
            await creative_repo.update_status(
                creative_id,
                "READY",
                oss_key=oss_key,
                oss_url=oss_key,
            )
            return TaskExecutionResult.succeeded(
                summary=f"highlight creative composed creative_id={creative_id}",
                details={
                    "creative_id": creative_id,
                    "oss_key": oss_key,
                    "highlight_start": start_f,
                    "highlight_end": end_f,
                    "compose_strategy": compose_strategy,
                },
            )
        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}"
            try:
                await creative_repo.update_status(creative_id, "FAILED")
            except Exception:
                pass
            return TaskExecutionResult.failed(error=error_text)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return execute


def make_highlight_export_executor(
    *,
    creative_repo,
    highlight_asset_repo,
    oss_client,
) -> Callable[[TaskEnvelope], Awaitable[TaskExecutionResult]]:
    """导出跨集高光：把已合成的 1 分钟片与所选数字人 ffmpeg 拼接，产出可下载 mp4。

    成片本身不变（仍是纯片）；拼接结果落到 OSS export 目录，返回 result_url 供前端下载。
    """

    async def execute(task: TaskEnvelope) -> TaskExecutionResult:
        creative_id = int(task.payload["creative_id"])
        creative = await creative_repo.get(creative_id)
        if creative is None:
            return TaskExecutionResult.failed(error=f"creative_id={creative_id} not found")
        clip_key = str(creative.get("oss_key") or "")
        if not clip_key:
            return TaskExecutionResult.failed(error=f"creative_id={creative_id} 还没有 1 分钟片")

        # 可选：前贴图片素材
        preroll_asset_id = creative.get("preroll_asset_id")
        preroll: dict | None = None
        if preroll_asset_id is not None:
            preroll = await highlight_asset_repo.get(int(preroll_asset_id))
            if preroll is None:
                return TaskExecutionResult.failed(
                    error=f"preroll_asset_id={preroll_asset_id} not found")

        # 可选：数字人连接器
        connector_asset_id = creative.get("connector_asset_id")
        connector: dict | None = None
        if connector_asset_id is not None:
            connector = await highlight_asset_repo.get(int(connector_asset_id))
            if connector is None:
                return TaskExecutionResult.failed(
                    error=f"connector_asset_id={connector_asset_id} not found")

        tmp_dir = tempfile.mkdtemp(prefix=f"flowcut_highlight_export_{creative_id}_")
        try:
            loop = asyncio.get_running_loop()
            clip_src = os.path.join(tmp_dir, "clip.mp4")
            await loop.run_in_executor(None, oss_client.download, clip_key, clip_src)

            # --- Branch A: 叠加前贴 or 仅归一化 ---
            clip_processed = os.path.join(tmp_dir, "clip_processed.mp4")
            if preroll is not None:
                preroll_src = os.path.join(tmp_dir, "preroll.png")
                await loop.run_in_executor(
                    None, oss_client.download,
                    str(preroll.get("oss_key") or preroll.get("oss_url") or ""),
                    preroll_src,
                )
                _ffmpeg_normalize_with_overlay(clip_src, preroll_src, clip_processed)
            else:
                _ffmpeg_normalize_clip(clip_src, clip_processed)

            # --- Branch B: 拼接数字人 or 直接输出 ---
            if connector is not None:
                dh_src = os.path.join(tmp_dir, "dh.mp4")
                await loop.run_in_executor(
                    None, oss_client.download,
                    str(connector.get("oss_key") or connector.get("oss_url") or ""),
                    dh_src,
                )
                dh_norm = os.path.join(tmp_dir, "dh_norm.mp4")
                _ffmpeg_normalize_clip(dh_src, dh_norm)
                output_path = os.path.join(tmp_dir, "export.mp4")
                concat_list = os.path.join(tmp_dir, "concat.txt")
                _write_concat_list(concat_list, [clip_processed, dh_norm])
                _ffmpeg_concat(concat_list, output_path)
                suffix = "数字人"
            else:
                output_path = clip_processed
                suffix = "前贴"

            tenant_key = str(creative.get("tenant_key") or task.tenant_key or "flowcut")
            oss_key = f"creatives/{tenant_key}/export/{creative_id}/{uuid.uuid4().hex}.mp4"
            await loop.run_in_executor(None, oss_client.upload, output_path, oss_key)
            drama = str(creative.get("source_drama_name")
                        or creative.get("source_asset_name") or "高光")
            result_url = oss_client.presigned_get_url(
                oss_key, disposition_filename=f"{drama}_{creative_id}_{suffix}.mp4")
            return TaskExecutionResult.succeeded(
                summary=f"highlight export composed creative_id={creative_id}",
                details={"creative_id": creative_id, "oss_key": oss_key,
                         "result_url": result_url},
            )
        except Exception as exc:
            return TaskExecutionResult.failed(error=f"{type(exc).__name__}: {exc}")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return execute


def _ffmpeg_path() -> str:
    env_path = os.environ.get("FFMPEG_PATH", "").strip()
    if env_path:
        # 若是绝对路径 → 直接用；否则（裸名称如 "ffmpeg"）走 shutil.which 解析
        if os.path.isabs(env_path):
            return env_path
        resolved = shutil.which(env_path)
        if resolved:
            return resolved
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found. Please install ffmpeg first.")
    return ffmpeg


def _run_ffmpeg(args: list[str], *, timeout: int = 900) -> None:
    result = subprocess.run(
        [_ffmpeg_path(), "-y", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffmpeg failed")


def _ffmpeg_cut_clip(source_path: str, output_path: str, start: float, end: float) -> None:
    duration = max(0.1, end - start)
    _run_ffmpeg(
        [
            "-ss",
            f"{max(0.0, start):.3f}",
            "-i",
            source_path,
            "-t",
            f"{duration:.3f}",
            "-vf",
            "scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1",
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            output_path,
        ]
    )


def _ffmpeg_normalize_clip(source_path: str, output_path: str) -> None:
    # 统一到 720×1280 竖屏：跨集合并/拼接时各源分辨率可能不一致（720p+1080p
    # 混合），仅调偶数不统一会让后续 concat -c copy 产出分辨率突变的畸形视频，
    # 导致 PySceneDetect 在突变段检测不到切点。force_original_aspect_ratio
    # +pad 保持宽高比并补黑边。
    _run_ffmpeg(
        [
            "-i",
            source_path,
            "-vf",
            "scale=720:1280:force_original_aspect_ratio=decrease,"
            "pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1",
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            output_path,
        ],
        timeout=1800,
    )


def _ffmpeg_normalize_with_overlay(
    source_path: str, overlay_path: str, output_path: str
) -> None:
    """将 overlay_path（PNG 前贴图）叠加到 source_path 视频上，输出归一化 mp4。

    overlay 缩放至与 base 视频相同分辨率后覆盖（左上角对齐）。
    若 source 无音轨，`0:a?` 使音轨映射静默失败而非报错。
    """
    filter_complex = (
        "[1:v]format=rgba[ovr];"
        "[0:v]scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1[base];"
        "[ovr][base]scale2ref[ovr_s][base2];"
        "[base2][ovr_s]overlay=0:0[outv]"
    )
    _run_ffmpeg(
        [
            "-i", source_path,
            "-i", overlay_path,
            "-filter_complex", filter_complex,
            "-map", "[outv]",
            "-map", "0:a?",
            "-r", "30",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "18",
            "-c:a", "aac",
            "-ar", "44100",
            "-ac", "2",
            "-movflags", "+faststart",
            output_path,
        ],
        timeout=1800,
    )


def _write_concat_list(path: str, files: list[str]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for file_path in files:
            safe = file_path.replace("'", "'\\''")
            f.write(f"file '{safe}'\n")


def _ffmpeg_concat(concat_list_path: str, output_path: str) -> None:
    _run_ffmpeg(
        [
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            concat_list_path,
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            output_path,
        ],
        timeout=1800,
    )


def make_qianchuan_publish_executor(
    creative_repo,
    oss_client,
    *,
    cdp_url: str,
) -> Callable[[TaskEnvelope], Awaitable[TaskExecutionResult]]:
    """素材上传千川 + 创建计划。

    pipeline:
      1. 取 fc_creative，校验 status=READY 且有 oss_key
      2. 下载成片到临时文件
      3. 调 publisher.publish_creative_via_browser 走浏览器流程（默认 dry-run）
      4. 成功 → update_qianchuan_ids 回写 material/campaign ID
      5. 失败 → 把 snapshot（若有）塞进 details 供调试 / AI fallback 参考
    """
    import shutil as _shutil

    async def execute(task: TaskEnvelope) -> TaskExecutionResult:
        from Flowcut.services.qianchuan_publisher import (
            PublishError,
            publish_creative_via_browser,
        )

        creative_id = task.payload.get("creative_id")
        title = task.payload.get("title")
        if not isinstance(creative_id, int) or not title:
            return TaskExecutionResult.failed(
                error=f"qianchuan_publish: payload 缺 creative_id/title (task={task.task_id})",
            )

        creative = await creative_repo.get(creative_id)
        if creative is None:
            return TaskExecutionResult.failed(
                error=f"qianchuan_publish: creative {creative_id} not found",
            )
        if creative.get("status") != "READY":
            return TaskExecutionResult.failed(
                error=(
                    f"qianchuan_publish: creative {creative_id} status="
                    f"{creative.get('status')!r}，需要 READY"
                ),
            )
        oss_key = creative.get("oss_key")
        if not oss_key:
            return TaskExecutionResult.failed(
                error=f"qianchuan_publish: creative {creative_id} 没有 oss_key",
            )

        workdir = Path(tempfile.mkdtemp(prefix=f"flowcut-publish-{creative_id}-"))
        local_path = workdir / "creative.mp4"
        try:
            try:
                oss_client.download(oss_key, str(local_path))
            except Exception as exc:
                return TaskExecutionResult.failed(
                    error=f"qianchuan_publish: 下载 {oss_key} 失败: {exc}",
                )

            try:
                result = await publish_creative_via_browser(
                    local_video_path=str(local_path),
                    title=title,
                    cdp_url=cdp_url,
                    creative_id=creative_id,
                )
            except PublishError as exc:
                details: dict = {"creative_id": creative_id, "title": title}
                if exc.snapshot:
                    details["snapshot"] = exc.snapshot
                return TaskExecutionResult.failed(
                    error=f"qianchuan_publish: {exc}",
                    details=details,
                )

            try:
                await creative_repo.update_qianchuan_ids(
                    creative_id,
                    material_id=result.material_id,
                    campaign_id=result.campaign_id,
                )
            except Exception as exc:
                return TaskExecutionResult.failed(
                    error=(
                        f"qianchuan_publish: publisher OK 但回写 DB 失败: {exc} "
                        f"(material={result.material_id}, campaign={result.campaign_id})"
                    ),
                )

            summary = (
                f"qianchuan_publish: creative={creative_id} → "
                f"material={result.material_id}, campaign={result.campaign_id}"
            )
            if result.dry_run:
                summary += " [DRY_RUN]"
            return TaskExecutionResult.succeeded(
                summary=summary,
                details={
                    "creative_id": creative_id,
                    "qianchuan_material_id": result.material_id,
                    "qianchuan_campaign_id": result.campaign_id,
                    "dry_run": result.dry_run,
                },
            )
        finally:
            _shutil.rmtree(workdir, ignore_errors=True)

    return execute


def make_qianchuan_sync_executor(
    creative_repo,
    qianchuan_repo,
    *,
    cdp_url: str,
    tenant_key: str,
) -> Callable[[TaskEnvelope], Awaitable[TaskExecutionResult]]:
    """T+1 数据回流：从千川抓取视频物料维度报表，写回 fc_creative.qc_* 字段。

    三段式对齐逻辑：
      1. 按 qc_material_id 查 fc_creative → UPDATE qc_*
      2. 否则按文件名正则 fc-<id>- 提取 creative_id → 首次绑定 + UPDATE qc_*
      3. 都不匹配 → INSERT 一条新 fc_creative（千川反向导入，session_key=qianchuan_import）
    """
    import re as _re

    _FC_NAME_RE = _re.compile(r"fc-(\d+)-")

    async def execute(task: TaskEnvelope) -> TaskExecutionResult:
        from Flowcut.services.qianchuan_scraper import fetch_video_material_stats

        try:
            rows = await fetch_video_material_stats(cdp_url=cdp_url)
        except Exception as exc:
            return TaskExecutionResult.failed(
                error=f"qianchuan_scraper failed: {exc}",
                summary="qianchuan_sync: scraper error",
            )

        if not rows:
            return TaskExecutionResult.succeeded(
                summary="qianchuan_sync: 0 rows (no spend data yet)",
                details={"matched": 0, "orphaned": 0},
            )

        matched = 0
        imported = 0
        errors: list[str] = []

        for row in rows:
            qc_material_id: str = row["material_id"]
            material_name: str = row.get("material_name", "")
            cost = row.get("cost")
            conversions = row.get("conversions")
            impressions = row.get("impressions")
            clicks = row.get("clicks")

            try:
                # 第一段：按 qc_material_id 查已绑定的成片
                creative = await creative_repo.find_by_qc_material_id(qc_material_id)

                if creative is None:
                    # 第二段：从文件名 fc-<id>- 尝试提取 creative_id
                    m = _FC_NAME_RE.search(material_name)
                    if m:
                        cid = int(m.group(1))
                        creative = await creative_repo.get(cid)

                if creative is not None:
                    bind_id = (
                        qc_material_id
                        if not creative.get("qc_material_id")
                        else None
                    )
                    await creative_repo.update_qc_stats(
                        creative["id"],
                        qc_material_id=bind_id,
                        qc_cost=cost,
                        qc_impressions=impressions,
                        qc_clicks=clicks,
                        qc_conversions=conversions,
                    )
                    matched += 1
                else:
                    # 第三段：千川反向导入为新 fc_creative
                    await creative_repo.insert_from_qc(
                        tenant_key=tenant_key,
                        qc_material_id=qc_material_id,
                        material_name=material_name,
                        oss_url=None,
                        qc_cost=cost,
                        qc_impressions=impressions,
                        qc_clicks=clicks,
                        qc_conversions=conversions,
                    )
                    imported += 1

            except Exception as row_exc:
                errors.append(f"material_id={qc_material_id}: {row_exc}")
                logger.warning(
                    "qianchuan_sync: 处理 row 失败 material_id=%s err=%s",
                    qc_material_id, row_exc,
                )

        detail: dict = {
            "total_rows": len(rows),
            "matched": matched,
            "imported": imported,
        }
        if errors:
            detail["errors"] = errors

        return TaskExecutionResult.succeeded(
            summary=(
                f"qianchuan_sync: total={len(rows)} "
                f"matched={matched} imported={imported} errors={len(errors)}"
            ),
            details=detail,
        )

    return execute


def make_vector_repair_executor(
    material_repo: MaterialRepository,
    embedding_service: EmbeddingService,
    vector_store: VectorStore,
) -> Callable[[TaskEnvelope], Awaitable[TaskExecutionResult]]:
    """扫描未向量化的 READY 素材，逐一 embedding + Qdrant upsert。

    定时任务，每 10 分钟由 AppContainer 入队执行。
    LIMIT 100 每轮，防止单次执行时间过长。
    """

    async def execute(task: TaskEnvelope) -> TaskExecutionResult:
        pending = await material_repo.list_pending_vector(limit=100)
        if not pending:
            return TaskExecutionResult.succeeded(
                summary="vector_repair: no pending materials",
                details={"repaired": 0},
            )

        repaired = 0
        errors: list[str] = []
        for mat in pending:
            mid = mat["id"]
            description = mat.get("description")
            if not description:
                continue
            try:
                desc_vec = await embedding_service.embed(description)
                transcript = mat.get("transcript")
                transcript_vec = (
                    await embedding_service.embed(transcript) if transcript else None
                )
                payload = {
                    "tenant_key": mat["tenant_key"],
                    "product": mat.get("product"),
                    "scene_role": mat.get("scene_role"),
                    "status": "READY",
                    "has_transcript": bool(transcript),
                }
                await vector_store.upsert(mid, desc_vec, transcript_vec, payload)
                await material_repo.mark_vector_indexed(mid)
                repaired += 1
            except Exception as exc:
                errors.append(f"material_id={mid}: {exc}")
                logger.warning("vector_repair failed for material %d: %s", mid, exc)

        detail = {"repaired": repaired, "total_pending": len(pending)}
        if errors:
            detail["errors"] = errors

        return TaskExecutionResult.succeeded(
            summary=f"vector_repair: repaired={repaired}/{len(pending)} errors={len(errors)}",
            details=detail,
        )

    return execute


def make_export_package_executor(
    *,
    script_repo: ScriptRepository,
    material_repo: MaterialRepository,
    ref_video_repo: ReferenceVideoRepository,
    oss_client,
) -> Callable[[TaskEnvelope], Awaitable[TaskExecutionResult]]:
    """打包 zip：script.json/md + materials/{mid}.mp4 + manifest.json + audio.mp3 + reference.mp4。

    包结构（2026-05 改版）：
      - materials/{mid}.mp4：所有用到的素材去重，每个 mid 一份
      - manifest.json：[{"seg_idx": int, "material_ids": [...]}, ...]，按 seg_idx 升序，只含非空段
      - script.md：每段末尾追加 "使用素材：..." 行
      - script.json / audio.mp3 / reference.mp4 / missing_materials.txt：保持原行为
    """
    import shutil
    import time
    import zipfile

    async def _executor(task: TaskEnvelope) -> TaskExecutionResult:
        script_id = task.payload["script_id"]
        selections_raw = task.payload.get("selections")
        if not isinstance(selections_raw, dict) or not selections_raw:
            return TaskExecutionResult.failed(
                error=f"export_package: payload.selections missing or empty (script={script_id})",
            )
        # 规整 selections：{seg_idx(int): [material_ids]}，按 seg_idx 升序
        selections: dict[int, list[int]] = {}
        for k, v in selections_raw.items():
            try:
                seg_idx = int(k)
            except (TypeError, ValueError):
                return TaskExecutionResult.failed(
                    error=f"export_package: selections key not int: {k!r}",
                )
            if not isinstance(v, list):
                return TaskExecutionResult.failed(
                    error=f"export_package: selections[{k}] not list",
                )
            selections[seg_idx] = [int(m) for m in v]

        tenant_key = task.tenant_key or "unknown"

        script = await script_repo.get(script_id)
        if script is None:
            return TaskExecutionResult.failed(error=f"script {script_id} not found")

        ref_video = None
        if script.get("source") == "decomposed" and script.get("reference_video_id"):
            ref_video = await ref_video_repo.get(script["reference_video_id"])

        # 收集去重的 material_ids，按 id 升序下载
        all_mids: list[int] = sorted({m for ids in selections.values() for m in ids})

        workdir = Path(tempfile.mkdtemp(prefix=f"flowcut-export-{script_id}-"))
        missing: list[int] = []
        try:
            materials_dir = workdir / "materials"
            materials_dir.mkdir()
            for mid in all_mids:
                mat = await material_repo.get(mid)
                if mat is None or not mat.get("oss_key"):
                    missing.append(mid)
                    continue
                local = materials_dir / f"{mid}.mp4"
                try:
                    oss_client.download(mat["oss_key"], str(local))
                except Exception as exc:
                    logger.warning("export: download material %s failed: %s", mid, exc)
                    missing.append(mid)

            if ref_video and ref_video.get("audio_oss_key"):
                try:
                    oss_client.download(ref_video["audio_oss_key"], str(workdir / "audio.mp3"))
                except Exception as exc:
                    logger.warning("export: download audio failed: %s", exc)

            if ref_video and ref_video.get("oss_key"):
                try:
                    oss_client.download(ref_video["oss_key"], str(workdir / "reference.mp4"))
                except Exception as exc:
                    logger.warning("export: download reference failed: %s", exc)

            (workdir / "script.json").write_text(
                json.dumps(script, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )

            # manifest.json：按 seg_idx 升序，只含非空段
            manifest = [
                {"seg_idx": seg_idx, "material_ids": list(selections[seg_idx])}
                for seg_idx in sorted(selections)
                if selections[seg_idx]
            ]
            (workdir / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            md_lines = [f"# 脚本 {script_id}\n"]
            for seg in script.get("segments", []):
                seg_idx = int(seg.get("idx", 0))
                md_lines.append(
                    f"## 段 {seg_idx} "
                    f"({float(seg.get('start_time', 0)):.2f}s - "
                    f"{float(seg.get('end_time', 0)):.2f}s)\n"
                )
                md_lines.append(f"**画面**：{seg.get('visual', '')}\n")
                md_lines.append(f"**文案**：{seg.get('copy', '')}\n")
                used = selections.get(seg_idx) or []
                if used:
                    md_lines.append("使用素材：" + ", ".join(str(m) for m in used) + "\n")
                else:
                    md_lines.append("使用素材：（未选）\n")
            (workdir / "script.md").write_text("\n".join(md_lines), encoding="utf-8")

            if missing:
                (workdir / "missing_materials.txt").write_text(
                    "\n".join(str(m) for m in missing), encoding="utf-8"
                )

            ts = int(time.time())
            zip_path = workdir.parent / f"export_{ts}_{script_id}.zip"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in workdir.rglob("*"):
                    if f.is_file():
                        zf.write(f, f.relative_to(workdir))

            export_key = f"exports/{tenant_key}/{ts}_{script_id}.zip"
            oss_client.upload(str(zip_path), export_key)
            result_url = oss_client.presigned_get_url(export_key, expires=24 * 3600)

            try:
                zip_path.unlink()
            except OSError:
                pass

            return TaskExecutionResult.succeeded(
                summary=f"export_package: script={script_id} url={result_url}",
                details={
                    "script_id": script_id,
                    "export_key": export_key,
                    "result_url": result_url,
                    "missing_materials": missing,
                },
            )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    return _executor
