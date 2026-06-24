# 高光数字人优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 清理高光相关遗留 prompt/工具、为跨集高光补充数字人参数、给 agent 提供主动导航能力、让 agent 每轮感知用户当前所在界面。

**Architecture:** Prompt 层删除已废弃的单集高光 / 批量高光流程，保留跨集高光为唯一入口并扩展 connector_asset_id；后端新增 UIContextAttentionProvider，每轮从 HTTP 请求接收 ui_context 并注入为 attention packet；前端新增 uiContextStore 供各 tab 组件写入当前位置，ChatPanel 随每次发送附带给后端；新增 navigate_to 工具让 agent 可主动触发页面跳转。

**Tech Stack:** Python / FastAPI / simpleclaw ContextBuilder（AttentionProvider protocol）；React 19 / TypeScript / Zustand

## Global Constraints

- 后端运行环境：`uv run pytest`（SimpleClaw 目录下）
- 测试标记：`@pytest.mark.unit`（本 plan 只写单测，无需集成环境）
- 前端无自动化测试，靠手动验证
- 删除废弃代码时只动 prompt/agent 文件和 container.py，不删除工具的 `.py` 文件（历史文件保留，仅从 container 移除注册）
- `create_cross_episode_job` 新增字段向后兼容：不传则为 NULL，不影响现有记录
- `navigate_to` 的路由白名单与前端 `ALLOWED_ROUTE_PATTERNS` 保持一致

---

## File Map

| 文件 | 操作 |
|------|------|
| `SimpleClaw/Flowcut/workspace/Agent.md` | 修改：删除 B/C 流程，更新高光工作流 |
| `SimpleClaw/Flowcut/workspace/TOOL.md` | 修改：删除废弃工具条目，新增 navigate_to |
| `SimpleClaw/Flowcut/skills/drama_highlight/SKILL.md` | 删除目录 |
| `SimpleClaw/Flowcut/tools/create_cross_episode_highlights.py` | 修改：新增 connector_asset_id 参数 |
| `SimpleClaw/Flowcut/storage/creative_repo.py` | 修改：create_cross_episode_job 新增 connector_asset_id |
| `SimpleClaw/Flowcut/runtime/executors.py` | 修改：highlight_plan executor 传递 connector_asset_id |
| `SimpleClaw/Flowcut/tools/navigate_to.py` | 新建 |
| `SimpleClaw/Flowcut/api/container.py` | 修改：移除废弃工具注册，注册 NavigateToTool |
| `SimpleClaw/Flowcut/context/providers.py` | 修改：新增 UIContextAttentionProvider |
| `SimpleClaw/Flowcut/agent/main_agent.py` | 修改：挂载 UIContextAttentionProvider |
| `SimpleClaw/Flowcut/storage/session_store.py` | 修改：set_turn_context 传递 ui_context |
| `SimpleClaw/Flowcut/api/routes/chat.py` | 修改：读取 ui_context 字段 |
| `flowcut_frontend/src/stores/uiContextStore.ts` | 新建 |
| `flowcut_frontend/src/api/chat.ts` | 修改：新增 uiContext 参数 |
| `flowcut_frontend/src/components/generate/ChatPanel.tsx` | 修改：传递 uiContext |
| `flowcut_frontend/src/components/material/MaterialTab.tsx` | 修改：写入 uiContextStore |
| `flowcut_frontend/src/components/material/HighlightAssetLibrary.tsx` | 修改：写入 drama |
| `flowcut_frontend/src/components/creative/CreativeTab.tsx` | 修改：写入 uiContextStore |
| `flowcut_frontend/src/components/creative/HighlightCreativeLibrary.tsx` | 修改：写入 drama |
| `SimpleClaw/tests/test_highlight_optimization.py` | 新建：单元测试 |

---

### Task 1: Prompt 清理

**Files:**
- Modify: `SimpleClaw/Flowcut/workspace/Agent.md`
- Modify: `SimpleClaw/Flowcut/workspace/TOOL.md`
- Delete: `SimpleClaw/Flowcut/skills/drama_highlight/` (整目录)

**Interfaces:**
- Produces: 无废弃流程的干净 prompt，agent 高光入口收敛为 `create_cross_episode_highlights`

- [ ] **Step 1: 删除 drama_highlight skill 目录**

```bash
rm -rf SimpleClaw/Flowcut/skills/drama_highlight
```

