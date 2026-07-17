"""Gemini 多模态视频理解 — 语义拆镜。

流程：
  1. 视频压缩到 720p 轻量级（Gemini 拆镜不需要 1080p）
  2. 压缩后 < 阈值则 base64 inline（跳过 Files API 上传+轮询，省 60-240s）
  3. 压缩后 ≥ 阈值则走 Files API 兜底
  4. 解析返回文本，得到 [{start_time, end_time, visual, copy, category}] 列表
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import google.genai as genai
from google.genai import types

from simpleclaw.llm.genai_client import make_genai_client

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "gemini-3.1-flash-lite-preview"
_HIGHLIGHT_PROMPT_PATH = (
    Path(__file__).resolve().parents[1] / "workspace" / "highlight" / "base_prompt.md"
)

# Total wall-clock cap for one analyze_video call (upload + active wait + generate).
# Default 1800s (30 min) as a generous safety net — caller can pass timeout_s=None
# to skip asyncio.wait_for entirely (used by long-running highlight_plan executor).
_DEFAULT_TIMEOUT_S = float(os.getenv("FLOWCUT_GEMINI_TIMEOUT_S", "1800"))
# Per-stage cap inside the blocking call (file ACTIVE polling).
# Upload + ACTIVE state should normally finish within 2 min; 120s is generous.
_UPLOAD_ACTIVE_TIMEOUT_S = 120
# Gemini 3.x flash/flash-lite 官方输出上限为 65536（64k）。默认拉满，避免长视频
# 密集拆镜被 8192 截断成稀疏分镜（可用 FLOWCUT_GEMINI_MAX_OUTPUT_TOKENS 下调）。
_MAX_OUTPUT_TOKENS = int(os.getenv("FLOWCUT_GEMINI_MAX_OUTPUT_TOKENS", "65536"))

# ── P0 视频压缩 + inline 内联 ──
# 压缩参数：720p + 2Mbps CRF 23。Gemini 拆镜不需要高清画质，降分辨率大幅减少体积。
_COMPRESS_WIDTH = 1280
_COMPRESS_BITRATE = "2M"
_COMPRESS_CRF = "23"
# inline 阈值（MB）：压缩后在此以下直接内联 base64，省掉 Files API 上传+轮询
_INLINE_MAX_MB = int(os.getenv("GEMINI_INLINE_MAX_MB", "15"))
# GEMINI_BASE_URL usually goes through a third-party generateContent proxy. Those
# services tend to reject large JSON bodies before Gemini sees them, and base64
# adds ~33% overhead, so keep the inline limit lower than direct Google calls.
_BASE_URL_INLINE_MAX_MB = int(
    os.getenv("GEMINI_BASE_URL_INLINE_MAX_MB", os.getenv("GEMINI_INLINE_BASE_URL_MAX_MB", "6"))
)
_COMPRESS_TIMEOUT_S = int(os.getenv("FLOWCUT_GEMINI_COMPRESS_TIMEOUT_S", "300"))
_INLINE_RESCUE_PROFILES = (
    ("compact", 854, "700k", "30", "12"),
    ("tiny", 640, "350k", "34", "8"),
    ("micro", 480, "180k", "36", "6"),
)


def _ffmpeg_path() -> str:
    configured = (
        os.getenv("FLOWCUT_FFMPEG_PATH", "").strip()
        or os.getenv("FFMPEG_PATH", "").strip()
    )
    candidates: list[Path | str] = []
    if configured:
        candidates.append(Path(configured) if os.path.isabs(configured) else configured)
    candidates.extend([
        "ffmpeg",
        Path(__file__).resolve().parents[3] / "flowcut_frontend" / "ffmpeg.exe",
    ])
    for candidate in candidates:
        if isinstance(candidate, Path):
            if candidate.exists():
                return str(candidate)
            continue
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    raise RuntimeError(
        "ffmpeg not found; set FLOWCUT_FFMPEG_PATH or keep "
        "flowcut_frontend/ffmpeg.exe in the repository."
    )

_DECOMPOSE_PROMPT = """\
请仔细观看这段视频，按镜头级别拆分为若干段落，每段时长 2-5 秒。
满足以下任一条件时开新段：切景、主体变化、景别变化、动作逻辑变化。

