# Scene Decompose Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现爆款视频拆镜全链路：Gemini 多模态语义粗分段 + PySceneDetect 时间戳修正，将结果写入 `fc_material.scene_data_json`，打通 `decompose_video` durable tool 与后台 executor。

**Architecture:** `DecomposeVideoTool.prepare_task()` 构造 TaskEnvelope 入队 → `SCENE_DECOMPOSE` worker 拉取 → executor 下载视频 → 并行调用 Gemini Files API 分析 + PySceneDetect 检测切点 → 时间对齐后写回 `fc_material.scene_data_json`。Gemini 调用在 `Flowcut/services/gemini_video.py` 中独立封装（直接用 google-genai SDK，非 GeminiLLM chat 路径），场景对齐纯函数在 `Flowcut/services/scene_align.py`。

**Tech Stack:** Python 3.11+, google-genai SDK, PySceneDetect (`scenedetect[opencv-headless]`), asyncio, aiohttp, existing `MaterialRepository`

---

## File Map

| 操作 | 路径 | 职责 |
|------|------|------|
| 新建 | `Flowcut/services/gemini_video.py` | 上传视频到 Gemini Files API，调模型，解析 JSON segments |
| 新建 | `Flowcut/services/scene_align.py` | PySceneDetect 检测切点，对齐时间戳，后处理规范化 |
| 修改 | `Flowcut/storage/database.py` | ensure_schema 加 `scene_data_json` 列迁移 |
| 修改 | `Flowcut/storage/material_repo.py` | `update_status()` 新增 `scene_data` 参数 |
| 修改 | `Flowcut/runtime/executors.py` | 实现 `make_scene_decompose_executor()` |
| 修改 | `Flowcut/tools/decompose_video.py` | 实现 `prepare_task()` |
| 修改 | `Flowcut/runtime/worker.py` | 将 scene_decompose executor 注册到 worker |
| 修改 | `requirements.txt` | 新增 `scenedetect[opencv-headless]` |
| 新建 | `tests/test_scene_align.py` | scene_align 纯函数单元测试 |
| 新建 | `tests/test_gemini_video_parse.py` | gemini_video JSON 解析逻辑单元测试 |
| 新建 | `tests/test_scene_decompose_executor.py` | executor 集成单元测试（mock Gemini + mock scenedetect + mock repo） |

---

## Task 1: 依赖与 DB Schema

**Files:**
- Modify: `requirements.txt`
- Modify: `SimpleClaw/Flowcut/storage/database.py`

- [ ] **Step 1: 在 requirements.txt 加 scenedetect**

在 `requirements.txt` 的 `# 工具库` 段之后增加一行：

```
scenedetect[opencv-headless]>=0.6.4
```

- [ ] **Step 2: 在 ensure_schema 末尾加列迁移**

在 `SimpleClaw/Flowcut/storage/database.py` 的 `ensure_schema` 函数末尾（`for sql in statements` 循环之后），在最后一个 `ALTER TABLE` 迁移块之后追加：

```python
            # 迁移：给 fc_material 补 scene_data_json（拆镜结果）
            await cur.execute(
                """
                SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME   = 'fc_material'
                  AND COLUMN_NAME  = 'scene_data_json'
                """
            )
            row = await cur.fetchone()
            if row and row[0] == 0:
                await cur.execute(
                    """
                    ALTER TABLE fc_material
                    ADD COLUMN scene_data_json JSON NULL AFTER transcript
                    """
                )
```

- [ ] **Step 3: 安装依赖并验证**

```bash
cd SimpleClaw
uv pip install -r requirements.txt
python -c "import scenedetect; print('scenedetect OK:', scenedetect.__version__)"
```

Expected: `scenedetect OK: 0.6.x`

- [ ] **Step 4: Commit**

```bash
git add requirements.txt SimpleClaw/Flowcut/storage/database.py
git commit -m "feat(flowcut): add scenedetect dep + scene_data_json column migration"
```

---

## Task 2: scene_align.py — 时间对齐纯函数（TDD）

**Files:**
- Create: `SimpleClaw/tests/test_scene_align.py`
- Create: `SimpleClaw/Flowcut/services/scene_align.py`

