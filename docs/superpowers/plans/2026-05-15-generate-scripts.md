# Generate Scripts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 `generate_scripts` 工具，基于拆镜数据并发调 Gemini 生成 4 条角色差异化广告脚本（预览，不写库）

**Architecture:** 新增 `Flowcut/services/script_generator.py` 封装单角色生成逻辑（Gemini SDK 直调 + JSON 解析）；`tools/generate_scripts.py` 重写为编排层（从 DB 读 scene_data，asyncio.gather 并发 4 个角色）；`api/container.py` 更新工具构造签名。

**Tech Stack:** Python asyncio, google-genai SDK（同步 SDK 用 asyncio.to_thread 包装），aiomysql（通过 MaterialRepository）

---

## File Map

| 文件 | 操作 |
|------|------|
| `SimpleClaw/Flowcut/services/script_generator.py` | 新建 |
| `SimpleClaw/tests/test_script_generator.py` | 新建 |
| `SimpleClaw/Flowcut/tools/generate_scripts.py` | 重写 |
| `SimpleClaw/tests/test_generate_scripts_tool.py` | 新建 |
| `SimpleClaw/Flowcut/api/container.py` | 修改：更新 GenerateScriptsTool 构造 |

---

## Task 1: `script_generator.py` 服务层（TDD）

**Files:**
- Create: `SimpleClaw/Flowcut/services/script_generator.py`
- Create: `SimpleClaw/tests/test_script_generator.py`

- [ ] **Step 1: 写失败测试 — `_parse_script_response` 正常解析**

  新建 `SimpleClaw/tests/test_script_generator.py`：

  ```python
  """tests/test_script_generator.py"""
  import pytest
  from Flowcut.services.script_generator import _parse_script_response


  def _make_raw(role: str, title: str, segments: list[dict]) -> str:
      import json
      return json.dumps({"role": role, "title": title, "segments": segments}, ensure_ascii=False)


  _SAMPLE_SEGMENTS = [
      {"segment_idx": 0, "start_time": 0.0, "end_time": 3.96,
       "visual_guide": "特写产品", "copy_text": "你是不是也失眠？"},
      {"segment_idx": 1, "start_time": 3.96, "end_time": 8.2,
       "visual_guide": "主播出镜", "copy_text": "用了这个，第一晚就睡着了"},
  ]


  def test_parse_valid_json():
      raw = _make_raw("痛点型", "失眠的你", _SAMPLE_SEGMENTS)
      result = _parse_script_response(raw, role="痛点型")
      assert result is not None
      assert result["role"] == "痛点型"
      assert result["title"] == "失眠的你"
      assert len(result["segments"]) == 2
      assert result["segments"][0]["copy_text"] == "你是不是也失眠？"


  def test_parse_markdown_fence():
      import json
      inner = json.dumps({"role": "场景型", "title": "T", "segments": _SAMPLE_SEGMENTS})
      raw = f"```json\n{inner}\n```"
      result = _parse_script_response(raw, role="场景型")
      assert result is not None
      assert result["role"] == "场景型"


  def test_parse_invalid_json_returns_none():
      result = _parse_script_response("not json at all", role="对比型")
      assert result is None


  def test_parse_missing_segments_returns_none():
      import json
      raw = json.dumps({"role": "口碑型", "title": "T"})
      result = _parse_script_response(raw, role="口碑型")
      assert result is None


  def test_parse_wrong_role_corrected():
      """模型偶尔返回错误的 role 字段，应被覆盖为传入的 role。"""
      raw = _make_raw("随便什么", "T", _SAMPLE_SEGMENTS)
      result = _parse_script_response(raw, role="痛点型")
      assert result["role"] == "痛点型"


  def test_parse_segment_missing_copy_text():
      """copy_text 缺失时段落仍解析，copy_text 默认空串。"""
      import json
      segs = [{"segment_idx": 0, "start_time": 0.0, "end_time": 3.0, "visual_guide": "x"}]
      raw = json.dumps({"role": "场景型", "title": "T", "segments": segs})
      result = _parse_script_response(raw, role="场景型")
      assert result is not None
      assert result["segments"][0]["copy_text"] == ""
  ```

- [ ] **Step 2: 运行测试，确认全部失败**

  ```bash
  cd SimpleClaw && uv run pytest tests/test_script_generator.py -v 2>&1 | head -30
  ```
  预期：`ImportError` 或 `ModuleNotFoundError`（文件尚未创建）

