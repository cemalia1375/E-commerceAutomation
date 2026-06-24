# 高光数字人优化 — 设计 spec

- 日期：2026-06-24
- 模块：`SimpleClaw/Flowcut/` + `flowcut_frontend/`
- 状态：设计已确认，待 writing-plans

## 背景与目标

聚焦三个具体问题：

1. **Prompt 清理**：单集高光提取（B）和批量高光跑量（C）是跨集高光切片（D）上线前的历史遗留，需从 prompt 和工具层彻底清除，收敛为 `create_cross_episode_highlights` 作为唯一高光生成入口。
2. **前端 UI 上下文注入**：Agent 当前对用户所在界面一无所知。每次对话应把用户当前路由、激活 tab、下钻剧名通过独立字段发给后端，后端注入为 attention packet，让 agent 能基于用户所在位置推断意图。
3. **Agent 主动导航工具**：新增 `navigate_to` 工具，让 agent 能在对话中主动引导用户跳转到指定界面，而不依赖工具 side-effect 携带 navigate 指令。

---

## Section 1：Prompt 清理

### 删除

**`Flowcut/workspace/Agent.md`**
- 工作流步骤 3（`load_skill(drama_highlight)` + `extract_highlight_video`）
- 工作流步骤 4（`create_highlight_batch` 批量高光）
- 「高光资产库批量处理」规则块整体移除

**`Flowcut/workspace/TOOL.md`**
- `extract_highlight_video` 工具描述
- `create_highlight_batch` 工具描述
- `load_skill` / `unload_skill`（若无其他 skill 使用则一并移除）

**`Flowcut/skills/drama_highlight/`**
- 整目录删除（scene skill 已无触发入口）

### 保留并更新

**`list_highlight_assets`** 保留：仍可用于「有哪些剧/数字人资产」的发现查询。

**`Agent.md` 高光工作流更新为：**
1. 用户提到「跑高光」→ 询问剧名和候选数量（默认 3，封顶 10）
2. 若用户需要接数字人 → 先调 `list_highlight_assets(asset_type="digital_human_connector", query="<关键词>")` 确认资产
3. 调用 `create_cross_episode_highlights(drama_name=..., num_candidates=N[, connector_asset_id=...])`
4. 告知用户已开始，结果将出现在成片库高光区

---

## Section 2：`create_cross_episode_highlights` 扩展

### 工具签名

```
create_cross_episode_highlights(
    drama_name: str,
    num_candidates: int = 3,
    connector_asset_id: int | None = None   # 新增：数字人素材 ID
)
```

### 后端影响

- `Flowcut/tools/create_cross_episode_highlights.py`：parameters schema 新增 `connector_asset_id`（optional integer）
- `HIGHLIGHT_PLAN` executor Phase 3 落库时，若 `connector_asset_id` 非空，`fc_creative` 记录带上该字段；`VIDEO_COMPOSE` 阶段（当前 stub）预留读取该字段进行数字人片段拼接
- `TOOL.md` 补充说明：用户要接数字人时传入 `connector_asset_id`，纯高光切片则不传

### Agent.md 对话规则补充

- 用户说「接数字人 XX」→ 先 `list_highlight_assets` 确认资产，再带 `connector_asset_id` 调用
- 若数字人关键词匹配多个，让用户确认后再调用

---

## Section 3：UI 上下文注入

### 数据结构

```typescript
interface UIContext {
  route: string      // 当前路由，如 "/creative"、"/material"、"/"
  tab?: string       // 当前激活 tab，如 "highlight" | "episode_source" | "digital_human_connector"
  drama?: string     // 下钻后的剧名，如 "斗破苍穹"；未下钻时不传
}
```

### 前端变更

**新建 `src/stores/uiContextStore.ts`（Zustand）**
- 暴露 `setUIContext(ctx: UIContext)` 和 `getUIContext(): UIContext`

**各组件写入时机：**
| 组件 | 写入时机 | 写入内容 |
|------|---------|---------|
| `MaterialTab` | tab 切换 | `{route: "/material", tab: activeTab}` |
| `HighlightAssetLibrary` | 剧名下钻 / 返回 | 追加或清除 `drama` |
| `CreativeTab` | tab 切换 | `{route: "/creative", tab: activeTab}` |
| `HighlightCreativeLibrary` | 剧名下钻 / 返回 | 追加或清除 `drama` |
| `ChatPanel` | 组件挂载 | 写当前 `window.location.pathname` 作兜底 |

