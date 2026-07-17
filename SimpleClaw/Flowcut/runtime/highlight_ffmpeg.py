"""Shared FFmpeg helper functions for FlowCut executors.

Extracted from executors.py to be reused across highlight batch pipeline stages.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess


def ffmpeg_path() -> str:
    env_path = (
        os.environ.get("FLOWCUT_FFMPEG_PATH", "").strip()
        or os.environ.get("FFMPEG_PATH", "").strip()
    )
    if env_path:
        if os.path.isabs(env_path):
            return env_path
        resolved = shutil.which(env_path)
        if resolved:
            return resolved
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg
    bundled = Path(__file__).resolve().parents[3] / "flowcut_frontend" / "ffmpeg.exe"
    if bundled.exists():
        return str(bundled)
    raise RuntimeError(
        "ffmpeg not found. Set FLOWCUT_FFMPEG_PATH or keep "
        "flowcut_frontend/ffmpeg.exe in the repository."
    )


def ffprobe_path() -> str | None:
    env_path = (
        os.environ.get("FLOWCUT_FFPROBE_PATH", "").strip()
        or os.environ.get("FFPROBE_PATH", "").strip()
    )
    if env_path:
        if os.path.isabs(env_path):
            return env_path
        resolved = shutil.which(env_path)
        if resolved:
            return resolved
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        return ffprobe
    try:
        sibling = Path(ffmpeg_path()).with_name("ffprobe.exe")
        if sibling.exists():
            return str(sibling)
    except RuntimeError:
        pass
    bundled = Path(__file__).resolve().parents[3] / "flowcut_frontend" / "ffprobe.exe"
    if bundled.exists():
        return str(bundled)
    return None


def run_ffmpeg(args: list[str], *, timeout: int = 900) -> None:
    result = subprocess.run(
        [ffmpeg_path(), "-y", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffmpeg failed")


def cut_clip(source_path: str, output_path: str, start: float, end: float) -> None:
    """Cut a clip from source_path between [start, end] seconds, re-encode to libx264."""
    duration = max(0.1, end - start)
    run_ffmpeg(
        [
            "-ss", f"{max(0.0, start):.3f}",
            "-i", source_path,
            "-t", f"{duration:.3f}",
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1",
            "-r", "30",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "18",
            "-c:a", "aac",
            "-ar", "44100",
            "-ac", "2",
            "-movflags", "+faststart",
            output_path,
        ]
    )


def normalize_clip(source_path: str, output_path: str) -> None:
    """Normalize to 720x1280 vertical, 30fps libx264."""
    run_ffmpeg(
        [
            "-i", source_path,
            "-vf",
            "scale=720:1280:force_original_aspect_ratio=decrease,"
            "pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1",
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


def normalize_with_overlay(
    source_path: str, overlay_path: str, output_path: str
) -> None:
    """Overlay a PNG onto source video, output normalized mp4."""
    filter_complex = (
        "[1:v]format=rgba[ovr];"
        "[0:v]scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1[base];"
        "[ovr][base]scale2ref[ovr_s][base2];"
        "[base2][ovr_s]overlay=0:0[outv]"
    )
    run_ffmpeg(
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


def write_concat_list(path: str, files: list[str]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for file_path in files:
            safe = file_path.replace("'", "'\\''")
            f.write(f"file '{safe}'\n")


def concat_clips(concat_list_path: str, output_path: str) -> None:
    run_ffmpeg(
        [
            "-f", "concat",
            "-safe", "0",
            "-i", concat_list_path,
            "-c", "copy",
            "-movflags", "+faststart",
            output_path,
        ],
        timeout=1800,
    )


def _parse_ffmpeg_duration(text: str) -> float:
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if not match:
        return 0.0
    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = float(match.group(3))
    return hours * 3600 + minutes * 60 + seconds


def probe_duration_seconds(video_path: str) -> float:
    """Probe video duration in seconds.

    Prefer ffprobe when available. The packaged Windows app currently ships
    ffmpeg.exe but not always ffprobe.exe, so fall back to parsing ffmpeg -i.
    """
    probe = ffprobe_path()
    if probe:
        try:
            result = subprocess.run(
                [
                    probe, "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    video_path,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            result = None
        if result is not None and result.returncode == 0:
            raw = (result.stdout or "").strip()
            if raw:
                try:
                    duration = float(raw)
                    if duration > 0:
                        return duration
                except ValueError:
                    pass

    try:
        result = subprocess.run(
            [
                ffmpeg_path(),
                "-i",
                video_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return 0.0
    return _parse_ffmpeg_duration((result.stderr or "") + "\n" + (result.stdout or ""))