- [ ] **Step 1: 写测试文件**

创建 `SimpleClaw/tests/test_scene_align.py`：

```python
"""Unit tests for Flowcut/services/scene_align.py — pure function, no I/O."""
import pytest
from Flowcut.services.scene_align import align_timestamps, detect_scene_cuts


# ── align_timestamps ──────────────────────────────────────────────────────────

def test_align_basic_snaps_to_nearest_cut():
    segments = [
        {"start_time": 0.0, "end_time": 4.2, "content": "A"},
        {"start_time": 4.2, "end_time": 8.1, "content": "B"},
        {"start_time": 8.1, "end_time": 11.7, "content": "C"},
    ]
    cuts = [0.0, 3.96, 7.92, 12.03]
    result = align_timestamps(segments, cuts)

    assert result[0]["start_time"] == 0.0
    assert result[0]["end_time"] == pytest.approx(3.96)
    assert result[1]["start_time"] == pytest.approx(3.96)
    assert result[1]["end_time"] == pytest.approx(7.92)
    assert result[2]["start_time"] == pytest.approx(7.92)
    assert result[2]["end_time"] == pytest.approx(12.03)


def test_align_first_segment_always_starts_at_zero():
    segments = [{"start_time": 0.5, "end_time": 4.0, "content": "A"}]
    cuts = [0.0, 4.1]
    result = align_timestamps(segments, cuts)
    assert result[0]["start_time"] == 0.0


def test_align_no_overlap_between_segments():
    segments = [
        {"start_time": 0.0, "end_time": 5.0, "content": "A"},
        {"start_time": 4.8, "end_time": 9.0, "content": "B"},
    ]
    cuts = [0.0, 5.1, 9.2]
    result = align_timestamps(segments, cuts)
    # end of seg[0] must not exceed start of seg[1]
    assert result[0]["end_time"] <= result[1]["start_time"]


def test_align_minimum_duration_enforced():
    """Segment shorter than 0.5s after alignment gets stretched to next cut."""
    segments = [
        {"start_time": 0.0, "end_time": 0.2, "content": "A"},
        {"start_time": 0.2, "end_time": 4.0, "content": "B"},
    ]
    cuts = [0.0, 3.96]
    result = align_timestamps(segments, cuts)
    # A is too short — pushed to the 3.96 cut; B follows
    assert result[0]["end_time"] - result[0]["start_time"] >= 0.5


def test_align_preserves_content():
    segments = [{"start_time": 0.0, "end_time": 3.0, "content": "hello"}]
    cuts = [0.0, 3.0]
    result = align_timestamps(segments, cuts)
    assert result[0]["content"] == "hello"


def test_align_empty_cuts_returns_original_times():
    segments = [{"start_time": 0.0, "end_time": 4.0, "content": "A"}]
    result = align_timestamps(segments, [])
    assert result[0]["start_time"] == 0.0
    assert result[0]["end_time"] == pytest.approx(4.0)


def test_align_window_no_match_keeps_original():
    """When no cut is within ±1s, keep the original timestamp."""
    segments = [{"start_time": 0.0, "end_time": 4.0, "content": "A"}]
    cuts = [0.0, 10.0]
    result = align_timestamps(segments, cuts)
    assert result[0]["end_time"] == pytest.approx(4.0)
```

- [ ] **Step 2: 运行测试，预期全部 FAIL**

```bash
cd SimpleClaw
uv run pytest tests/test_scene_align.py -v 2>&1 | head -30
```

Expected: `ImportError` 或全部 FAILED（模块不存在）

- [ ] **Step 3: 创建 `Flowcut/services/__init__.py`（若不存在）**

```bash
touch SimpleClaw/Flowcut/services/__init__.py
```

- [ ] **Step 4: 实现 `Flowcut/services/scene_align.py`**

创建 `SimpleClaw/Flowcut/services/scene_align.py`：