确认目录已消失：`ls SimpleClaw/Flowcut/skills/` 只剩其他目录。

- [ ] **Step 2: 更新 Agent.md**

打开 `SimpleClaw/Flowcut/workspace/Agent.md`。

**删除**工作流中的步骤 3 和步骤 4（整段删掉）：
```
3. 用户选择提取高光视频 → 先调用 `load_skill(name="drama_highlight")`，再按 skill 流程调用高光工具
4. 用户要求"把某个 AI 漫剧全部批量跑高光" → 先调用 `list_highlight_assets` 定位原片/数字人，再调用 `create_highlight_batch`
```

**把原步骤 3 替换为**（插入到「用户选择拆解爆款视频」那步之后）：
```
3. 用户要「跑高光」/「跨集高光」→ 询问剧名和候选数量（默认 3），若需接数字人先调用 `list_highlight_assets(asset_type="digital_human_connector")` 确认素材，再调用 `create_cross_episode_highlights`
```

**删除**「高光资产库批量处理」整个规则块（从 `### 高光资产库批量处理` 到该块结尾）。

**新增**「界面上下文」规则块（追加到「规则」章节末尾）：
```markdown
### 界面上下文

每轮对话的系统层会注入 `[用户当前界面位置]` packet，包含 route、tab、drama 字段（有则有，无则省略）。
收到时，优先基于这些字段推断用户意图，无需用户重复说明所在位置。

### navigate_to 使用规则

- 用户询问某功能在哪里，或 agent 判断需要引导用户去某界面时，可调用 `navigate_to`
- 调用前必须先输出一句引导语（如「我帮你打开成片库」）
- 不要无故跳转；不要在同一轮内多次调用 `navigate_to`
```

- [ ] **Step 3: 更新 TOOL.md**

打开 `SimpleClaw/Flowcut/workspace/TOOL.md`。

**删除**内容生产链路中以下三行（整行含描述）：
- `extract_highlight_video：...`
- `list_highlight_assets：...`（内容生产链路部分；保留如有其他出现处 → 实际文件只有一处）
- `create_highlight_batch：...`
- `load_skill：...` 和 `generate_scripts：...` 保留（前者暂留以防其他 skill 使用；实际本项目已无其他 skill，可同时删除 load_skill/unload_skill 两条）

**在「内容生产链路」末尾追加**：
```
- navigate_to：agent 想主动引导用户跳转到某个界面时调用。调用前先用自然语言告知用户（如「我帮你打开成片库」），调用后不再重复说明。可跳转路由：`/`（首页）、`/material`、`/creative`、`/workspace/:scriptId`、`/dashboard`
```

**更新 create_cross_episode_highlights 描述**，在末尾补充：
```
；若需将成片接数字人，可传入 `connector_asset_id=<数字人素材ID>`（先用 list_highlight_assets 找到 ID）
```

- [ ] **Step 4: 验证**

- 搜索 Agent.md 确认不含 `load_skill`、`extract_highlight_video`、`create_highlight_batch` 关键词
- 搜索 TOOL.md 同上
- 确认 `skills/drama_highlight/` 目录不存在

- [ ] **Step 5: Commit**

```bash
git add SimpleClaw/Flowcut/workspace/Agent.md SimpleClaw/Flowcut/workspace/TOOL.md
git commit -m "chore(prompt): 移除单集高光/批量高光流程，收敛为跨集高光切片"
```

---

### Task 2: 扩展 create_cross_episode_highlights + creative_repo

**Files:**
- Modify: `SimpleClaw/Flowcut/tools/create_cross_episode_highlights.py`
- Modify: `SimpleClaw/Flowcut/storage/creative_repo.py`
- Modify: `SimpleClaw/Flowcut/runtime/executors.py`
- Test: `SimpleClaw/tests/test_highlight_optimization.py`

**Interfaces:**
- Produces:
  - `CreateCrossEpisodeHighlightsTool.prepare_task(drama_name, num_candidates, connector_asset_id)` → `TaskEnvelope` with `connector_asset_id` in payload
  - `CreativeRepository.create_cross_episode_job(..., connector_asset_id: int | None)` → `dict`

- [ ] **Step 1: 写失败测试**

新建 `SimpleClaw/tests/test_highlight_optimization.py`：