- [ ] **Step 3: 实现 `script_generator.py`**

  新建 `SimpleClaw/Flowcut/services/script_generator.py`：

  ```python
  """Flowcut 脚本生成服务 — 基于拆镜数据生成差异化广告脚本。"""
  from __future__ import annotations

  import asyncio
  import json
  import os
  import re
  from typing import Any

  import google.genai as genai
  from google.genai import types

  _DEFAULT_MODEL = "gemini-3.1-flash-lite-preview"

  ROLES: list[dict[str, str]] = [
      {
          "name": "痛点型",
          "instruction": (
              "开头直击用户痛点，建立共鸣，产品作为解法登场。"
              "情绪基调：共鸣 → 希望。"
          ),
      },
      {
          "name": "场景型",
          "instruction": (
              "描绘真实使用场景，代入感强，让观众想象自己正在使用产品。"
              "情绪基调：轻松 → 向往。"
          ),
      },
      {
          "name": "对比型",
          "instruction": (
              "呈现使用前后的明显对比，强化产品效果，制造惊喜感。"
              "情绪基调：怀疑 → 惊喜。"
          ),
      },
      {
          "name": "口碑型",
          "instruction": (
              "以真实用户证言视角叙述，增强可信度，引发推荐欲望。"
              "情绪基调：信任 → 推荐。"
          ),
      },
  ]


  def _build_prompt(role: dict[str, str], scene_data: list[dict]) -> str:
      scene_json = json.dumps(scene_data, ensure_ascii=False, indent=2)
      return (
          f"你是一名专业的抖音短视频脚本创作专家，擅长「{role['name']}」风格。\n"
          f"该风格特点：{role['instruction']}\n\n"
          f"以下是爆款视频的拆镜数据（共 {len(scene_data)} 个分镜）：\n"
          f"{scene_json}\n\n"
          "请为每个分镜生成画面指引（visual_guide）和口播文案（copy_text），"
          f"创作一条「{role['name']}」广告脚本。\n\n"
          "输出严格遵循以下 JSON 格式，不要添加任何解释文字：\n"
          "{\n"
          f'  "role": "{role["name"]}",\n'
          '  "title": "<一句吸引人的标题>",\n'
          '  "segments": [\n'
          "    {\n"
          '      "segment_idx": 0,\n'
          '      "start_time": 0.0,\n'
          '      "end_time": 3.96,\n'
          '      "visual_guide": "<画面指引>",\n'
          '      "copy_text": "<口播文案>"\n'
          "    },\n"
          "    ...\n"
          "  ]\n"
          "}"
      )


  def _parse_script_response(raw_text: str, *, role: str) -> dict[str, Any] | None:
      """从模型返回文本中解析脚本 JSON，容错处理 markdown fence 和缺失字段。"""
      text = raw_text.strip()

      fence = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
      if fence:
          text = fence.group(1).strip()

      try:
          data = json.loads(text)
      except Exception:
          return None

      if not isinstance(data, dict):
          return None

      segments_raw = data.get("segments")
      if not isinstance(segments_raw, list) or len(segments_raw) == 0:
          return None

      segments: list[dict] = []
      for item in segments_raw:
          if not isinstance(item, dict):
              continue
          segments.append({
              "segment_idx": int(item.get("segment_idx", len(segments))),
              "start_time": float(item.get("start_time", 0.0)),
              "end_time": float(item.get("end_time", 0.0)),
              "visual_guide": str(item.get("visual_guide", "")),
              "copy_text": str(item.get("copy_text", "")),
          })

      if not segments:
          return None

      return {
          "role": role,
          "title": str(data.get("title", "")),
          "segments": segments,
      }


  def _call_gemini(prompt: str, *, api_key: str, model: str) -> str:
      """同步调用 Gemini（在线程中执行）。"""
      client = genai.Client(api_key=api_key)
      response = client.models.generate_content(
          model=model,
          contents=[types.Part(text=prompt)],
          config=types.GenerateContentConfig(
              temperature=0.8,
              max_output_tokens=4096,
              response_mime_type="application/json",
          ),
      )
      return response.text or ""


  async def generate_for_role(
      role: dict[str, str],
      scene_data: list[dict],
      *,
      api_key: str | None = None,
      model: str | None = None,
  ) -> dict[str, Any] | None:
      """为单个角色生成脚本，失败返回 None。"""
      resolved_key = api_key or os.environ["GOOGLE_API_KEY"]
      resolved_model = model or os.getenv("FLOWCUT_DECOMPOSE_MODEL", _DEFAULT_MODEL)
      prompt = _build_prompt(role, scene_data)

      try:
          raw = await asyncio.to_thread(_call_gemini, prompt, api_key=resolved_key, model=resolved_model)
          return _parse_script_response(raw, role=role["name"])
      except Exception:
          return None
  ```