```python
"""PySceneDetect 场景切点检测 + LLM 时间戳对齐。"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

_ALIGN_WINDOW_S = 1.0   # 匹配窗口：±1 秒
_MIN_DURATION_S = 0.5   # 最短段时长


def _find_nearest_cut(target: float, cuts: list[float]) -> float | None:
    """在 cuts 中找 ±ALIGN_WINDOW_S 内最接近 target 的切点。"""
    best: float | None = None
    best_dist = float("inf")
    for c in cuts:
        dist = abs(c - target)
        if dist <= _ALIGN_WINDOW_S and dist < best_dist:
            best_dist = dist
            best = c
    return best


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
        content = seg.get("content", "")

        snapped_end = _find_nearest_cut(end, cuts) if cuts else None
        new_end = snapped_end if snapped_end is not None else end

        result.append({"start_time": start, "end_time": new_end, "content": content})

    # 后处理 1：首段强制从 0 开始
    result[0] = {**result[0], "start_time": 0.0}

    # 后处理 2：后段 start 不早于前段 end；前段 end 不晚于后段 start
    for i in range(1, len(result)):
        prev_end = result[i - 1]["end_time"]
        cur_start = result[i]["start_time"]
        if cur_start < prev_end:
            result[i] = {**result[i], "start_time": prev_end}

    # 后处理 3：最小持续时间 0.5s
    for i, seg in enumerate(result):
        if seg["end_time"] - seg["start_time"] < _MIN_DURATION_S:
            # 向后拉：找下一个切点大于 start + MIN_DURATION
            new_end = seg["start_time"] + _MIN_DURATION_S
            for c in cuts:
                if c >= new_end:
                    new_end = c
                    break
            result[i] = {**result[i], "end_time": new_end}

    return result


def _run_scene_detect(video_path: str, threshold: float = 27.0) -> list[float]:
    """同步：对本地视频文件运行 PySceneDetect，返回切点秒数列表。"""
    from scenedetect import open_video, SceneManager
    from scenedetect.detectors import ContentDetector

    video = open_video(video_path)
    manager = SceneManager()
    manager.add_detector(ContentDetector(threshold=threshold))
    manager.detect_scenes(video, show_progress=False)
    scenes = manager.get_scene_list()

    cuts: list[float] = [0.0]
    for _, end in scenes:
        cuts.append(end.get_seconds())
    return cuts


async def detect_scene_cuts(video_path: str, threshold: float = 27.0) -> list[float]:
    """异步包装：在线程池中运行 PySceneDetect，返回切点秒数列表。"""
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=1) as pool:
        return await loop.run_in_executor(pool, _run_scene_detect, video_path, threshold)
```

- [ ] **Step 5: 运行测试，预期全部 PASS**

```bash
cd SimpleClaw
uv run pytest tests/test_scene_align.py -v
```

Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add SimpleClaw/Flowcut/services/__init__.py SimpleClaw/Flowcut/services/scene_align.py SimpleClaw/tests/test_scene_align.py
git commit -m "feat(flowcut): implement scene_align — PySceneDetect cuts + timestamp alignment"
```

---

## Task 3: gemini_video.py — Gemini 多模态分析（TDD）

**Files:**
- Create: `SimpleClaw/tests/test_gemini_video_parse.py`
- Create: `SimpleClaw/Flowcut/services/gemini_video.py`

- [ ] **Step 1: 写测试**

创建 `SimpleClaw/tests/test_gemini_video_parse.py`：

```python
"""Unit tests for gemini_video._parse_segments — no network calls."""
import json
import pytest
from Flowcut.services.gemini_video import _parse_segments


def test_parse_valid_json_array():
    raw = json.dumps([
        {"start_time": 0, "end_time": 4, "content": "开场"},
        {"start_time": 4, "end_time": 8, "content": "产品展示"},
    ])
    result = _parse_segments(raw)
    assert len(result) == 2
    assert result[0]["start_time"] == 0.0
    assert result[1]["content"] == "产品展示"


def test_parse_json_with_markdown_fence():
    raw = "```json\n[{\"start_time\": 0, \"end_time\": 3, \"content\": \"A\"}]\n```"
    result = _parse_segments(raw)
    assert len(result) == 1
    assert result[0]["end_time"] == 3.0


def test_parse_missing_end_time_defaults_to_start_plus_one():
    raw = json.dumps([{"start_time": 5, "content": "B"}])
    result = _parse_segments(raw)
    assert result[0]["end_time"] == pytest.approx(6.0)