对每段需要分别填写两个独立字段：
- visual：纯画面描述。只描述人物、物体、场景、动作、镜头语言（景别/运镜/构图）、画面文字标签等
  视觉信息。**绝对不要在 visual 里写口播台词或对白。**
- copy：这一段中真人或旁白说出的话（口播 / 对白 / 旁白）。需逐字转录为中文文本，包含标点。
  如果该段没有人说话（纯产品展示空镜、纯背景音乐等），copy 字段填空字符串 ""。

对每段还需判断素材类别 category，仅限以下两种：
- "真人口播"：画面以真人面部/半身/全身出镜为主，有人在说话或做表情动作
- "产品展示"：画面以产品特写、产品使用过程、包装展示、场景空镜为主

输出严格遵循 JSON 数组格式，不要添加任何解释文字：
[
  {
    "start_time": <累计秒数，数字>,
    "end_time": <累计秒数，数字>,
    "visual": "<纯画面描述，不含口播台词>",
    "copy": "<该段中说话内容的逐字转录；无人说话则填空字符串>",
    "category": "<真人口播 或 产品展示>"
  },
  ...
]
"""


def _parse_segments(raw_text: str) -> list[dict]:
    """从模型返回的文本中提取 JSON 数组，容错处理 markdown 代码块。

    返回字段：{start_time, end_time, visual, copy, category}。
    向后兼容：若旧 schema 出现 `content` 字段，则当作 visual，copy 取空。
    """
    payload = _parse_json_payload(raw_text)
    return _parse_segments_from_payload(payload, preserve_extra=False)


def _parse_json_payload(raw_text: str) -> object:
    text = raw_text.strip()

    fence = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if fence:
        text = fence.group(1).strip()

    try:
        return json.loads(text)
    except Exception:
        return []


def _parse_segments_from_payload(payload: object, *, preserve_extra: bool) -> list[dict]:
    if isinstance(payload, dict):
        data = payload.get("segments") or []
    else:
        data = payload
    if not isinstance(data, list):
        return []

    result: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item.get("start_time", 0.0))
            end = float(item.get("end_time", start + 1.0))
            if "visual" in item or "copy" in item:
                visual = str(item.get("visual", ""))
                copy_text = str(item.get("copy", ""))
            else:
                # 向后兼容旧 schema：content → visual，copy 留空待 ASR 或后续填
                visual = str(item.get("content", ""))
                copy_text = ""
            category = str(item.get("category", "产品展示"))
            if category not in ("真人口播", "产品展示"):
                category = "产品展示"
            normalized = {
                "start_time": start,
                "end_time": end,
                "visual": visual,
                "copy": copy_text,
                "category": category,
            }
            if preserve_extra:
                normalized = {**item, **normalized}
            result.append(normalized)
        except (TypeError, ValueError):
            continue
    return result


def load_highlight_prompt() -> str:
    """读取 highlight workflow 的底层共享 prompt。"""
    return _HIGHLIGHT_PROMPT_PATH.read_text(encoding="utf-8")


def _upload_active_file(client: genai.Client, file_path: str):
    """上传文件到 Gemini Files API，并等待 ACTIVE。"""
    suffix = os.path.splitext(file_path)[1] or ".mp4"
    tmp_dir: str | None = None
    upload_path = file_path
    if not file_path.encode("ascii", errors="ignore").decode() == file_path:
        # 使用独立子目录，避免 genai SDK 内部的 temp 文件 rename
        # 与我们的 temp 文件路径冲突（Windows os.rename 不允许覆盖已存在文件）
        tmp_dir = tempfile.mkdtemp()
        safe_name = "video" + suffix
        upload_path = os.path.join(tmp_dir, safe_name)
        shutil.copy2(file_path, upload_path)

    try:
        try:
            uploaded = client.files.upload(file=upload_path)
        except KeyError as exc:
            if "Upload URL was not returned" not in str(exc):
                raise
            transport = (
                "GEMINI_BASE_URL"
                if os.getenv("GEMINI_BASE_URL", "").strip()
                else "Google direct"
            )
            raise RuntimeError(
                "Gemini Files API did not return an upload URL "
                f"(transport={transport}). The running backend is not using a "
                "Files-API-compatible Google endpoint. Restart FlowCut so the "
                "configured GEMINI_BASE_URL takes effect and the video uses inline upload."
            ) from exc
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    deadline = time.time() + _UPLOAD_ACTIVE_TIMEOUT_S
    while uploaded.state.name != "ACTIVE":
        if time.time() > deadline:
            raise TimeoutError(f"Gemini file upload timed out: {uploaded.name}")
        time.sleep(2)
        uploaded = client.files.get(name=uploaded.name)
    return uploaded


def _read_video_bytes(file_path: str) -> bytes:
    """读取视频文件的全部字节，处理非 ASCII 路径兼容。"""
    suffix = os.path.splitext(file_path)[1] or ".mp4"
    tmp_dir: str | None = None
    read_path = file_path
    if not file_path.encode("ascii", errors="ignore").decode() == file_path:
        tmp_dir = tempfile.mkdtemp()
        safe_name = "video" + suffix
        read_path = os.path.join(tmp_dir, safe_name)
        shutil.copy2(file_path, read_path)
    try:
        with open(read_path, "rb") as fh:
            return fh.read()
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _file_size_mb(file_path: str) -> float:
    return os.path.getsize(file_path) / 1e6


def _inline_limit_mb(use_base_url: bool) -> int:
    return _BASE_URL_INLINE_MAX_MB if use_base_url else _INLINE_MAX_MB


def _is_request_too_large(exc: Exception) -> bool:
    msg = f"{type(exc).__name__}: {exc}"
    return any(
        token in msg
        for token in (
            "413",
            "Request Entity Too Large",
            "request body too large",
            "请求体过大",
            "内容过大",
        )
    )


def _compress_video_variant(
    src_path: str,
    dst_dir: str,
    *,
    name: str,
    width: int,
    bitrate: str,
    crf: str,
    fps: str | None = None,
) -> str:
    out_path = os.path.join(dst_dir, f"{name}.mp4")
    vf = f"scale={width}:-2"
    if fps:
        vf += f",fps={fps}"
    cmd = [
        _ffmpeg_path(), "-y", "-loglevel", "error",
        "-i", src_path,
        "-vf", vf,
        "-c:v", "libx264",
        "-crf", crf,
        "-b:v", bitrate,
        "-preset", "ultrafast",
        "-an",
        out_path,
    ]
    try:
        subprocess.run(cmd, check=True, timeout=_COMPRESS_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        logger.warning("video compress timed out, falling back to original: %s", src_path)
        return src_path
    except subprocess.CalledProcessError as e:
        logger.warning("video compress failed (rc=%s), falling back to original: %s", e.returncode, src_path)
        return src_path

    orig_size = os.path.getsize(src_path)
    new_size = os.path.getsize(out_path)
    logger.info(
        "video compressed[%s]: %.1fMB -> %.1fMB (%.0f%%)",
        name, orig_size / 1e6, new_size / 1e6, new_size / max(orig_size, 1) * 100,
    )
    return out_path


def _compress_video(src_path: str, dst_dir: str) -> str:
    """FFmpeg 压缩视频到 720p 低码率，返回压缩后文件路径。

    Gemini 拆镜只需要看懂场景切换，不需要原画质。
    压缩后体积从 200-400MB 降到 10-20MB，可直接 base64 inline。
    """
    out_path = os.path.join(dst_dir, "compressed.mp4")
    cmd = [
        _ffmpeg_path(), "-y", "-loglevel", "error",
        "-i", src_path,
        "-vf", f"scale={_COMPRESS_WIDTH}:-2",
        "-c:v", "libx264",
        "-crf", _COMPRESS_CRF,
        "-b:v", _COMPRESS_BITRATE,
        "-preset", "ultrafast",
        "-an",          # 去掉音轨：拆镜只需要画面，音频对 Gemini 无意义且增加体积
        out_path,
    ]
    try:
        subprocess.run(cmd, check=True, timeout=_COMPRESS_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        logger.warning("video compress timed out, falling back to original: %s", src_path)
        return src_path
    except subprocess.CalledProcessError as e:
        logger.warning("video compress failed (rc=%s), falling back to original: %s", e.returncode, src_path)
        return src_path

    orig_size = os.path.getsize(src_path)
    new_size = os.path.getsize(out_path)
    logger.info(
        "video compressed: %.1fMB -> %.1fMB (%.0f%%)",
        orig_size / 1e6, new_size / 1e6, new_size / max(orig_size, 1) * 100,
    )
    return out_path


def _compress_video_to_inline_limit(src_path: str, dst_dir: str, limit_mb: float) -> str:
    """Try increasingly small analysis profiles until the inline body is safe."""
    best_path = src_path
    best_size = _file_size_mb(src_path)
    for name, width, bitrate, crf, fps in _INLINE_RESCUE_PROFILES:
        path = _compress_video_variant(
            src_path,
            dst_dir,
            name=name,
            width=width,
            bitrate=bitrate,
            crf=crf,
            fps=fps,
        )
        size_mb = _file_size_mb(path)
        if size_mb < best_size:
            best_path = path
            best_size = size_mb
        if size_mb <= limit_mb:
            return path
    return best_path


def _video_inline_part(file_path: str) -> types.Part:
    """将视频文件封装为 inline_data Part（base64 内联，用于中转模式）。"""
    data = _read_video_bytes(file_path)
    return types.Part(inline_data=types.Blob(mime_type="video/mp4", data=data))


def _prepare_video_for_request(
    src_path: str,
    tmp_dir: str,
    *,
    inline_part_limit_mb: float,
    use_base_url: bool,
) -> tuple[str, float]:
    compressed_path = _compress_video(src_path, tmp_dir)
    size_mb = _file_size_mb(compressed_path)
    if use_base_url and size_mb >= inline_part_limit_mb:
        compact_path = _compress_video_to_inline_limit(src_path, tmp_dir, inline_part_limit_mb)
        compact_size_mb = _file_size_mb(compact_path)
        if compact_size_mb < size_mb:
            logger.info(
                "gemini_video: inline rescue compression %.1fMB -> %.1fMB (limit=%.1fMB)",
                size_mb, compact_size_mb, inline_part_limit_mb,
            )
            compressed_path = compact_path
            size_mb = compact_size_mb
    return compressed_path, size_mb


def _inline_video_parts(
    primary_path: str,
    connector_path: str | None,
) -> list[types.Part]:
    parts = [
        types.Part(text="[video: primary highlight source]"),
        _video_inline_part(primary_path),
    ]
    if connector_path:
        parts.extend([
            types.Part(text="[video: connector]"),
            _video_inline_part(connector_path),
        ])
    return parts


def _uploaded_video_parts(
    upload_client: genai.Client,
    primary_path: str,
    connector_path: str | None,
) -> list[types.Part]:
    uploaded = _upload_active_file(upload_client, primary_path)
    parts = [
        types.Part(text="[video: primary highlight source]"),
        types.Part(file_data=types.FileData(
            file_uri=uploaded.uri, mime_type="video/mp4",
        )),
    ]
    if connector_path:
        connector_uploaded = _upload_active_file(upload_client, connector_path)
        parts.extend([
            types.Part(text="[video: connector]"),
            types.Part(file_data=types.FileData(
                file_uri=connector_uploaded.uri, mime_type="video/mp4",
            )),
        ])
    return parts


def _analyze_video_blocking(
    video_path: str,
    resolved_key: str,
    resolved_model: str,
    prompt_text: str,
    connector_video_path: str | None = None,
    continuation_type: str = "unspecified",
) -> str:
    """执行视频分析（压缩 → inline / upload → generate_content），返回原始文本。

    P0 优化：无论直连还是中转模式，先压缩视频到 720p。压缩后 < _INLINE_MAX_MB
    则走 base64 inline（跳过 Files API 上传+轮询，省 60-240s）；超过阈值才走
    Files API 兜底。GEMINI_BASE_URL 中转模式下始终走 inline。
    """
    client = make_genai_client(api_key=resolved_key)
    use_base_url = bool(os.getenv("GEMINI_BASE_URL", "").strip())
    inline_limit_mb = _inline_limit_mb(use_base_url)
    part_count = 2 if connector_video_path else 1
    inline_part_limit_mb = max(1.0, inline_limit_mb / part_count)
    generate_client = client

    t_start = time.monotonic()

    # ── P0：先压缩，减小体积再决定上传方式 ──
    tmp_dir = tempfile.mkdtemp(prefix="flowcut_gemini_compress_")
    try:
        compressed_path, compressed_size_mb = _prepare_video_for_request(
            video_path,
            tmp_dir,
            inline_part_limit_mb=inline_part_limit_mb,
            use_base_url=use_base_url,
        )
        connector_compressed: str | None = None
        connector_size_mb = 0.0
        if connector_video_path:
            connector_compressed, connector_size_mb = _prepare_video_for_request(
                connector_video_path,
                tmp_dir,
                inline_part_limit_mb=inline_part_limit_mb,
                use_base_url=use_base_url,
            )
        total_inline_mb = compressed_size_mb + connector_size_mb

        compress_s = time.monotonic() - t_start
        logger.info(
            "gemini_video: prepared in %.1fs, total=%.1fMB, inline_limit=%dMB, base_url=%s",
            compress_s, total_inline_mb, inline_limit_mb, use_base_url,
        )
    except Exception:
        # 压缩彻底失败（极少见），回退到原文件
        shutil.rmtree(tmp_dir, ignore_errors=True)
        compressed_path = video_path
        compressed_size_mb = os.path.getsize(video_path) / 1e6
        connector_compressed = connector_video_path
        connector_size_mb = (
            os.path.getsize(connector_video_path) / 1e6
            if connector_video_path
            else 0.0
        )
        total_inline_mb = compressed_size_mb + connector_size_mb

    # 判断是否 inline：中转模式始终 inline，直连模式看大小
    do_inline = total_inline_mb < inline_limit_mb

    try:
        if do_inline:
            # inline 模式：视频 base64 内联，跳过 Files API
            contents = [
                types.Part(text="【视频1：原视频 / 高光提取对象】"),
                _video_inline_part(compressed_path),
            ]
            if connector_video_path:
                conn_compressed = connector_compressed
                contents.extend([
                    types.Part(text="【视频2：后续衔接视频】"),
                    _video_inline_part(conn_compressed),
                ])
        else:
            # Files API 模式：视频过大，兜底走上传+轮询
            from simpleclaw.llm.genai_client import make_genai_upload_client
            upload_client = make_genai_upload_client(api_key=resolved_key)
            generate_client = upload_client
            uploaded = _upload_active_file(upload_client, compressed_path)
            connector_uploaded = (
                _upload_active_file(upload_client, connector_compressed)
                if connector_compressed
                else None
            )
            contents = [
                types.Part(text="【视频1：原视频 / 高光提取对象】"),
                types.Part(file_data=types.FileData(
                    file_uri=uploaded.uri, mime_type="video/mp4",
                )),
            ]
            if connector_uploaded is not None:
                contents.extend([
                    types.Part(text="【视频2：后续衔接视频】"),
                    types.Part(file_data=types.FileData(
                        file_uri=connector_uploaded.uri, mime_type="video/mp4",
                    )),
                ])
    finally:
        # 压缩临时目录不再需要
        if tmp_dir and os.path.isdir(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)

    role_prompt = (
        f"\n\n## 本次衔接方式\n"
        f"- continuation_type: {continuation_type}\n"
        "- 第一个视频是需要提取高光的原视频。\n"
    )
    if connector_video_path is not None:
        role_prompt += (
            "- 第二个视频是后续衔接视频，请重点判断原视频高光片段的结尾"
            "能否自然切入该衔接视频的开头；如果需要桥接话术，请输出 bridge_text。\n"
        )
    elif continuation_type == "original":
        role_prompt += (
            "- 没有第二个视频。请把原视频开头视为后续衔接对象，"
            "判断高光前置后能否自然接回原片开头。\n"
        )
    else:
        role_prompt += "- 没有第二个视频。请按通用高光前置场景判断。\n"

    contents.append(types.Part(text=prompt_text + role_prompt))

    try:
        response = generate_client.models.generate_content(
            model=resolved_model,
            contents=contents,
            config=types.GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=_MAX_OUTPUT_TOKENS,
            ),
        )
    except Exception as exc:
        if do_inline and _is_request_too_large(exc):
            raise RuntimeError(
                "Gemini inline request is still too large after compression "
                f"(prepared_total={total_inline_mb:.1f}MB, inline_limit={inline_limit_mb}MB). "
                "Lower GEMINI_BASE_URL_INLINE_MAX_MB or configure "
                "GOOGLE_API_KEY_DIRECT/GEMINI_PROXY so large videos can use Files API."
            ) from exc
        raise

    return response.text or ""


_START_PICK_PROMPT = """\
你是短剧高光投放的剪辑助手。下面是一段【已按镜头拆好】的视频分镜列表（来自一部短剧的前几集合并），\
每个分镜含 idx、时间区间、画面描述 visual、台词 copy。

