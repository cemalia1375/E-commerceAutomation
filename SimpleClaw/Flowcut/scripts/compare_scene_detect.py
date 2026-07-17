#!/usr/bin/env python3
"""对比 PySceneDetect opencv vs pyav 后端的拆镜效果。

流程：
  1. 合并指定目录前3集（ffmpeg concat，-c copy 快速拼接）
  2. Gemini 多模态拆镜（语义分段 + visual/copy）
  3. 分别用 opencv / pyav 后端跑 PySceneDetect 取物理切点
  4. 用 align_timestamps 把 Gemini 语义段吸附到物理切点
  5. 导出每个段落的视频片段到 output_dir/opencv/ 和 output_dir/pyav/
  6. 打印对比报告

Usage（从 SimpleClaw/ 目录运行）：
    uv run python -m Flowcut.scripts.compare_scene_detect \\
        --episodes-dir "/Users/.../被儿媳逼相亲，我成了设备维修天花板" \\
        --output-dir /tmp/scene_compare

Dependencies:
    scenedetect[opencv-headless]  （opencv 后端，已在 requirements.txt）
    av                            （pyav 后端，uv pip install av）
    GOOGLE_API_KEY 环境变量
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# ── 路径修正：允许直接运行也允许 -m 运行 ─────────────────────────────────
_SIMPLECLAW_ROOT = Path(__file__).resolve().parents[2]
if str(_SIMPLECLAW_ROOT) not in sys.path:
    sys.path.insert(0, str(_SIMPLECLAW_ROOT))

from Flowcut.services.gemini_video import analyze_video
from Flowcut.services.scene_align import align_timestamps

# ── 常量 ─────────────────────────────────────────────────────────────────
_EPISODE_NAMES = ["第1集.mp4", "第2集.mp4", "第3集.mp4"]
_DEFAULT_THRESHOLD = 27.0


# ── FFmpeg 工具 ───────────────────────────────────────────────────────────

def _run_ffmpeg(args: list[str], *, timeout: int = 1800) -> None:
    result = subprocess.run(
        ["ffmpeg", "-y", *args],
        capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{result.stderr.strip()}")


def _merge_episodes(episode_paths: list[Path], output_path: Path) -> None:
    """归一化每集到统一分辨率后再合并。

    不能直接 -c copy 合并：各集分辨率/帧率可能不一致（如 720p+1080p 混合），
    -c copy 会产出一个分辨率中途突变的畸形 mp4，导致 PySceneDetect 在突变段
    完全检测不到切点、突变后时间戳错乱。先逐集重编码到 720×1280 / 30fps，
    再 concat copy。
    """
    norm_paths: list[Path] = []
    for i, p in enumerate(episode_paths):
        norm = output_path.parent / f"norm_ep{i}.mp4"
        print(f"[merge] 归一化 {p.name} → 720x1280/30fps ...")
        _run_ffmpeg([
            "-i", str(p),
            "-vf",
            "scale=720:1280:force_original_aspect_ratio=decrease,"
            "pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1",
            "-r", "30",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-c:a", "aac", "-ar", "44100", "-ac", "2",
            str(norm),
        ])
        norm_paths.append(norm)

    concat_list = output_path.parent / "concat_list.txt"
    with open(concat_list, "w", encoding="utf-8") as f:
        for p in norm_paths:
            safe = str(p).replace("'", "'\\''")
            f.write(f"file '{safe}'\n")
    print(f"[merge] 合并 {len(norm_paths)} 集 → {output_path.name} ...")
    _run_ffmpeg([
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ])
    print(f"[merge] 完成 ({output_path.stat().st_size / 1024 / 1024:.1f} MB)")


def _cut_clip(src: Path, dst: Path, start: float, end: float) -> None:
    """精确裁剪：重编码到帧级精度。

    不能用 -c copy：copy 只能在关键帧切，-t 结束点会延伸到下一个关键帧，
    把零点几秒的下一镜头开头带进片段末尾。重编码才能精确切到 [start, end)。
    """
    duration = max(0.1, end - start)
    _run_ffmpeg([
        "-ss", f"{max(0.0, start):.3f}",
        "-i", str(src),
        "-t", f"{duration:.3f}",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "18",
        "-c:a", "aac",
        "-movflags", "+faststart",
        str(dst),
    ], timeout=300)


# ── PySceneDetect ─────────────────────────────────────────────────────────

def _detect_cuts(video_path: Path, backend: str, threshold: float) -> list[float]:
    """用指定后端跑 PySceneDetect，返回切点秒数列表（含 0.0）。"""
    from scenedetect import open_video, SceneManager
    from scenedetect.detectors import ContentDetector

    print(f"[scenedetect/{backend}] 检测切点（threshold={threshold}）...")
    t0 = time.time()
    video = open_video(str(video_path), backend=backend)
    manager = SceneManager()
    manager.add_detector(ContentDetector(threshold=threshold))
    manager.detect_scenes(video, show_progress=False)
    scenes = manager.get_scene_list()

    cuts: list[float] = [0.0]
    for _, end in scenes:
        cuts.append(end.seconds)
    elapsed = time.time() - t0
    print(f"[scenedetect/{backend}] {len(cuts)} 切点，耗时 {elapsed:.1f}s")
    return cuts


# ── 导出片段 ──────────────────────────────────────────────────────────────

def _export_segments(
    merged: Path,
    segments: list[dict],
    out_dir: Path,
    label: str,
) -> None:
    """按对齐后的 segments 裁出片段，写入 out_dir。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[export/{label}] 导出 {len(segments)} 个片段 → {out_dir} ...")
    for i, seg in enumerate(segments):
        start = float(seg.get("start_time", 0))
        end = float(seg.get("end_time", start + 1))
        dst = out_dir / f"seg_{i:03d}_{start:.1f}s-{end:.1f}s.mp4"
        try:
            _cut_clip(merged, dst, start, end)
        except Exception as exc:
            print(f"  [!] seg {i} 裁剪失败: {exc}")
    print(f"[export/{label}] 完成")