def test_parse_empty_list_returns_empty():
    result = _parse_segments("[]")
    assert result == []


def test_parse_invalid_json_returns_empty():
    result = _parse_segments("not json at all")
    assert result == []


def test_parse_string_times_converted_to_float():
    raw = json.dumps([{"start_time": "1", "end_time": "5", "content": "C"}])
    result = _parse_segments(raw)
    assert result[0]["start_time"] == pytest.approx(1.0)
    assert result[0]["end_time"] == pytest.approx(5.0)
```

- [ ] **Step 2: 运行测试，预期全部 FAIL**

```bash
cd SimpleClaw
uv run pytest tests/test_gemini_video_parse.py -v 2>&1 | head -15
```

Expected: `ImportError` 或 FAILED（模块不存在）

- [ ] **Step 3: 实现 `Flowcut/services/gemini_video.py`**

创建 `SimpleClaw/Flowcut/services/gemini_video.py`：

```python
"""Gemini 多模态视频理解 — 语义拆镜。

流程：
  1. 将本地视频文件上传到 Gemini Files API（google-genai SDK）
  2. 调用 gemini-3.1-flash-lite-preview（或环境变量覆盖）生成拆镜 JSON
  3. 解析返回文本，得到 [{start_time, end_time, content}] 列表
"""
from __future__ import annotations

import json
import os
import re

import google.genai as genai
from google.genai import types

_DEFAULT_MODEL = "gemini-3.1-flash-lite-preview"

_DECOMPOSE_PROMPT = """\
请仔细观看这段视频，按镜头级别拆分为若干段落，每段时长 2-5 秒。
满足以下任一条件时开新段：切景、主体变化、景别变化、动作逻辑变化。

输出严格遵循 JSON 数组格式，不要添加任何解释文字：
[
  {
    "start_time": <累计秒数，数字>,
    "end_time": <累计秒数，数字>,
    "content": "<人物/物体、场景、关键动作、明显视觉元素、镜头语言、对话内容>"
  },
  ...
]
"""


def _parse_segments(raw_text: str) -> list[dict]:
    """从模型返回的文本中提取 JSON 数组，容错处理 markdown 代码块。"""
    text = raw_text.strip()

    # 去除 markdown 代码块
    fence = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if fence:
        text = fence.group(1).strip()

    # 尝试解析 JSON
    try:
        data = json.loads(text)
    except Exception:
        return []

    if not isinstance(data, list):
        return []

    result: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item.get("start_time", 0.0))
            end = float(item.get("end_time", start + 1.0))
            content = str(item.get("content", ""))
            result.append({"start_time": start, "end_time": end, "content": content})
        except (TypeError, ValueError):
            continue
    return result


async def analyze_video(
    video_path: str,
    *,
    api_key: str | None = None,
    model: str | None = None,
) -> list[dict]:
    """上传视频到 Gemini Files API，调用多模态模型，返回拆镜段落列表。

    Args:
        video_path: 本地视频文件路径。
        api_key: Google API Key，默认读取 GOOGLE_API_KEY 环境变量。
        model: 模型名称，默认 gemini-3.1-flash-lite-preview（或 FLOWCUT_DECOMPOSE_MODEL 覆盖）。

    Returns:
        [{start_time: float, end_time: float, content: str}] 列表，可能为空。
    """
    resolved_key = api_key or os.environ["GOOGLE_API_KEY"]
    resolved_model = model or os.getenv("FLOWCUT_DECOMPOSE_MODEL", _DEFAULT_MODEL)

    client = genai.Client(api_key=resolved_key)

    # 上传视频文件，等待处理完成
    uploaded = client.files.upload(file=video_path)

    # 等待文件状态变为 ACTIVE（最多 120 秒）
    import time
    deadline = time.time() + 120
    while uploaded.state.name != "ACTIVE":
        if time.time() > deadline:
            raise TimeoutError(f"Gemini file upload timed out: {uploaded.name}")
        time.sleep(2)
        uploaded = client.files.get(name=uploaded.name)

    response = client.models.generate_content(
        model=resolved_model,
        contents=[
            types.Part(file_data=types.FileData(
                file_uri=uploaded.uri,
                mime_type="video/mp4",
            )),
            types.Part(text=_DECOMPOSE_PROMPT),
        ],
        config=types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=4096,
        ),
    )

    raw_text = response.text or ""
    return _parse_segments(raw_text)