```python
"""单元测试：跨集高光 connector_asset_id 扩展 + navigate_to 工具。"""
import json
import pytest

pytestmark = pytest.mark.unit


# ── Task 2 tests ──────────────────────────────────────────────────────────────

class TestCreateCrossEpisodeHighlightsTool:
    def _make_tool(self):
        from unittest.mock import MagicMock
        from Flowcut.tools.create_cross_episode_highlights import CreateCrossEpisodeHighlightsTool
        runtime = MagicMock()
        return CreateCrossEpisodeHighlightsTool(runtime=runtime)

    @pytest.mark.asyncio
    async def test_connector_asset_id_in_payload(self):
        tool = self._make_tool()
        result = await tool.prepare_task(
            drama_name="斗破苍穹",
            num_candidates=2,
            connector_asset_id=42,
        )
        assert result.payload["connector_asset_id"] == 42

    @pytest.mark.asyncio
    async def test_no_connector_asset_id_defaults_none(self):
        tool = self._make_tool()
        result = await tool.prepare_task(drama_name="斗破苍穹")
        assert result.payload.get("connector_asset_id") is None
```

- [ ] **Step 2: 运行确认失败**

```bash
cd SimpleClaw && uv run pytest tests/test_highlight_optimization.py::TestCreateCrossEpisodeHighlightsTool -v
```

期望：`FAILED` with `TypeError` (unexpected keyword `connector_asset_id`)。

- [ ] **Step 3: 修改工具**

在 `SimpleClaw/Flowcut/tools/create_cross_episode_highlights.py` 中：

在 `parameters["properties"]` 里新增：
```python
"connector_asset_id": {
    "type": "integer",
    "description": "数字人素材 ID（可选）；先用 list_highlight_assets 找到 ID 再传入",
},
```

在 `prepare_task` 签名改为：
```python
async def prepare_task(
    self, drama_name: str, num_candidates: int = 3,
    connector_asset_id: int | None = None, **_: Any,
) -> TaskEnvelope | ToolResult:
```

在返回 `TaskEnvelope` 的 `payload` 里新增一行：
```python
"connector_asset_id": connector_asset_id,
```

- [ ] **Step 4: 运行确认通过**

```bash
cd SimpleClaw && uv run pytest tests/test_highlight_optimization.py::TestCreateCrossEpisodeHighlightsTool -v
```

期望：`2 passed`。

- [ ] **Step 5: 修改 creative_repo**

在 `SimpleClaw/Flowcut/storage/creative_repo.py` 的 `create_cross_episode_job` 方法：

签名新增参数（在 `status` 之前）：
```python
connector_asset_id: int | None = None,
```

SQL 语句改为：
```python
await cur.execute(
    """
    INSERT INTO fc_creative
        (tenant_key, session_key, script_id, creative_type, batch_id,
         source_asset_id, connector_asset_id, status, label, highlight_start,
         highlight_reason_json, clip_plan_json, created_at, updated_at)
    VALUES (%s, %s, %s, 'continuous_cross_episode', %s, %s, %s, %s, %s,
            'NORMAL', %s, %s, %s, %s, %s)
    """,
    (
        tenant_key, session_key, script_id, batch_id,
        source_asset_id, connector_asset_id, status, highlight_start,
        highlight_reason_json, clip_plan_json, now, now,
    ),
)
```

- [ ] **Step 6: 修改 executors.py（highlight_plan executor）**

在 `SimpleClaw/Flowcut/runtime/executors.py` 的 `make_highlight_plan_executor` 内部 `execute` 函数中：

在读取 `batch_id` 那行附近，新增：
```python
connector_asset_id_raw = payload.get("connector_asset_id")
connector_asset_id: int | None = int(connector_asset_id_raw) if connector_asset_id_raw is not None else None
```

把 `create_cross_episode_job` 调用改为：
```python
creative = await creative_repo.create_cross_episode_job(
    tenant_key=tenant_key,
    session_key=session_key,
    script_id=None,
    batch_id=batch_id,
    source_asset_id=ep_index[cand.episode_no]["id"],
    clip_plan_json=json.dumps(clip_plan_dict, ensure_ascii=False),
    highlight_start=cand.local_start,
    highlight_reason_json=json.dumps(reason_dict, ensure_ascii=False),
    connector_asset_id=connector_asset_id,
)
```

- [ ] **Step 7: Commit**