请从中挑选最适合作为短视频【开头高光起点】的分镜，按适合度从高到低排序，返回前 {top_n} 个。

好的高光起点应满足：
- 开头几秒就有明确冲突 / 危机 / 悬念 / 强情绪，能在 3 秒内抓住观众。
- 不强依赖前情，没看过前面也能看懂基本冲突。
- 是一个自然的镜头/场景开始处，不是某句台词或某个动作的中间。
- 避免纯铺垫、纯环境空镜、纯人物出场而无冲突的分镜。

**必须排除的片段（即使看起来画面精彩）：**
- 片头/OP/Logo：包含剧名标题、出品信息、制作团队字幕、演职员表的片段。
- 纯片头音乐/空镜：无对白、只有背景音乐和标题画面的片段。
- 前置介绍字幕：角色介绍字幕、设定说明文字等非剧情内容。
- copy 字段为空或极短（<3字）的片段，除非紧接着有密集对白的冲突场景。
- 第一段有对白的段之前的任何内容：如果前几段 copy 全为空而 visual 是标题/空镜/环境铺垫，
  说明这是片头区域，绝对不能选。

只输出严格 JSON 数组，不要任何解释或 markdown：
[{{"idx": <分镜idx>, "hook_strength": <0-10 吸引力打分>, "reason": "<为什么适合做起点，具体到画面/台词/冲突>"}}]

