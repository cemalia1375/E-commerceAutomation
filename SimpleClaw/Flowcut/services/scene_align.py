"""PySceneDetect 场景切点检测 + LLM 时间戳对齐。"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

_ALIGN_WINDOW_S = 1.0          # 空镜段匹配窗口：±1 秒
_TALK_FORWARD_WINDOW_S = 2.0   # 口播段往后顺延找句末切点的窗口
_MIN_DURATION_S = 0.5          # 最短段时长


def _find_nearest_cut(target: float, cuts: list[float]) -> float | None:
    """在 cuts 中找 ±ALIGN_WINDOW_S 内绝对距离最近的切点。

    用于空镜/产品展示段（无口播）：画面边界优先，不区分前后取最近切点。
    """
    candidates = [c for c in cuts if abs(c - target) <= _ALIGN_WINDOW_S]
    if not candidates:
        return None
    return min(candidates, key=lambda c: abs(c - target))


def _find_forward_cut(target: float, cuts: list[float]) -> float | None:
    """用于口播段：优先取 >= target 的最近切点，顺延到句末后的画面切点。

    Gemini 整秒 end 常早于音频里句子真正说完的时刻，若吸附到更近但更早的
    画面切点会把口播拦腰截断。故口播段优先往后顺延到 [target, target+窗口]
    内第一个画面切点（句子说完后才切），窗口内无切点时返回 None 保留语义 end。
    """
    forward = [c for c in cuts if target <= c <= target + _TALK_FORWARD_WINDOW_S]
    if forward:
        return min(forward)
    # 窗口内无前向切点：保留语义 end，不往前吸附（句子完整优先于画面边界）。
    return None


def align_timestamps(
    segments: list[dict],
    cuts: list[float],
) -> list[dict]:
    """将 LLM 返回的段落时间戳吸附到物理切点，并做后处理规范化。

    Args:
        segments: LLM 输出，每项含 start_time / end_time / content。
        cuts: PySceneDetect 检测到的切点秒数列表（已排序）。

    Returns:
        新的 segments 列表，时间戳修正后不可变副本。
    """
    if not segments:
        return []

    result: list[dict] = []
    for seg in segments:
        start = float(seg.get("start_time", 0.0))
        end = float(seg.get("end_time", start + 1.0))

        # 口播段（copy 非空）顺延到句末后的切点；空镜段取绝对最近切点。
        has_speech = bool((seg.get("copy") or "").strip())
        if cuts:
            snapped_end = (
                _find_forward_cut(end, cuts)
                if has_speech
                else _find_nearest_cut(end, cuts)
            )
        else:
            snapped_end = None
        new_end = snapped_end if snapped_end is not None else end

        # 保留 Gemini 输出的所有字段（visual/copy/category 等），仅覆盖时间戳
        result.append({**seg, "start_time": start, "end_time": new_end})

    # 后处理 1：首段强制从 0 开始
    result[0] = {**result[0], "start_time": 0.0}

    # 后处理 2：后段 start 与前段 end 对齐，保证段落无缝衔接
    for i in range(1, len(result)):
        prev_end = result[i - 1]["end_time"]
        result[i] = {**result[i], "start_time": prev_end}

    # 后处理 3：最小持续时间 0.5s
    for i, seg in enumerate(result):
        if seg["end_time"] - seg["start_time"] < _MIN_DURATION_S:
            new_end = seg["start_time"] + _MIN_DURATION_S
            for c in cuts:
                if c >= new_end:
                    new_end = c
                    break
            result[i] = {**result[i], "end_time": new_end}

    return result


def _run_scene_detect(video_path: str, threshold: float = 27.0) -> list[float]:
    """同步：对本地视频文件运行 PySceneDetect，返回切点秒数列表。"""
    import os as _os
    from scenedetect import open_video, SceneManager
    from scenedetect.backends import AVAILABLE_BACKENDS
    from scenedetect.detectors import ContentDetector

    if not _os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")
    file_size = _os.path.getsize(video_path)
    if file_size == 0:
        raise RuntimeError(f"Video file is empty (0 bytes): {video_path}")

    backend = "pyav" if "pyav" in AVAILABLE_BACKENDS else "opencv"
    try:
        video = open_video(video_path, backend=backend)
    except Exception as exc:
        raise RuntimeError(
            f"{backend} cannot open video: {video_path} (size={file_size}). "
            f"Check codec support: {exc}"
        ) from exc

    manager = SceneManager()
    manager.add_detector(ContentDetector(threshold=threshold))
    manager.detect_scenes(video, show_progress=False)
    scenes = manager.get_scene_list()

    cuts: list[float] = [0.0]
    for _, end in scenes:
        cuts.append(end.seconds)
    return cuts


async def detect_scene_cuts(video_path: str, threshold: float = 27.0) -> list[float]:
    """异步包装：在线程池中运行 PySceneDetect，返回切点秒数列表。"""
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=1) as pool:
        return await loop.run_in_executor(pool, _run_scene_detect, video_path, threshold)