- [ ] **Step 4: 运行测试，确认全部通过**

  ```bash
  cd SimpleClaw && uv run pytest tests/test_script_generator.py -v
  ```
  预期：6 个测试全部 PASS

- [ ] **Step 5: 提交**

  ```bash
  cd SimpleClaw && git add Flowcut/services/script_generator.py tests/test_script_generator.py && git commit -m "feat: script_generator 服务 — 角色 prompt + Gemini 调用 + JSON 解析"
  ```

---

## Task 2: 重写 `generate_scripts.py` 工具并更新 container（TDD）

**Files:**
- Modify: `SimpleClaw/Flowcut/tools/generate_scripts.py`
- Create: `SimpleClaw/tests/test_generate_scripts_tool.py`
- Modify: `SimpleClaw/Flowcut/api/container.py`

- [ ] **Step 1: 写失败测试**

  新建 `SimpleClaw/tests/test_generate_scripts_tool.py`：

  ```python
  """tests/test_generate_scripts_tool.py"""
  from __future__ import annotations

  import json
  import pytest
  from unittest.mock import AsyncMock, patch, MagicMock

  from Flowcut.tools.generate_scripts import GenerateScriptsTool


  def _make_tool():
      repo = MagicMock()
      return GenerateScriptsTool(material_repo=repo), repo


  def _sample_scene_data():
      return [
          {"start_time": 0.0, "end_time": 3.96, "content": "开场"},
          {"start_time": 3.96, "end_time": 8.2, "content": "主播出镜"},
      ]


  def _make_script(role: str):
      return {
          "role": role, "title": f"{role}标题",
          "segments": [
              {"segment_idx": 0, "start_time": 0.0, "end_time": 3.96,
               "visual_guide": "x", "copy_text": "y"},
          ],
      }


  @pytest.mark.asyncio
  async def test_execute_material_not_found():
      tool, repo = _make_tool()
      repo.get = AsyncMock(return_value=None)
      result = await tool.execute(material_id=99)
      assert result.ok is False
      assert "不存在" in result.content


  @pytest.mark.asyncio
  async def test_execute_scene_data_empty():
      tool, repo = _make_tool()
      repo.get = AsyncMock(return_value={"id": 1, "scene_data_json": None})
      result = await tool.execute(material_id=1)
      assert result.ok is False
      assert "拆镜" in result.content


  @pytest.mark.asyncio
  async def test_execute_all_success():
      tool, repo = _make_tool()
      scene_data = _sample_scene_data()
      repo.get = AsyncMock(return_value={
          "id": 1,
          "scene_data_json": json.dumps(scene_data),
      })
      roles = ["痛点型", "场景型", "对比型", "口碑型"]
      side_effects = [_make_script(r) for r in roles]

      with patch("Flowcut.tools.generate_scripts.generate_for_role") as mock_gen:
          mock_gen.side_effect = [
              _async_return(s) for s in side_effects
          ]
          result = await tool.execute(material_id=1)

      assert result.ok is True
      scripts = json.loads(result.content)
      assert len(scripts) == 4
      assert {s["role"] for s in scripts} == set(roles)


  @pytest.mark.asyncio
  async def test_execute_partial_failure():
      tool, repo = _make_tool()
      scene_data = _sample_scene_data()
      repo.get = AsyncMock(return_value={
          "id": 1,
          "scene_data_json": json.dumps(scene_data),
      })

      with patch("Flowcut.tools.generate_scripts.generate_for_role") as mock_gen:
          mock_gen.side_effect = [
              _async_return(_make_script("痛点型")),
              _async_return(None),   # 场景型 失败
              _async_return(_make_script("对比型")),
              _async_return(None),   # 口碑型 失败
          ]
          result = await tool.execute(material_id=1)

      assert result.ok is True
      scripts = json.loads(result.content)
      assert len(scripts) == 2
      assert "场景型" in result.content or "口碑型" in result.content  # 失败角色被标注


  @pytest.mark.asyncio
  async def test_execute_all_fail():
      tool, repo = _make_tool()
      scene_data = _sample_scene_data()
      repo.get = AsyncMock(return_value={
          "id": 1,
          "scene_data_json": json.dumps(scene_data),
      })

      with patch("Flowcut.tools.generate_scripts.generate_for_role") as mock_gen:
          mock_gen.side_effect = [_async_return(None)] * 4
          result = await tool.execute(material_id=1)

      assert result.ok is False


  async def _async_return(value):
      return value
  ```