分镜列表：
{shots_json}
"""


def _generate_text_blocking(
    prompt_text: str, resolved_key: str, resolved_model: str,
) -> str:
    """同步：纯文本 generate_content（不上传视频），返回原始文本。"""
    client = make_genai_client(api_key=resolved_key)
    response = client.models.generate_content(
        model=resolved_model,
        contents=[types.Part(text=prompt_text)],
        config=types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=_MAX_OUTPUT_TOKENS,
        ),
    )
    return response.text or ""


async def select_start_shots(
    shots: list[dict],
    top_n: int = 3,
    *,
    api_key: str | None = None,
    model: str | None = None,
    timeout_s: float | None = None,
) -> list[dict]:
    """把已拆好的分镜列表（纯文本）交给 Gemini，专门挑出最适合做高光起点的分镜。

    这是「先拆镜、再判起点」两步流程里的第二步：不上传视频，只对文本分镜推理。
    返回 [{idx, hook_strength, reason}]，idx 指向传入 shots 的下标，最多 top_n 条。
    """
    if not shots:
        return []
    resolved_key = api_key or os.environ["GOOGLE_API_KEY"]
    resolved_model = (
        model
        or os.getenv("FLOWCUT_HIGHLIGHT_SELECT_MODEL")
        or os.getenv("FLOWCUT_DECOMPOSE_MODEL")
        or _DEFAULT_MODEL
    )
    resolved_timeout = timeout_s if timeout_s is not None else _DEFAULT_TIMEOUT_S
    capped = max(1, int(top_n))
    compact = [
        {
            "idx": i,
            "start_time": round(float(s.get("start_time") or 0.0), 2),
            "end_time": round(float(s.get("end_time") or 0.0), 2),
            "visual": str(s.get("visual") or "")[:120],
            "copy": str(s.get("copy") or "")[:120],
        }
        for i, s in enumerate(shots)
    ]
    prompt = _START_PICK_PROMPT.format(
        top_n=capped, shots_json=json.dumps(compact, ensure_ascii=False),
    )
    coro = asyncio.to_thread(
        _generate_text_blocking, prompt, resolved_key, resolved_model,
    )
    if resolved_timeout is not None:
        raw = await asyncio.wait_for(coro, timeout=resolved_timeout)
    else:
        raw = await coro
    payload = _parse_json_payload(raw)
    if not isinstance(payload, list):
        return []
    out: list[dict] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item["idx"])
        except (KeyError, TypeError, ValueError):
            continue
        if idx < 0 or idx >= len(shots):
            continue
        out.append({
            "idx": idx,
            "hook_strength": float(item.get("hook_strength") or 0.0),
            "reason": str(item.get("reason") or ""),
        })
        if len(out) >= capped:
            break
    return out


async def analyze_video(
    video_path: str,
    *,
    api_key: str | None = None,
    model: str | None = None,
    timeout_s: float | None = None,
    prompt_text: str | None = None,
    preserve_extra: bool = False,
) -> list[dict]:
    """上传视频到 Gemini Files API，调用多模态模型，返回拆镜段落列表。

    阻塞 SDK 调用全部下放到线程池，并由 asyncio.wait_for 兜底硬超时
    （默认 _DEFAULT_TIMEOUT_S，可通过 FLOWCUT_GEMINI_TIMEOUT_S 覆盖）。

    Args:
        video_path: 本地视频文件路径。
        api_key: Google API Key，默认读 GOOGLE_API_KEY 环境变量。
        model: 模型名称，默认 gemini-3.1-flash-lite-preview（或 FLOWCUT_DECOMPOSE_MODEL 覆盖）。
        timeout_s: 整体硬超时秒数，None 表示用默认值。

    Returns:
        [{start_time: float, end_time: float, visual: str, copy: str, category: str}] 列表，可能为空。

    Raises:
        asyncio.TimeoutError: 整体超过 timeout_s 仍未完成。
    """
    resolved_key = api_key or os.environ["GOOGLE_API_KEY"]
    resolved_model = model or os.getenv("FLOWCUT_DECOMPOSE_MODEL", _DEFAULT_MODEL)
    resolved_timeout = timeout_s if timeout_s is not None else _DEFAULT_TIMEOUT_S
    resolved_prompt = prompt_text or _DECOMPOSE_PROMPT

    coro = asyncio.to_thread(
        _analyze_video_blocking,
        video_path,
        resolved_key,
        resolved_model,
        resolved_prompt,
    )
    if resolved_timeout is not None:
        raw_text = await asyncio.wait_for(coro, timeout=resolved_timeout)
    else:
        raw_text = await coro
    payload = _parse_json_payload(raw_text)
    return _parse_segments_from_payload(payload, preserve_extra=preserve_extra)


async def analyze_highlight_video(
    video_path: str,
    *,
    connector_video_path: str | None = None,
    continuation_type: str = "unspecified",
    api_key: str | None = None,
    model: str | None = None,
    timeout_s: float | None = None,
) -> dict:
    """使用 highlight/base_prompt.md 分析视频，返回完整高光分析 JSON。

    返回值至少包含 `segments`。如果模型返回非对象 JSON，则降级为
    {"segments": [...]}。
    """
    resolved_key = api_key or os.environ["GOOGLE_API_KEY"]
    resolved_model = model or os.getenv("FLOWCUT_DECOMPOSE_MODEL", _DEFAULT_MODEL)
    resolved_timeout = timeout_s if timeout_s is not None else _DEFAULT_TIMEOUT_S
    coro = asyncio.to_thread(
        _analyze_video_blocking,
        video_path,
        resolved_key,
        resolved_model,
        load_highlight_prompt(),
        connector_video_path,
        continuation_type,
    )
    if resolved_timeout is not None:
        raw_text = await asyncio.wait_for(coro, timeout=resolved_timeout)
    else:
        raw_text = await coro
    payload = _parse_json_payload(raw_text)
    segments = _parse_segments_from_payload(payload, preserve_extra=True)
    if isinstance(payload, dict):
        return {**payload, "segments": segments}
    return {"segments": segments}