```

- [ ] **Step 4: 运行解析测试，预期全部 PASS**

```bash
cd SimpleClaw
uv run pytest tests/test_gemini_video_parse.py -v
```

Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add SimpleClaw/Flowcut/services/gemini_video.py SimpleClaw/tests/test_gemini_video_parse.py
git commit -m "feat(flowcut): implement gemini_video — multimodal scene analysis with Gemini Files API"
```

---

## Task 4: MaterialRepository.update_status 支持 scene_data

**Files:**
- Modify: `SimpleClaw/Flowcut/storage/material_repo.py`

- [ ] **Step 1: 扩展 update_status 方法**

在 `SimpleClaw/Flowcut/storage/material_repo.py` 中，修改 `update_status` 方法签名和方法体，增加 `scene_data` 参数：

原始签名（`material_repo.py:82`）：
```python
    async def update_status(self, material_id: int, status: str, *,
                             name: str | None = None,
                             transcript: str | None = None,
                             thumbnail_url: str | None = None,
                             preview_url: str | None = None) -> None:
```

修改为：
```python
    async def update_status(self, material_id: int, status: str, *,
                             name: str | None = None,
                             transcript: str | None = None,
                             thumbnail_url: str | None = None,
                             preview_url: str | None = None,
                             scene_data: list[dict] | None = None) -> None:
```

在方法体的 `if preview_url is not None:` 块之后，`params.append(material_id)` 之前，增加：

```python
        if scene_data is not None:
            import json as _json
            set_clauses.append("scene_data_json=%s")
            params.append(_json.dumps(scene_data, ensure_ascii=False))
```

- [ ] **Step 2: 验证 import 无报错**

```bash
cd SimpleClaw
uv run python -c "from Flowcut.storage.material_repo import MaterialRepository; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add SimpleClaw/Flowcut/storage/material_repo.py
git commit -m "feat(flowcut): extend MaterialRepository.update_status with scene_data param"
```

---

## Task 5: make_scene_decompose_executor 实现

**Files:**
- Modify: `SimpleClaw/Flowcut/runtime/executors.py`
- Create: `SimpleClaw/tests/test_scene_decompose_executor.py`

- [ ] **Step 1: 写 executor 集成单元测试**

创建 `SimpleClaw/tests/test_scene_decompose_executor.py`：