```bash
cd SimpleClaw && git add Flowcut/tools/create_cross_episode_highlights.py Flowcut/storage/creative_repo.py Flowcut/runtime/executors.py tests/test_highlight_optimization.py
git commit -m "feat: create_cross_episode_highlights 支持 connector_asset_id（数字人衔接）"
```

---

### Task 3: navigate_to 工具

**Files:**
- Create: `SimpleClaw/Flowcut/tools/navigate_to.py`
- Modify: `SimpleClaw/Flowcut/api/container.py`
- Test: `SimpleClaw/tests/test_highlight_optimization.py` (追加)

**Interfaces:**
- Produces: `NavigateToTool` → `ToolResult` with `content={"ok": True, "navigate": {...}}`

- [ ] **Step 1: 追加测试**

在 `SimpleClaw/tests/test_highlight_optimization.py` 末尾追加：

```python
# ── Task 3 tests ──────────────────────────────────────────────────────────────

class TestNavigateToTool:
    def _make_tool(self):
        from Flowcut.tools.navigate_to import NavigateToTool
        return NavigateToTool()

    @pytest.mark.asyncio
    async def test_valid_route_returns_navigate_directive(self):
        tool = self._make_tool()
        result = await tool.execute(route="/creative")
        data = json.loads(result.content)
        assert data["ok"] is True
        assert data["navigate"]["route"] == "/creative"

    @pytest.mark.asyncio
    async def test_invalid_route_returns_error(self):
        tool = self._make_tool()
        result = await tool.execute(route="/admin/secrets")
        assert result.ok is False

    @pytest.mark.asyncio
    async def test_route_with_params(self):
        tool = self._make_tool()
        result = await tool.execute(route="/workspace/:scriptId", params={"scriptId": 7})
        data = json.loads(result.content)
        assert data["navigate"]["route"] == "/workspace/7"

    def test_needs_followup_is_false(self):
        from Flowcut.tools.navigate_to import NavigateToTool
        assert NavigateToTool.needs_followup is False
```

- [ ] **Step 2: 运行确认失败**

```bash
cd SimpleClaw && uv run pytest tests/test_highlight_optimization.py::TestNavigateToTool -v
```

期望：`FAILED` with `ModuleNotFoundError`。

- [ ] **Step 3: 新建工具文件**

新建 `SimpleClaw/Flowcut/tools/navigate_to.py`：

```python
"""navigate_to 工具：让 agent 主动触发前端页面跳转。"""
from __future__ import annotations

import json
import re
from typing import Any

from simpleclaw.tools.base import Tool, ToolResult

# 与前端 ALLOWED_ROUTE_PATTERNS 保持一致
_ALLOWED_PATTERNS: list[re.Pattern] = [
    re.compile(r"^/$"),
    re.compile(r"^/material(?:\?.*)?$"),
    re.compile(r"^/creative(?:\?.*)?$"),
    re.compile(r"^/workspace/[^/?]+(?:\?.*)?$"),
    re.compile(r"^/dashboard(?:\?.*)?$"),
]

# 占位符路由（含 :param）的模板匹配
_TEMPLATE_PATTERNS: list[re.Pattern] = [
    re.compile(r"^/workspace/:[^/?]+$"),
]


def _is_allowed(route: str) -> bool:
    return any(p.match(route) for p in _ALLOWED_PATTERNS)


def _fill_params(route: str, params: dict[str, Any]) -> str:
    def replace(m: re.Match) -> str:
        key = m.group(1)
        return str(params[key]) if key in params else m.group(0)
    return re.sub(r":(\w+)", replace, route)


class NavigateToTool(Tool):
    name = "navigate_to"
    description = (
        "主动引导用户跳转到指定前端页面。调用前必须先输出一句引导语。"
        "可用路由：/（首页）、/material（素材库）、/creative（成片库）、"
        "/workspace/:scriptId（脚本工作台）、/dashboard（数据看板）。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "route": {
                "type": "string",
                "description": "目标路由，如 /creative 或 /workspace/:scriptId",
            },
            "params": {
                "type": "object",
                "description": "路由参数，如 {\"scriptId\": 123}",
            },
            "mode": {
                "type": "string",
                "enum": ["push", "replace"],
                "description": "跳转模式，默认 push",
            },
        },
        "required": ["route"],
    }
    needs_followup = False
    tool_category = "navigate"
    read_only = True

    async def execute(
        self,
        *,
        route: str,
        params: dict[str, Any] | None = None,
        mode: str = "push",
        **_: Any,
    ) -> ToolResult:
        params = params or {}
        filled = _fill_params(route, params)
        if not _is_allowed(filled):
            return ToolResult(
                content=json.dumps(
                    {"ok": False, "error": f"不允许跳转到路由：{filled}"},
                    ensure_ascii=False,
                ),
                ok=False,
            )
        return ToolResult(
            content=json.dumps(
                {
                    "ok": True,
                    "navigate": {"route": filled, "params": params, "mode": mode},
                },
                ensure_ascii=False,
            ),
            ok=True,
        )
```

