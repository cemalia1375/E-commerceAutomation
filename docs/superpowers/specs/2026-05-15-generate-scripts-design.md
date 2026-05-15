# Generate Scripts 设计文档

**日期:** 2026-05-15
**功能:** FlowCut `generate_scripts` 工具 — 基于拆镜数据生成差异化广告脚本

---

## 背景

`decompose_video` 工具完成后，`fc_material.scene_data_json` 中存储了拆镜结果（`[{start_time, end_time, content}]`）。下一步是根据这份拆镜数据生成多条差异化的抖音广告脚本，供运营人员选择。

---

## 目标

- 一次调用生成 4 条角色差异化脚本（痛点型 / 场景型 / 对比型 / 口碑型）
- 每条脚本按分镜粒度输出 `visual_guide`（画面指引）和 `copy_text`（口播文案）
- 只预览，不自动写库；用户选定后由后续流程持久化

---

## 不在范围内

- 脚本保存到 `fc_script` 表（留给后续 `save_script` 工具或 API）
- 用户自定义角色风格（当前固定 4 个角色）
- 视频合成（`compose_video` 工具负责）

---

## 架构

```
GenerateScriptsTool.execute(material_id)
  │
  ├─ material_repo.get(material_id)   ← 读 fc_material.scene_data_json
  │
  ├─ asyncio.gather(
  │     generate_for_role("痛点型", scene_data),
  │     generate_for_role("场景型", scene_data),
  │     generate_for_role("对比型", scene_data),
  │     generate_for_role("口碑型", scene_data),
  │  )
  │
  └─ 收集成功结果 → ToolResult(content=JSON array)
```

新增服务文件 `Flowcut/services/script_generator.py`，封装单角色生成逻辑（prompt 构造 + Gemini 调用 + JSON 解析）。工具层 (`tools/generate_scripts.py`) 只做编排，不含 LLM 细节。

---

## 数据结构

### 输入（从 DB 读取）

```json
[
  {"start_time": 0.0,  "end_time": 3.96, "content": "开场：产品特写，背景音乐渐入"},
  {"start_time": 3.96, "end_time": 8.2,  "content": "主播出镜讲解核心卖点"}
]
```

### 单角色输出（Gemini 响应解析后）

```json
{
  "role": "痛点型",
  "title": "睡眠不好？试试这个...",
  "segments": [
    {
      "segment_idx": 0,
      "start_time": 0.0,
      "end_time": 3.96,
      "visual_guide": "特写产品包装，灯光柔和，慢推镜头",
      "copy_text": "你是不是也经常失眠，躺下却怎么也睡不着？"
    },
    {
      "segment_idx": 1,
      "start_time": 3.96,
      "end_time": 8.2,
      "visual_guide": "主播正面出镜，表情亲切自然",
      "copy_text": "我用了这个之后，第一晚就睡着了，真的不是夸张"
    }
  ]
}
```

### ToolResult content

4 条脚本的 JSON 数组字符串，LLM 读取后呈现给用户。

---

## 角色定义

| 角色 | 核心策略 | 情绪基调 |
|------|---------|---------|
| 痛点型 | 开头直击痛点，产品作为解法 | 共鸣 → 希望 |
| 场景型 | 描绘使用场景，代入感强 | 轻松 → 向往 |
| 对比型 | 使用前后对比，强化效果 | 怀疑 → 惊喜 |
| 口碑型 | 用户证言视角，真实感强 | 信任 → 推荐 |

---

## Gemini 调用方式

使用 `google-genai` SDK 直接调用（与 `gemini_video.py` 模式一致），不通过 `GeminiLLM` 流式接口。

- 模型：从 `FLOWCUT_DECOMPOSE_MODEL` 环境变量读取，默认 `gemini-2.5-flash-lite-preview`
- 输出格式：要求 Gemini 返回 JSON（在 prompt 中明确约束 + `response_mime_type="application/json"`）
- 解析：`json.loads` + 结构校验；解析失败返回 `None`（工具层跳过该角色）

---

## 错误处理

| 场景 | 行为 |
|------|------|
| `material_id` 不存在 | `ToolResult(ok=False)` |
| `scene_data_json` 为空或 null | `ToolResult(ok=False, content="素材尚未完成拆镜")` |
| 某角色 Gemini 调用失败 / JSON 无效 | 跳过该角色，content 中标注失败角色名 |
| 全部 4 条失败 | `ToolResult(ok=False, content="脚本生成失败，请重试")` |

---

## 文件变更

| 文件 | 操作 |
|------|------|
| `Flowcut/services/script_generator.py` | 新建：`ROLES` 常量、`generate_for_role()`、`_parse_script_response()` |
| `Flowcut/tools/generate_scripts.py` | 重写：调用 `script_generator`，编排并发 |
| `tests/test_script_generator.py` | 新建：单元测试（mock Gemini SDK） |
| `tests/test_generate_scripts_tool.py` | 新建：单元测试（mock repo + generator） |

---

## 测试策略

**`test_script_generator.py`（服务层单元测试）**
- `_parse_script_response` 正常解析
- `_parse_script_response` 容错：markdown fence 包裹、缺失字段、无效 JSON
- `generate_for_role` mock Gemini SDK 返回，验证 prompt 包含角色名和 scene_data

**`test_generate_scripts_tool.py`（工具层单元测试）**
- 正常路径：4 条全部成功，ToolResult ok=True
- `scene_data_json` 为 None → ok=False
- 部分角色失败 → 返回剩余成功条目，content 标注失败角色
- 全部失败 → ok=False