```python
"""Unit tests for make_scene_decompose_executor — mock Gemini + scenedetect + repo."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from simpleclaw.runtime.task_protocol import TaskEnvelope
from Flowcut.runtime.executors import make_scene_decompose_executor
from Flowcut.runtime.streams import FlowcutTaskStream


def _make_envelope(material_id: int, oss_key: str = "fc/test.mp4", oss_url: str = "") -> TaskEnvelope:
    return TaskEnvelope(
        task_type="scene_decompose",
        payload={"material_id": material_id, "oss_key": oss_key, "oss_url": oss_url},
        stream=FlowcutTaskStream.SCENE_DECOMPOSE,
    )


@pytest.mark.asyncio
async def test_executor_success_writes_scene_data():
    """Happy path: Gemini returns 3 segments, scene detect returns cuts → aligned data written."""
    mock_repo = MagicMock()
    mock_repo.update_status = AsyncMock()

    fake_segments = [
        {"start_time": 0.0, "end_time": 4.2, "content": "开场"},
        {"start_time": 4.2, "end_time": 8.1, "content": "产品"},
        {"start_time": 8.1, "end_time": 12.0, "content": "结尾"},
    ]
    fake_cuts = [0.0, 3.96, 7.92, 12.03]

    with (
        patch("Flowcut.runtime.executors._download_file", new=AsyncMock()),
        patch("Flowcut.runtime.executors.analyze_video", new=AsyncMock(return_value=fake_segments)),
        patch("Flowcut.runtime.executors.detect_scene_cuts", new=AsyncMock(return_value=fake_cuts)),
        patch("Flowcut.runtime.executors._resolve_download_url", return_value="http://example.com/video.mp4"),
    ):
        executor = make_scene_decompose_executor(material_repo=mock_repo)
        result = await executor(_make_envelope(42))

    assert result.status == "succeeded"
    mock_repo.update_status.assert_awaited_once()
    call_kwargs = mock_repo.update_status.call_args.kwargs
    assert call_kwargs["status"] == "READY"
    assert isinstance(call_kwargs["scene_data"], list)
    assert len(call_kwargs["scene_data"]) == 3


@pytest.mark.asyncio
async def test_executor_gemini_returns_empty_marks_failed():
    """Gemini returns empty segments → mark material FAILED."""
    mock_repo = MagicMock()
    mock_repo.update_status = AsyncMock()

    with (
        patch("Flowcut.runtime.executors._download_file", new=AsyncMock()),
        patch("Flowcut.runtime.executors.analyze_video", new=AsyncMock(return_value=[])),
        patch("Flowcut.runtime.executors.detect_scene_cuts", new=AsyncMock(return_value=[0.0])),
        patch("Flowcut.runtime.executors._resolve_download_url", return_value="http://example.com/video.mp4"),
    ):
        executor = make_scene_decompose_executor(material_repo=mock_repo)
        result = await executor(_make_envelope(99))

    assert result.status == "failed"


@pytest.mark.asyncio
async def test_executor_download_failure_marks_failed():
    """Download exception → executor returns failed without crashing."""
    mock_repo = MagicMock()
    mock_repo.update_status = AsyncMock()

    with (
        patch("Flowcut.runtime.executors._download_file", new=AsyncMock(side_effect=RuntimeError("network error"))),
        patch("Flowcut.runtime.executors._resolve_download_url", return_value="http://example.com/video.mp4"),
    ):
        executor = make_scene_decompose_executor(material_repo=mock_repo)
        result = await executor(_make_envelope(7))

    assert result.status == "failed"
    assert "network error" in (result.error or "")
```

- [ ] **Step 2: 运行测试，预期 FAIL（函数未实现）**

```bash
cd SimpleClaw
uv run pytest tests/test_scene_decompose_executor.py -v 2>&1 | head -20
```

Expected: `NotImplementedError` 或 FAILED

- [ ] **Step 3: 实现 make_scene_decompose_executor**

在 `SimpleClaw/Flowcut/runtime/executors.py` 中：

1. 在文件顶部 import 区域（`from Flowcut.storage.oss_client import build_oss_client` 之后）新增两个 import：

```python
from Flowcut.services.gemini_video import analyze_video
from Flowcut.services.scene_align import detect_scene_cuts, align_timestamps
```

2. 将 `make_scene_decompose_executor` 函数替换为：

```python
def make_scene_decompose_executor(
    material_repo: MaterialRepository,
) -> Callable[[TaskEnvelope], Awaitable[TaskExecutionResult]]:
    """爆款视频拆镜：Gemini 语义分段 + PySceneDetect 时间修正。"""

    async def execute(task: TaskEnvelope) -> TaskExecutionResult:
        payload = task.payload
        material_id = int(payload["material_id"])
        oss_key = str(payload.get("oss_key", ""))
        oss_url = str(payload.get("oss_url", ""))

        video_path: str | None = None
        try:
            download_url = _resolve_download_url(oss_key, oss_url)
            if not download_url:
                raise ValueError("No download URL: oss_key and oss_url are both empty")

            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                video_path = tmp.name

            await _download_file(download_url, video_path)

            # 并行：Gemini 语义分析 + PySceneDetect 切点检测
            gemini_task = asyncio.create_task(analyze_video(video_path))
            cuts_task = asyncio.create_task(detect_scene_cuts(video_path))
            segments, cuts = await asyncio.gather(gemini_task, cuts_task)

            if not segments:
                await material_repo.update_status(material_id, "FAILED",
                                                   transcript="Gemini returned empty segments")
                return TaskExecutionResult.failed(
                    error="Gemini returned empty segments",
                    summary=f"material_id={material_id} decompose empty",
                )

            aligned = align_timestamps(segments, cuts)
            await material_repo.update_status(material_id, "READY", scene_data=aligned)
            return TaskExecutionResult.succeeded(
                summary=f"material_id={material_id} segments={len(aligned)}",
                details={"material_id": material_id, "segment_count": len(aligned)},
            )

        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}"
            try:
                await material_repo.update_status(material_id, "FAILED",
                                                   transcript=error_text[:2000])
            except Exception:
                pass
            return TaskExecutionResult.failed(error=error_text)

        finally:
            if video_path and os.path.exists(video_path):
                try:
                    os.unlink(video_path)
                except OSError:
                    pass

    return execute
```