- [ ] **Step 4: 运行确认通过**

```bash
cd SimpleClaw && uv run pytest tests/test_highlight_optimization.py::TestNavigateToTool -v
```

期望：`4 passed`。

- [ ] **Step 5: 注册到 container，移除废弃注册**

在 `SimpleClaw/Flowcut/api/container.py` 中：

**删除** import 块中的这两行：
```python
from Flowcut.tools.create_highlight_batch import CreateHighlightBatchTool
from Flowcut.tools.extract_highlight_video import ExtractHighlightVideoTool
```

**新增** import：
```python
from Flowcut.tools.navigate_to import NavigateToTool
```

**删除** `tool_factories` 列表中的这两个 lambda：
```python
lambda _: ExtractHighlightVideoTool(
    runtime=runtime,
    ref_video_repo=ref_video_repo,
    script_repo=script_repo,
),
lambda _: CreateHighlightBatchTool(
    runtime=runtime,
    highlight_asset_repo=highlight_asset_repo,
    ref_video_repo=ref_video_repo,
    script_repo=script_repo,
    creative_repo=creative_repo,
),
```

**新增** 到 `tool_factories` 列表末尾（在最后一个 GetMaterialStatsTool 之后，`]` 之前）：
```python
lambda _: NavigateToTool(),
```

- [ ] **Step 6: Commit**

```bash
cd SimpleClaw && git add Flowcut/tools/navigate_to.py Flowcut/api/container.py tests/test_highlight_optimization.py
git commit -m "feat: 新增 navigate_to 工具，移除废弃工具注册"
```

---

### Task 4: 后端 UI 上下文注入

**Files:**
- Modify: `SimpleClaw/Flowcut/context/providers.py`
- Modify: `SimpleClaw/Flowcut/agent/main_agent.py`
- Modify: `SimpleClaw/Flowcut/storage/session_store.py`
- Modify: `SimpleClaw/Flowcut/api/routes/chat.py`
- Test: `SimpleClaw/tests/test_highlight_optimization.py` (追加)

**Interfaces:**
- Produces:
  - `UIContextAttentionProvider.set_ui_context(ui_context: dict | None)` → None
  - `UIContextAttentionProvider.collect_attention(ctx)` → `list[AttentionPacket]`
  - `SessionStore.set_turn_context(..., ui_context: dict | None = None)` → None

- [ ] **Step 1: 追加测试**

在 `SimpleClaw/tests/test_highlight_optimization.py` 末尾追加：

```python
# ── Task 4 tests ──────────────────────────────────────────────────────────────

class TestUIContextAttentionProvider:
    def _make_provider(self):
        from Flowcut.context.providers import UIContextAttentionProvider
        return UIContextAttentionProvider()

    @pytest.mark.asyncio
    async def test_no_context_returns_empty(self):
        provider = self._make_provider()
        from simpleclaw.context.providers import ContextBuildContext
        ctx = ContextBuildContext(history=[])
        packets = await provider.collect_attention(ctx)
        assert packets == []

    @pytest.mark.asyncio
    async def test_full_context_returns_packet(self):
        provider = self._make_provider()
        provider.set_ui_context({"route": "/creative", "tab": "highlight", "drama": "斗破苍穹"})
        from simpleclaw.context.providers import ContextBuildContext
        ctx = ContextBuildContext(history=[])
        packets = await provider.collect_attention(ctx)
        assert len(packets) == 1
        assert "/creative" in packets[0].content
        assert "highlight" in packets[0].content
        assert "斗破苍穹" in packets[0].content

    @pytest.mark.asyncio
    async def test_partial_context_no_drama(self):
        provider = self._make_provider()
        provider.set_ui_context({"route": "/material", "tab": "episode_source"})
        from simpleclaw.context.providers import ContextBuildContext
        ctx = ContextBuildContext(history=[])
        packets = await provider.collect_attention(ctx)
        assert len(packets) == 1
        assert "drama" not in packets[0].content

    @pytest.mark.asyncio
    async def test_set_none_clears_context(self):
        provider = self._make_provider()
        provider.set_ui_context({"route": "/creative"})
        provider.set_ui_context(None)
        from simpleclaw.context.providers import ContextBuildContext
        ctx = ContextBuildContext(history=[])
        packets = await provider.collect_attention(ctx)
        assert packets == []
```