- [ ] **Step 2: 运行测试，确认失败**

  ```bash
  cd SimpleClaw && uv run pytest tests/test_generate_scripts_tool.py -v 2>&1 | head -30
  ```
  预期：`TypeError` 或 `ImportError`（旧签名不接受 `material_repo`）

- [ ] **Step 3: 重写 `generate_scripts.py`**

  完整替换 `SimpleClaw/Flowcut/tools/generate_scripts.py`：

  ```python
  """拆镜完成后并发生成 4 条角色差异化广告脚本（预览，不写库）。"""
  from __future__ import annotations

  import asyncio
  import json
  from typing import TYPE_CHECKING

  from simpleclaw.tools.base import Tool, ToolResult
  from Flowcut.services.script_generator import ROLES, generate_for_role

  if TYPE_CHECKING:
      from Flowcut.storage.material_repo import MaterialRepository


  class GenerateScriptsTool(Tool):
      """基于拆镜结果并发生成 4 条角色差异化广告脚本，预览用，不写库。"""

      name = "generate_scripts"
      description = (
          "根据爆款视频的拆镜结果，生成 4 条差异化广告脚本（痛点型、场景型、对比型、口碑型）。"
          "每条脚本包含各分镜的画面指引和口播文案，供运营选择后再保存。"
          "请在 decompose_video 任务完成后调用。"
      )
      parameters = {
          "type": "object",
          "properties": {
              "material_id": {
                  "type": "integer",
                  "description": "已完成拆镜的素材 ID（fc_material.scene_data_json 不为空）",
              },
          },
          "required": ["material_id"],
      }
      execution_mode = "inline"
      needs_followup = True

      def __init__(self, *, material_repo: "MaterialRepository") -> None:
          self._material_repo = material_repo

      async def execute(self, material_id: int, **kwargs) -> ToolResult:
          material = await self._material_repo.get(material_id)

          if material is None:
              return ToolResult(content=f"素材 {material_id} 不存在", ok=False)

          raw_scene = material.get("scene_data_json")
          if not raw_scene:
              return ToolResult(
                  content="该素材尚未完成拆镜，请先调用 decompose_video 并等待任务完成",
                  ok=False,
              )

          scene_data: list[dict] = json.loads(raw_scene) if isinstance(raw_scene, str) else raw_scene

          results = await asyncio.gather(
              *[generate_for_role(role, scene_data) for role in ROLES],
              return_exceptions=False,
          )

          successful = [r for r in results if r is not None]
          failed_roles = [
              ROLES[i]["name"] for i, r in enumerate(results) if r is None
          ]

          if not successful:
              return ToolResult(content="所有角色脚本生成失败，请重试", ok=False)

          content_parts = [json.dumps(successful, ensure_ascii=False, indent=2)]
          if failed_roles:
              content_parts.append(f"\n（以下角色生成失败：{', '.join(failed_roles)}）")

          return ToolResult(content="".join(content_parts), ok=True)
  ```

- [ ] **Step 4: 运行测试，确认全部通过**

  ```bash
  cd SimpleClaw && uv run pytest tests/test_generate_scripts_tool.py -v
  ```
  预期：5 个测试全部 PASS

- [ ] **Step 5: 更新 `container.py` 构造签名**

  找到文件 `SimpleClaw/Flowcut/api/container.py` 中以下行：

  ```python
  lambda _: GenerateScriptsTool(),
  ```

  替换为：

  ```python
  lambda _: GenerateScriptsTool(material_repo=material_repo),
  ```

- [ ] **Step 6: 运行全量测试，确认无回归**

  ```bash
  cd SimpleClaw && uv run pytest tests/ -v --tb=short 2>&1 | tail -20
  ```
  预期：全部 PASS（含 Task 1 的 6 个 + Task 2 的 5 个 + 之前的回归测试）

- [ ] **Step 7: 提交**

  ```bash
  cd SimpleClaw && git add Flowcut/tools/generate_scripts.py tests/test_generate_scripts_tool.py Flowcut/api/container.py && git commit -m "feat: generate_scripts 工具 — 并发 4 角色脚本生成，预览不写库"
  ```