- [ ] **Step 4: 运行测试，预期全部 PASS**

```bash
cd SimpleClaw
uv run pytest tests/test_scene_decompose_executor.py -v
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add SimpleClaw/Flowcut/runtime/executors.py SimpleClaw/tests/test_scene_decompose_executor.py
git commit -m "feat(flowcut): implement make_scene_decompose_executor — Gemini + SceneDetect pipeline"
```

---

## Task 6: DecomposeVideoTool.prepare_task + worker 注册

**Files:**
- Modify: `SimpleClaw/Flowcut/tools/decompose_video.py`
- Modify: `SimpleClaw/Flowcut/runtime/worker.py`

- [ ] **Step 1: 实现 prepare_task**

将 `SimpleClaw/Flowcut/tools/decompose_video.py` 的内容替换为：

```python
"""上传爆款视频后拆镜，调 Gemini。"""
from __future__ import annotations

from typing import TYPE_CHECKING

from simpleclaw.runtime.task_protocol import TaskEnvelope
from simpleclaw.tools.base import Tool, ToolResult
from Flowcut.runtime.streams import FlowcutTaskStream

if TYPE_CHECKING:
    from simpleclaw.runtime.services import RuntimeServices
    from Flowcut.storage.material_repo import MaterialRepository


class DecomposeVideoTool(Tool):
    """拆解爆款视频为分镜场景数据，调用 Gemini 进行视觉分析。"""

    name = "decompose_video"
    description = (
        "上传一个已处理完成的爆款视频素材（status=READY），"
        "触发后台 Gemini 拆镜任务，将视频分解为各段时间戳、画面描述和口播台词。"
        "任务异步执行，调用后立即返回 task_id，可用 check_task_status 查进度。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "material_id": {
                "type": "integer",
                "description": "已上传并处理完成的素材 ID（status=READY 的爆款视频）",
            }
        },
        "required": ["material_id"],
    }
    execution_mode = "durable"
    needs_followup = True

    def __init__(self, *, runtime: "RuntimeServices", material_repo: "MaterialRepository") -> None:
        self._runtime = runtime
        self._material_repo = material_repo

    async def prepare_task(self, material_id: int, **kwargs) -> "TaskEnvelope | ToolResult":
        """验证素材状态，构造拆镜 TaskEnvelope 并提交队列。"""
        material = await self._material_repo.get(material_id)

        if material is None:
            return ToolResult(
                content=f"素材 {material_id} 不存在",
                ok=False,
            )

        if material.get("status") != "READY":
            status = material.get("status", "UNKNOWN")
            return ToolResult(
                content=f"素材 {material_id} 当前状态为 {status}，需等待处理完成（READY）后再拆镜",
                ok=False,
            )

        return TaskEnvelope(
            task_type="scene_decompose",
            payload={
                "material_id": material_id,
                "oss_key": material.get("oss_key", ""),
                "oss_url": material.get("oss_url", ""),
            },
            stream=FlowcutTaskStream.SCENE_DECOMPOSE,
            scope_key=f"scene_decompose:{material_id}",
        )
```

- [ ] **Step 2: 注册 executor 到 SCENE_DECOMPOSE worker**

在 `SimpleClaw/Flowcut/runtime/worker.py` 中：

1. 修改 import，加入 `make_scene_decompose_executor`：

```python
from Flowcut.runtime.executors import make_material_process_executor, make_scene_decompose_executor
```

2. 修改 `make_workers` 函数签名，加入 `script_repo` 参数（为未来保留，当前仅传 material_repo）：

将函数签名第一行后的参数改为：