- [ ] **Step 2: 运行确认失败**

```bash
cd SimpleClaw && uv run pytest tests/test_highlight_optimization.py::TestUIContextAttentionProvider -v
```

期望：`FAILED` with `ImportError`。

- [ ] **Step 3: 实现 UIContextAttentionProvider**

在 `SimpleClaw/Flowcut/context/providers.py` 末尾追加：

```python
from simpleclaw.context.providers import AttentionPacket


class UIContextAttentionProvider:
    """每轮注入用户当前界面位置（route / tab / drama）作为 attention packet。

    不持久化到对话历史；ContextBuilder 每轮重新注入当前状态。
    """

    def __init__(self) -> None:
        self._ui_context: dict | None = None

    def set_ui_context(self, ui_context: dict | None) -> None:
        self._ui_context = ui_context or None

    async def collect_attention(
        self,
        ctx: "ContextBuildContext",
    ) -> list[AttentionPacket]:
        del ctx
        if not self._ui_context:
            return []
        lines = ["[用户当前界面位置]"]
        if route := self._ui_context.get("route"):
            lines.append(f"route: {route}")
        if tab := self._ui_context.get("tab"):
            lines.append(f"tab: {tab}")
        if drama := self._ui_context.get("drama"):
            lines.append(f"drama: {drama}")
        if len(lines) == 1:
            return []
        return [
            AttentionPacket(
                content="\n".join(lines),
                source="ui_context",
                lifetime="always",
                placement="before_last_user",
                role="system",
            )
        ]
```

需要在文件顶部的 `TYPE_CHECKING` 块中补充 `ContextBuildContext` 的 import（它已在 `from simpleclaw.context.providers import ContextBuildContext, ContextSection` 里，但需确认运行时可用，直接放在正常 import 区）：

将文件头部的 import 改为非 TYPE_CHECKING 的运行时 import：
```python
from simpleclaw.context.providers import (
    AttentionPacket,
    ContextBuildContext,
    ContextSection,
)
```

- [ ] **Step 4: 运行确认通过**

```bash
cd SimpleClaw && uv run pytest tests/test_highlight_optimization.py::TestUIContextAttentionProvider -v
```

期望：`4 passed`。

- [ ] **Step 5: 挂载到 MainAgent（per-session 实例）**

在 `SimpleClaw/Flowcut/agent/main_agent.py` 中：

**在 import 区**新增：
```python
from Flowcut.context.providers import UIContextAttentionProvider
```

**在 `make_context_builder` 中**，在创建 `ContextBuilder` 之前新增一行：
```python
        ui_ctx_provider = UIContextAttentionProvider()
```

**把 `attention_providers` 参数**改为：
```python
        attention_providers=[ui_ctx_provider],
```

⚠️ 注意：provider 是 per-session 实例（每次 `make_context_builder` 调用都新建），不挂在 `self` 上。
`ContextBuilder` 持有这个实例，`set_turn_context` 通过 `loop.context_builder` 访问它。

- [ ] **Step 6: 扩展 SessionStore.set_turn_context**

在 `SimpleClaw/Flowcut/storage/session_store.py` 中，`set_turn_context` 方法签名新增参数：
```python
ui_context: dict | None = None,
```

在方法体末尾（`for tool in loop.tool_registry.tools` 循环结束后）追加：
```python
        if ui_context is not None and loop.context_builder is not None:
            for provider in loop.context_builder._attention_providers:
                if hasattr(provider, "set_ui_context"):
                    provider.set_ui_context(ui_context)
```

注意：`_attention_providers` 是 `ContextBuilder` 的私有字段，此处直接访问是有意为之——避免修改 simpleclaw 核心，且两者在同一 monorepo 内。

