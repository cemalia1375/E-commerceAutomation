# 高光库剧名入口下钻 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把素材库（仅原片库 tab）和成片库的高光片段从「全部平铺」改成「剧名入口卡 → 页面内下钻看该剧内容」的两级结构。

**Architecture:** 纯前端改动，两个 React 组件各自新增一个 `activeDrama: string | null` 本地 state 做页面内下钻；入口层渲染剧名入口卡（名称+数量），下钻层复用现有卡片网格 + 顶部返回栏。移除两处顶部「按剧名筛选」下拉。数字人库 tab 行为完全不变。

**Tech Stack:** React 19 + TypeScript + Ant Design 6 + CSS Modules（Vite）。

**验证策略：** 项目前端无测试框架，本计划不引入测试栈（避免投机性改动）。每个任务的验证门为 `npm run build`（tsc 类型检查）+ `npm run lint`，最终任务做一次手动冒烟。

---

### Task 1: 素材库 — 原片库剧名入口下钻

**Files:**
- Modify: `flowcut_frontend/src/components/material/HighlightAssetLibrary.tsx`
- Modify: `flowcut_frontend/src/components/material/HighlightAssetLibrary.module.css`

实现要点（基于现有代码，行号以当前文件为准）：

1. **新增 state**：在 `mode` state 附近加 `const [activeDrama, setActiveDrama] = useState<string | null>(null)`。
2. **重置 activeDrama**：现有 `useEffect(..., [mode])`（约 82-86 行）内加 `setActiveDrama(null)`，使切 tab 回到入口层。
3. **移除剧名下拉**：删除顶部 `mode === 'episode_source' && (<Select ... 按剧名筛选 .../>)`（约 205-215 行）；删除 `dramaFilter` state（约 62 行）、`dramaOptions` useMemo（约 88-96 行）；删除 `visibleAssets` 中 `if (dramaFilter !== undefined ...) return false` 分支（约 101 行），并把 `visibleAssets` 的依赖数组从 `[assets, keyword, dramaFilter]` 改为 `[assets, keyword]`。清理因此孤立的 `Select` import（仅当数字人库上传区不再用 Select 时——注意数字人库上传区仍用 `Select`，故 import 保留）。
4. **入口分组数据**：保留 `groupAssets`。在 `grouped` 之后，针对原片库入口层计算入口卡数据（直接复用 `grouped`，它已是 `[name, items][]`）。
5. **渲染分支**（替换现有 `content` 区的 `grouped.map(...)`，约 292-379 行）：
   - 计算 `const isEntryLevel = mode === 'episode_source' && activeDrama === null`。
   - **入口层（`isEntryLevel`）**：渲染入口卡网格，遍历 `grouped`，每张卡 `onClick={() => setActiveDrama(group)}`，显示 `group` + `<Tag>{items.length}</Tag>`。用新样式类 `styles.entryGrid` / `styles.entryCard`。
   - **下钻层 / 数字人库**：
     - 若 `mode === 'episode_source' && activeDrama !== null`：先渲染返回栏 `<div className={styles.backBar}>` 含一个 `<Button type="link" onClick={() => setActiveDrama(null)}>← 返回</Button>` 和当前剧名；网格只渲染 `grouped` 中 `group === activeDrama` 的那一组的 `items`（沿用现有卡片 JSX 与「选择本组」按钮）。
     - 若 `mode === 'digital_human_connector'`：保持现有 `grouped.map(...)` 平铺渲染，完全不变。
   - 现有的单组 `<section>` + `styles.grid` + 卡片 JSX（含 Checkbox、video、删除等）原样复用于下钻层与数字人库。
6. **搜索/汇总**：`summary`（约 293-295 行）与搜索框逻辑保留；搜索在入口层自然过滤掉无匹配资产的剧（因为 `visibleAssets` 已过滤，空组不出现在 `grouped`）。

**CSS（`HighlightAssetLibrary.module.css`）新增：**
- `.entryGrid`：与现有 `.grid` 类似的响应式网格容器。
- `.entryCard`：可点击卡片，padding、圆角、边框、hover 高亮（`cursor: pointer`），内部剧名加粗 + 数量 Tag 右对齐。
- `.backBar`：flex 行，返回按钮 + 剧名，下边距。

- [ ] **Step 1: 改 TSX**

按上述要点修改 `HighlightAssetLibrary.tsx`：新增 `activeDrama` state、`useEffect` 重置、移除剧名下拉及相关 state/memo/过滤分支、改 `content` 区渲染分支为入口层/下钻层/数字人库三态。

- [ ] **Step 2: 加 CSS**

在 `HighlightAssetLibrary.module.css` 末尾新增 `.entryGrid`、`.entryCard`、`.backBar` 三个类，复用文件内现有配色/圆角变量风格。

- [ ] **Step 3: 类型检查 + lint**

Run:
```bash
cd flowcut_frontend && npm run build && npm run lint
```
Expected: build 通过（无 tsc 报错），lint 无新增错误。若报「`dramaFilter`/`dramaOptions` 已声明但未使用」或 `Select` 未使用，回到 Step 1 清理孤立引用。

- [ ] **Step 4: Commit**

```bash
git add flowcut_frontend/src/components/material/HighlightAssetLibrary.tsx flowcut_frontend/src/components/material/HighlightAssetLibrary.module.css
git commit -m "feat(fe): 素材库原片库改为剧名入口下钻"
```