```python
def make_workers(
    task_queue: InMemoryTaskQueue | RedisTaskQueue,
    task_scope_locks: ScopeLockRegistry,
    task_state_store: TaskStateStore,
    *,
    material_repo: MaterialRepository,
) -> list[TaskWorker]:
```

（签名不变，只更新 executor 注册）

3. 将 `SCENE_DECOMPOSE` worker 的 `executors={}` 改为：

```python
        _make_worker(
            FlowcutTaskStream.SCENE_DECOMPOSE,
            {"scene_decompose": make_scene_decompose_executor(material_repo)},
        ),
```

- [ ] **Step 3: 验证模块可导入**

```bash
cd SimpleClaw
uv run python -c "
from Flowcut.tools.decompose_video import DecomposeVideoTool
from Flowcut.runtime.worker import make_workers
print('imports OK')
"
```

Expected: `imports OK`

- [ ] **Step 4: 更新 container.py 中 DecomposeVideoTool 构造调用**

在 `SimpleClaw/Flowcut/api/container.py` 中，找到 `DecomposeVideoTool` 的实例化，增加 `material_repo` 参数：

搜索：
```python
DecomposeVideoTool(runtime=runtime)
```

替换为：
```python
DecomposeVideoTool(runtime=runtime, material_repo=material_repo)
```

（若 container.py 中 DecomposeVideoTool 尚未使用 `material_repo`，先读 container.py 找到正确的构造位置再编辑）

- [ ] **Step 5: Commit**

```bash
git add SimpleClaw/Flowcut/tools/decompose_video.py SimpleClaw/Flowcut/runtime/worker.py SimpleClaw/Flowcut/api/container.py
git commit -m "feat(flowcut): wire DecomposeVideoTool.prepare_task + register scene_decompose executor"
```

---

## Task 7: 配置与环境变量文档

**Files:**
- Modify: `SimpleClaw/.env.example`（若存在）或 README

- [ ] **Step 1: 确认 .env.example 存在并补充变量**

```bash
ls SimpleClaw/.env.example 2>/dev/null || echo "not found"
```

若存在，在文件末尾追加：

```bash
# Flowcut — 拆镜模型（可覆盖默认值 gemini-3.1-flash-lite-preview）
FLOWCUT_DECOMPOSE_MODEL=gemini-3.1-flash-lite-preview
```

- [ ] **Step 2: 运行全部新增测试，确认全部通过**

```bash
cd SimpleClaw
uv run pytest tests/test_scene_align.py tests/test_gemini_video_parse.py tests/test_scene_decompose_executor.py -v
```

Expected: 16 passed, 0 failed

- [ ] **Step 3: Commit**

```bash
git add SimpleClaw/.env.example
git commit -m "chore(flowcut): document FLOWCUT_DECOMPOSE_MODEL env var"
```

---

## Self-Review

**Spec coverage check:**

| 需求 | 任务 |
|------|------|
| Gemini 多模态产出初稿脚本 | Task 3 (gemini_video.py) + Task 5 executor |
| scene detect 做时间修正 | Task 2 (scene_align.py) + Task 5 executor |
| JSON 结构 {start_time, end_time, content} | Task 3 _parse_segments + Task 2 align_timestamps |
| 2-5s 分段建议写入 prompt | Task 3 _DECOMPOSE_PROMPT |
| ±1s 窗口对齐候选切点 | Task 2 _find_nearest_cut（_ALIGN_WINDOW_S = 1.0）|
| 首段从 0 开始 | Task 2 后处理 1 |
| 后段 start 不早于前段 end | Task 2 后处理 2 |
| 最小 0.5s 持续时间 | Task 2 后处理 3 |
| durable tool 入队 | Task 6 prepare_task |
| 结果写库 | Task 4 + Task 5 material_repo.update_status(scene_data=...) |
| worker 注册 | Task 6 |

**Placeholder scan:** 无 TBD / TODO 残留。

**Type consistency:** `align_timestamps` 返回 `list[dict]`，executor 传给 `update_status(scene_data=...)` — 与 Task 4 扩展的 `scene_data: list[dict] | None` 签名一致。`detect_scene_cuts` 返回 `list[float]`，与 `align_timestamps` 入参 `cuts: list[float]` 一致。
