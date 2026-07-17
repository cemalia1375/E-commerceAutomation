"""集成测试：本地视频拆镜 — 真实 Gemini + PySceneDetect + FFmpeg，输出保留在本地。

用法：
    # 需要 GOOGLE_API_KEY 环境变量
    uv run pytest tests/test_scene_decompose_local.py -v -s --video /path/to/your.mp4

    # 指定输出目录（默认 /tmp/scene_decompose_out）
    uv run pytest tests/test_scene_decompose_local.py -v -s \
        --video /path/to/your.mp4 \
        --out-dir /tmp/my_clips

标记为 external，不在普通 CI 中运行：
    uv run pytest -m "not external"  # 跳过本测试
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv()

from Flowcut.services.gemini_video import analyze_video
from Flowcut.services.scene_align import align_timestamps, detect_scene_cuts


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def video_path(request: pytest.FixtureRequest) -> str:
    path = request.config.getoption("--video")
    if not path:
        pytest.skip("需要 --video 参数指定本地视频文件")
    if not Path(path).exists():
        pytest.fail(f"视频文件不存在: {path}")
    return str(path)


@pytest.fixture
def out_dir(request: pytest.FixtureRequest) -> Path:
    d = Path(request.config.getoption("--out-dir"))
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    return d


# ── 工具函数 ──────────────────────────────────────────────────────────────────

def _ffmpeg_cut(src: str, start: float, duration: float, dst: str) -> None:
    # Re-encode instead of -c copy: copy can only cut at keyframe boundaries,
    # leaving a few frames of the previous scene at the clip head.
    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-i", src,
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "fast",
            "-c:a", "aac",
            dst,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg cut failed: {result.stderr.strip()}")


def _extract_cover(src: str, at: float, dst: str) -> None:
    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-ss", str(at),
            "-i", src,
            "-frames:v", "1",
            "-q:v", "2",
            dst,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg cover failed: {result.stderr.strip()}")


def _save_clips_locally(
    video_path: str,
    segments: list[dict],
    out_dir: Path,
) -> list[dict]:
    """将每个 segment 用 FFmpeg 切出，保存到 out_dir，返回带本地路径的 segment 列表。"""
    ext = Path(video_path).suffix or ".mp4"
    results: list[dict] = []

    for i, seg in enumerate(segments):
        start = float(seg["start_time"])
        end = float(seg["end_time"])
        duration = round(end - start, 3)
        content = seg.get("content", "")
        category = seg.get("category", "")

        clip_name = f"clip_{i:02d}_{round(start)}s-{round(end)}s{ext}"
        cover_name = f"clip_{i:02d}_{round(start)}s-{round(end)}s.jpg"
        clip_path = str(out_dir / clip_name)
        cover_path = str(out_dir / cover_name)

        _ffmpeg_cut(video_path, start, duration, clip_path)
        cover_at = min(0.5, duration / 2)
        _extract_cover(clip_path, cover_at, cover_path)

        results.append({
            **seg,
            "duration": duration,
            "clip_file": clip_name,
            "cover_file": cover_name,
        })

    return results


def _print_summary(segments: list[dict], cuts: list[float], out_dir: Path) -> None:
    print(f"\n{'='*60}")
    print(f"输出目录: {out_dir}")
    print(f"PySceneDetect 物理切点 ({len(cuts)} 个): {[round(c, 2) for c in cuts]}")
    print(f"最终段落数: {len(segments)}")
    print(f"{'='*60}")
    for i, seg in enumerate(segments):
        print(
            f"  [{i:02d}] {seg['start_time']:.2f}s → {seg['end_time']:.2f}s "
            f"({seg['duration']:.2f}s)  [{seg.get('category', '')}]"
        )
        print(f"       {seg.get('content', '')[:80]}")
        print(f"       → {seg.get('clip_file', '')}")
    print(f"{'='*60}\n")


# ── 测试主体 ──────────────────────────────────────────────────────────────────

@pytest.mark.external
@pytest.mark.asyncio
async def test_scene_decompose_local(video_path: str, out_dir: Path) -> None:
    """完整拆镜链路：Gemini 语义分析 + PySceneDetect 对齐 + FFmpeg 切条，全部保留本地。"""
    assert os.environ.get("GOOGLE_API_KEY"), "需要设置 GOOGLE_API_KEY 环境变量"

    # 1. 并行执行 Gemini 分析 + PySceneDetect
    gemini_task = asyncio.create_task(analyze_video(video_path))
    cuts_task = asyncio.create_task(detect_scene_cuts(video_path))
    segments, cuts = await asyncio.gather(gemini_task, cuts_task)

    assert segments, "Gemini 返回了空段落，可能是视频格式不支持或 API 调用失败"

    # 2. 时间戳对齐
    aligned = align_timestamps(segments, cuts)

    assert aligned, "对齐后段落为空"
    assert aligned[0]["start_time"] == 0.0, "首段必须从 0 开始"
    for i in range(1, len(aligned)):
        assert aligned[i]["start_time"] == aligned[i - 1]["end_time"], (
            f"段落 {i-1} 和 {i} 之间存在空隙或重叠"
        )
    for seg in aligned:
        assert seg["end_time"] - seg["start_time"] >= 0.5, (
            f"段落时长不足 0.5s: {seg}"
        )

    # 3. FFmpeg 切条，输出到本地
    loop = asyncio.get_running_loop()
    result_segments = await loop.run_in_executor(
        None, _save_clips_locally, video_path, aligned, out_dir
    )

    # 4. 验证切片文件确实存在
    for seg in result_segments:
        clip_path = out_dir / seg["clip_file"]
        cover_path = out_dir / seg["cover_file"]
        assert clip_path.exists(), f"切片文件不存在: {clip_path}"
        assert clip_path.stat().st_size > 0, f"切片文件为空: {clip_path}"
        assert cover_path.exists(), f"封面文件不存在: {cover_path}"

    # 5. 打印可读摘要供人工检查
    _print_summary(result_segments, cuts, out_dir)

    print(f"✓ 共生成 {len(result_segments)} 个切片，已保存到 {out_dir}")