# ── 报告 ──────────────────────────────────────────────────────────────────

def _print_report(
    gemini_segments: list[dict],
    opencv_aligned: list[dict],
    pyav_aligned: list[dict],
    opencv_cuts: list[float],
    pyav_cuts: list[float],
) -> str:
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("拆镜对比报告")
    lines.append("=" * 72)
    lines.append(f"Gemini 语义段数：{len(gemini_segments)}")
    lines.append(f"OpenCV 物理切点数：{len(opencv_cuts)}")
    lines.append(f"pyav   物理切点数：{len(pyav_cuts)}")
    lines.append("")
    lines.append(f"{'idx':>4}  {'Gemini 原始':>18}  {'OpenCV 对齐':>18}  {'pyav 对齐':>18}  {'差值(pyav-opencv)':>18}")
    lines.append("-" * 82)
    for i in range(max(len(opencv_aligned), len(pyav_aligned))):
        oc = pyav_aligned[i] if i < len(pyav_aligned) else None
        cv = opencv_aligned[i] if i < len(opencv_aligned) else None
        gm = gemini_segments[i] if i < len(gemini_segments) else None

        gm_s = f"{gm['start_time']:.2f}-{gm['end_time']:.2f}" if gm else "—"
        cv_s = f"{cv['start_time']:.2f}-{cv['end_time']:.2f}" if cv else "—"
        pv_s = f"{oc['start_time']:.2f}-{oc['end_time']:.2f}" if oc else "—"

        if cv and oc:
            diff_start = oc["start_time"] - cv["start_time"]
            diff_end = oc["end_time"] - cv["end_time"]
            diff_s = f"Δstart={diff_start:+.2f} Δend={diff_end:+.2f}"
        else:
            diff_s = "—"

        lines.append(f"{i:>4}  {gm_s:>18}  {cv_s:>18}  {pv_s:>18}  {diff_s}")

    lines.append("")
    lines.append("切点差异（opencv vs pyav）：")
    min_len = min(len(opencv_cuts), len(pyav_cuts))
    diffs = [abs(pyav_cuts[j] - opencv_cuts[j]) for j in range(min_len)]
    if diffs:
        lines.append(f"  最大偏差：{max(diffs):.3f}s  平均偏差：{sum(diffs)/len(diffs):.3f}s")
    lines.append("=" * 72)

    report = "\n".join(lines)
    print(report)
    return report