**`src/api/chat.ts` 变更：**
- `StreamChatParams` 新增 `uiContext?: UIContext`
- POST body 新增 `ui_context` 字段（undefined 时不传）
- `ChatPanel.handleSend` 读取 `uiContextStore`，传入 `streamChat`

### 后端变更

**`Flowcut/api/routes/chat.py`**
- 请求体 `ChatRequest` 新增 `ui_context: dict | None = None`

**`Flowcut/context/providers.py`**（或独立新文件）
- 新增 `UIContextProvider`，接收 `ui_context` dict
- 若非空，生成 attention packet：
  ```
  [用户当前界面位置]
  route: /creative
  tab: highlight
  drama: 斗破苍穹
  ```
  配置：`placement: before_last_user`，`strategy: always`
- 注入到 `MainAgent.make_context_builder()` 的 provider 列表

### 上下文生命周期（重要设计决策）

- **不持久化到对话历史**：UI context packet 是「当前状态」而非「历史事件」，每轮由 ContextBuilder 重新注入，旧轮次的位置信息不进 context history。SimpleClaw attention packet 机制天然支持此行为，无需额外处理。
- **Agent.md 新增规则**：收到 `[用户当前界面位置]` 时，优先基于该位置推断用户意图，无需用户重复说明所在位置。

---

## Section 4：`navigate_to` 工具

### 工具定义

新建 `Flowcut/tools/navigate_to.py`：

```python
navigate_to(
    route: str,            # 目标路由，如 "/creative"、"/material"
    params: dict = {},     # 路由参数，如 {"scriptId": 123}
    mode: str = "push"     # "push" | "replace"
)
```

### 路由白名单

```python
ALLOWED_ROUTES = {
    "/",
    "/material",
    "/creative",
    "/workspace/:scriptId",
    "/dashboard",
}
```

后端校验 route 在白名单内；`:param` 占位符由 `params` 填充，填充后再校验模式匹配。

### 返回值

```json
{"ok": true, "navigate": {"route": "/creative", "params": {}, "mode": "push"}}
```

前端 `ChatPanel.handleToolResult` 已有 navigate 指令处理逻辑，无需改动前端。

### 关键配置

- `needs_followup = False`（fire-and-forget，导航结果不回注 LLM）
- `persist_to_history = False`（navigate 指令是 UI 行为，不进对话历史）

### 注册

- `Flowcut/api/container.py` 注册 `NavigateToTool`

### TOOL.md 新增

```
- navigate_to：agent 想主动引导用户跳转到某个界面时调用。调用前必须先输出一句引导语（如「我帮你打开成片库」）；调用后不再重复说明。
```

### Agent.md 新增规则

- 用户询问某功能在哪里，或 agent 判断需要引导用户去某界面时，可调用 `navigate_to`
- 不要无故跳转；每次跳转前必须有引导语
- 不要在同一轮内连续调用多次 `navigate_to`

---

## 改动文件清单

### 后端（`SimpleClaw/Flowcut/`）
| 文件 | 操作 |
|------|------|
| `workspace/Agent.md` | 删除 B/C 流程，更新高光工作流，新增 UI context 规则和 navigate_to 规则 |
| `workspace/TOOL.md` | 删除 extract_highlight_video、create_highlight_batch、load_skill；新增 navigate_to |
| `workspace/compliance.md` | 不变 |
| `workspace/SOUL.md` | 不变 |
| `skills/drama_highlight/` | 整目录删除 |
| `tools/create_cross_episode_highlights.py` | 新增 `connector_asset_id` 参数 |
| `tools/navigate_to.py` | 新建 |
| `api/routes/chat.py` | `ChatRequest` 新增 `ui_context` 字段 |
| `context/providers.py` | 新增 `UIContextProvider` |
| `api/container.py` | 注册 `NavigateToTool`，挂载 `UIContextProvider` |
| `runtime/executors.py` | Phase 3 落库时透传 `connector_asset_id` |

### 前端（`flowcut_frontend/`）
| 文件 | 操作 |
|------|------|
| `src/stores/uiContextStore.ts` | 新建 |
| `src/api/chat.ts` | 新增 `uiContext` 字段 |
| `src/components/generate/ChatPanel.tsx` | 读取 uiContextStore，传给 streamChat |
| `src/components/material/MaterialTab.tsx` | tab 切换时写入 uiContextStore |
| `src/components/material/HighlightAssetLibrary.tsx` | 下钻/返回时更新 drama |
| `src/components/creative/CreativeTab.tsx` | tab 切换时写入 uiContextStore |
| `src/components/creative/HighlightCreativeLibrary.tsx` | 下钻/返回时更新 drama |