---

### Task 2: 成片库 — 剧名入口下钻

**Files:**
- Modify: `flowcut_frontend/src/components/creative/HighlightCreativeLibrary.tsx`
- Modify: `flowcut_frontend/src/components/creative/HighlightCreativeLibrary.module.css`

实现要点（基于当前文件）：

1. **新增 state**：在 `dramaFilter` 附近加 `const [activeDrama, setActiveDrama] = useState<string | null>(null)`。
2. **移除剧名下拉**：删除顶部「按剧名筛选」`<Select>`（约 153-161 行）；删除 `dramaFilter` state（约 63 行）、`dramaOptions` useMemo（约 70-80 行）；删除 `rows` 链中 `if (dramaFilter !== undefined ...) return false` 分支（约 86 行）。
3. **入口分组**：在 `rows` 之后新增按剧名分组：
   ```ts
   const dramaGroups = useMemo(() => {
     const groups: Record<string, Creative[]> = {}
     for (const c of rows) {
       const key = c.sourceDramaName || '未命名剧集'
       if (!groups[key]) groups[key] = []
       groups[key].push(c)
     }
     return Object.entries(groups).sort(([a], [b]) => a.localeCompare(b, 'zh-Hans-CN'))
   }, [rows])
   ```
   注意：`rows` 当前是普通常量（非 useMemo）。若直接放进 `useMemo` 依赖会有「依赖项是每次新建数组」的告警——把 `rows` 也提为 `useMemo`（依赖 `[creatives, typeFilter, keyword]`），再让 `dramaGroups` 依赖 `[rows]`。
4. **渲染分支**（替换现有 `styles.list` 区，约 165-277 行）：
   - **入口层（`activeDrama === null`）**：渲染入口卡网格，遍历 `dramaGroups`，每卡 `onClick={() => setActiveDrama(name)}`，显示剧名 + 成片数量。空时显示「暂无高光成片记录」。
   - **下钻层（`activeDrama !== null`）**：顶部返回栏（`← 返回` → `setActiveDrama(null)` + 剧名）；列表只渲染 `dramaGroups` 中 `name === activeDrama` 那组的成片，沿用现有 `article` 卡片 JSX（约 179-275 行整块）。
5. **顶部 count**：`共 {rows.length} 条高光` 保留。

**CSS（`HighlightCreativeLibrary.module.css`）新增：** `.entryGrid`、`.entryCard`、`.backBar`（同 Task 1 语义，按本文件现有风格）。

- [ ] **Step 1: 改 TSX**

按上述要点修改 `HighlightCreativeLibrary.tsx`：新增 `activeDrama`，移除剧名下拉及相关 state/memo/过滤，`rows` 提为 useMemo，新增 `dramaGroups`，`list` 区改为入口层/下钻层两态。

- [ ] **Step 2: 加 CSS**

在 `HighlightCreativeLibrary.module.css` 末尾新增 `.entryGrid`、`.entryCard`、`.backBar`。

- [ ] **Step 3: 类型检查 + lint**

Run:
```bash
cd flowcut_frontend && npm run build && npm run lint
```
Expected: build 通过，lint 无新增错误。清理 `dramaFilter`/`dramaOptions` 等孤立引用。

- [ ] **Step 4: Commit**

```bash
git add flowcut_frontend/src/components/creative/HighlightCreativeLibrary.tsx flowcut_frontend/src/components/creative/HighlightCreativeLibrary.module.css
git commit -m "feat(fe): 成片库改为剧名入口下钻"
```

---

### Task 3: 手动冒烟验证

**Files:** 无（仅运行与人工核对）

- [ ] **Step 1: 启动 dev server**

Run:
```bash
cd flowcut_frontend && npm run dev
```

- [ ] **Step 2: 逐项核对验收标准**

在浏览器中确认：
1. 素材库「原片库」默认显示剧名入口卡（名称+数量）；点某剧进入只看该剧资产；「← 返回」回到入口列表。
2. 素材库「数字人库」与改动前一致（平铺分组、上传区角色选择、删除等）。
3. 成片库默认显示剧名入口卡；点进入只看该剧成片；可返回。
4. 两个组件顶部均无「按剧名筛选」下拉。
5. 搜索框 / 类型筛选在对应层级正常生效；切 tab 自动回到入口层。

- [ ] **Step 3: 如发现问题**

回到对应 Task 修复后重跑 Step 1-2，无需额外 commit 直到修复完成再补提交。

---

## Self-Review

- **Spec coverage**：导航方式（页面内下钻）✓Task1/2；入口卡名称+数量 ✓；仅原片库改、数字人库不变 ✓Task1；成片库按 sourceDramaName 分组 ✓Task2；移除两处下拉 ✓Task1/2；复用现有卡片/网格样式 ✓；验收标准 ✓Task3。
- **Placeholder scan**：无 TBD/TODO；CSS 与渲染分支均给出具体类名与逻辑要点（非伪代码占位）。
- **Type consistency**：两组件统一用 `activeDrama: string | null` / `setActiveDrama`；统一新样式类名 `.entryGrid` / `.entryCard` / `.backBar`；缺省剧名键统一为「未命名剧集」（与素材库 `groupAssets` 现有缺省值一致）。