# ── 主流程 ────────────────────────────────────────────────────────────────

async def _main(episodes_dir: Path, output_dir: Path, threshold: float) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. 找前3集
    episodes: list[Path] = []
    for name in _EPISODE_NAMES:
        p = episodes_dir / name
        if not p.exists():
            raise FileNotFoundError(f"找不到：{p}")
        episodes.append(p)
    print(f"[setup] 前3集：{[e.name for e in episodes]}")

    # 2. 合并
    merged = output_dir / "merged_ep1-3.mp4"
    if not merged.exists():
        _merge_episodes(episodes, merged)
    else:
        print(f"[merge] 已存在，跳过：{merged.name}")

    # 3. Gemini 拆镜 + opencv 切点（并行）
    print("[gemini] 上传视频，等待 Gemini 拆镜...")
    t0 = time.time()
    gemini_task = asyncio.create_task(analyze_video(str(merged)))

    import concurrent.futures
    loop = asyncio.get_running_loop()
    opencv_future = loop.run_in_executor(
        concurrent.futures.ThreadPoolExecutor(max_workers=1),
        _detect_cuts, merged, "opencv", threshold,
    )

    gemini_segments, opencv_cuts = await asyncio.gather(gemini_task, opencv_future)
    print(f"[gemini] {len(gemini_segments)} 段，Gemini 耗时 {time.time()-t0:.1f}s")

    # 4. pyav 切点
    pyav_cuts = await loop.run_in_executor(None, _detect_cuts, merged, "pyav", threshold)

    # 5. 对齐
    opencv_aligned = align_timestamps(list(gemini_segments), opencv_cuts)
    pyav_aligned = align_timestamps(list(gemini_segments), pyav_cuts)

    # 6. 保存 JSON
    (output_dir / "gemini_segments.json").write_text(
        json.dumps(gemini_segments, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "opencv_cuts.json").write_text(
        json.dumps(opencv_cuts, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "pyav_cuts.json").write_text(
        json.dumps(pyav_cuts, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "opencv_aligned.json").write_text(
        json.dumps(opencv_aligned, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "pyav_aligned.json").write_text(
        json.dumps(pyav_aligned, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 7. 导出片段
    _export_segments(merged, opencv_aligned, output_dir / "opencv", "opencv")
    _export_segments(merged, pyav_aligned, output_dir / "pyav", "pyav")

    # 8. 报告
    report = _print_report(
        gemini_segments, opencv_aligned, pyav_aligned, opencv_cuts, pyav_cuts,
    )
    (output_dir / "comparison_report.txt").write_text(report, encoding="utf-8")
    print(f"\n[done] 输出目录：{output_dir}")
    print(f"       opencv 片段：{output_dir / 'opencv'}")
    print(f"       pyav   片段：{output_dir / 'pyav'}")
    print(f"       对比报告：  {output_dir / 'comparison_report.txt'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="对比 opencv vs pyav 拆镜效果")
    parser.add_argument(
        "--episodes-dir", "-i",
        default="/Users/shengxingou-1/Downloads/近期跑量+原剧/原剧/被儿媳逼相亲，我成了设备维修天花板",
        help="包含第1集.mp4 等文件的目录",
    )
    parser.add_argument(
        "--output-dir", "-o",
        default="/tmp/scene_compare",
        help="输出目录",
    )
    parser.add_argument(
        "--threshold", "-t",
        type=float, default=_DEFAULT_THRESHOLD,
        help=f"PySceneDetect ContentDetector 阈值（默认 {_DEFAULT_THRESHOLD}）",
    )
    args = parser.parse_args()

    if not os.environ.get("GOOGLE_API_KEY"):
        print("[error] 请先设置 GOOGLE_API_KEY 环境变量", file=sys.stderr)
        sys.exit(1)

    asyncio.run(_main(
        Path(args.episodes_dir),
        Path(args.output_dir),
        args.threshold,
    ))


if __name__ == "__main__":
    main()