- [ ] **Step 7: 读取 HTTP 请求中的 ui_context**

在 `SimpleClaw/Flowcut/api/routes/chat.py` 的 `agent_chat` 函数中：

在读取 `query` 那行之后新增：
```python
    ui_context: dict | None = payload.get("ui_context") or None
    if ui_context is not None and not isinstance(ui_context, dict):
        ui_context = None
```

在 `c.sessions.set_turn_context(...)` 调用中新增 `ui_context=ui_context` 参数：
```python
    c.sessions.set_turn_context(
        session_key,
        tenant_key=tenant_key,
        query=query,
        ui_context=ui_context,
    )
```

- [ ] **Step 8: Commit**

```bash
cd SimpleClaw && git add Flowcut/context/providers.py Flowcut/agent/main_agent.py Flowcut/storage/session_store.py Flowcut/api/routes/chat.py tests/test_highlight_optimization.py
git commit -m "feat: 后端 UI 上下文注入（UIContextAttentionProvider + set_turn_context 扩展）"
```

---

### Task 5: 前端 uiContextStore + ChatPanel

**Files:**
- Create: `flowcut_frontend/src/stores/uiContextStore.ts`
- Modify: `flowcut_frontend/src/api/chat.ts`
- Modify: `flowcut_frontend/src/components/generate/ChatPanel.tsx`

**Interfaces:**
- Produces:
  - `useUIContextStore()` → `{ setUIContext(ctx), setDrama(drama) }`
  - `streamChat({ ..., uiContext? })` → cancel fn（接口不变，新增可选字段）

- [ ] **Step 1: 新建 uiContextStore**

新建 `flowcut_frontend/src/stores/uiContextStore.ts`：

```typescript
import { create } from 'zustand'

export interface UIContext {
  route: string
  tab?: string
  drama?: string
}

interface UIContextState {
  ctx: UIContext
  setUIContext: (ctx: UIContext) => void
  setDrama: (drama: string | null) => void
}

export const useUIContextStore = create<UIContextState>((set) => ({
  ctx: { route: '/' },
  setUIContext: (ctx) => set({ ctx }),
  setDrama: (drama) =>
    set((state) => ({
      ctx: drama !== null ? { ...state.ctx, drama } : { route: state.ctx.route, tab: state.ctx.tab },
    })),
}))
```

- [ ] **Step 2: 更新 chat.ts**

在 `flowcut_frontend/src/api/chat.ts` 的 `StreamChatParams` interface 中新增：
```typescript
  uiContext?: { route: string; tab?: string; drama?: string }
```

在 `streamChat` 函数中，从 params 解构出 `uiContext`：
```typescript
const { tenantKey, sessionKey, query, onChunk, onDone, onError, onToolResult, uiContext } = params
```

POST body 改为：
```typescript
    body: JSON.stringify({
      tenant_key: tenantKey,
      session_key: sessionKey,
      query,
      ...(uiContext ? { ui_context: uiContext } : {}),
    }),
```

- [ ] **Step 3: 更新 ChatPanel.tsx**

在 `flowcut_frontend/src/components/generate/ChatPanel.tsx` 中：

**新增 import**（在其他 store import 附近）：
```typescript
import { useUIContextStore } from '../../stores/uiContextStore'
```

**在组件函数顶部**（在其他 state 声明附近）新增：
```typescript
  const uiCtx = useUIContextStore((s) => s.ctx)
```

**在 `streamChat` 调用中**（`handleSend` 里）新增 `uiContext` 参数：
```typescript
    cancelRef.current = streamChat({
      tenantKey: TENANT_KEY,
      sessionKey,
      query,
      uiContext: uiCtx,
      onChunk: ...
```

- [ ] **Step 4: 手动验证**

启动前端开发服务器（`cd flowcut_frontend && npm run dev`），打开浏览器，打开 DevTools Network tab，发送任意一条聊天消息，检查 POST `/agent/chat` 的 request body 中包含 `ui_context: {"route": "/"}` 字段。

- [ ] **Step 5: Commit**

```bash
git add flowcut_frontend/src/stores/uiContextStore.ts flowcut_frontend/src/api/chat.ts flowcut_frontend/src/components/generate/ChatPanel.tsx
git commit -m "feat(fe): uiContextStore + ChatPanel 附带界面上下文"
```

---

### Task 6: 前端组件写入 uiContextStore

**Files:**
- Modify: `flowcut_frontend/src/components/material/MaterialTab.tsx`
- Modify: `flowcut_frontend/src/components/material/HighlightAssetLibrary.tsx`
- Modify: `flowcut_frontend/src/components/creative/CreativeTab.tsx`
- Modify: `flowcut_frontend/src/components/creative/HighlightCreativeLibrary.tsx`

**Interfaces:**
- Consumes: `useUIContextStore().setUIContext` 和 `useUIContextStore().setDrama`（来自 Task 5）

- [ ] **Step 1: MaterialTab**

在 `flowcut_frontend/src/components/material/MaterialTab.tsx` 中：

**新增 import**：
```typescript
import { useUIContextStore } from '../../stores/uiContextStore'
```

**在组件函数内**（`activeSubTab` 之后）新增：
```typescript
  const setUIContext = useUIContextStore((s) => s.setUIContext)
```

**新增 useEffect**（在现有 useEffect 之后）：
```typescript
  useEffect(() => {
    setUIContext({ route: '/material', tab: activeSubTab })
  }, [activeSubTab, setUIContext])
```

- [ ] **Step 2: HighlightAssetLibrary**

在 `flowcut_frontend/src/components/material/HighlightAssetLibrary.tsx` 中：

**新增 import**：
```typescript
import { useUIContextStore } from '../../stores/uiContextStore'
```

**在组件函数内**（`activeDrama` state 之后）新增：
```typescript
  const setDrama = useUIContextStore((s) => s.setDrama)
```

**在现有处理 `activeDrama` 变化的 useEffect 后**（或在对应 `setActiveDrama` 调用处）新增 useEffect：
```typescript
  useEffect(() => {
    setDrama(activeDrama)
  }, [activeDrama, setDrama])
```

注意：`activeDrama` 在切换 mode 时会被重置为 null（现有 `useEffect([mode])` 调用了 `setActiveDrama(null)`），这会触发 `setDrama(null)` 自动清除 drama —— 行为正确，无需额外处理。

- [ ] **Step 3: CreativeTab**

在 `flowcut_frontend/src/components/creative/CreativeTab.tsx` 中：

**新增 import**：
```typescript
import { useUIContextStore } from '../../stores/uiContextStore'
```

**在组件函数内**新增：
```typescript
  const setUIContext = useUIContextStore((s) => s.setUIContext)
```

**新增 useEffect**（在现有 tab 同步 useEffect 之后）：
```typescript
  useEffect(() => {
    setUIContext({ route: '/creative', tab: activeSubTab })
  }, [activeSubTab, setUIContext])
```

- [ ] **Step 4: HighlightCreativeLibrary**

在 `flowcut_frontend/src/components/creative/HighlightCreativeLibrary.tsx` 中：

**新增 import**：
```typescript
import { useUIContextStore } from '../../stores/uiContextStore'
```

**在组件函数内**（`activeDrama` state 之后）新增：
```typescript
  const setDrama = useUIContextStore((s) => s.setDrama)
```

**新增 useEffect**：
```typescript
  useEffect(() => {
    setDrama(activeDrama)
  }, [activeDrama, setDrama])
```

- [ ] **Step 5: 手动验证**

1. 切换到素材库 → 原片库 tab → 下钻进「斗破苍穹」
2. 在 ChatPanel 发一条消息
3. 检查 Network 请求 body：`ui_context` 应为 `{"route": "/material", "tab": "episode_source", "drama": "斗破苍穹"}`
4. 返回剧名列表，再发消息：`ui_context` 应无 `drama` 字段
5. 切换到成片库 → 高光 tab，发消息：`{"route": "/creative", "tab": "highlight"}`

- [ ] **Step 6: 全量单测**

```bash
cd SimpleClaw && uv run pytest tests/test_highlight_optimization.py -v
```

期望：全部测试通过。

- [ ] **Step 7: Commit**

```bash
git add flowcut_frontend/src/components/material/MaterialTab.tsx flowcut_frontend/src/components/material/HighlightAssetLibrary.tsx flowcut_frontend/src/components/creative/CreativeTab.tsx flowcut_frontend/src/components/creative/HighlightCreativeLibrary.tsx
git commit -m "feat(fe): 各 tab/库组件写入 uiContextStore，agent 可感知用户界面位置"
```
