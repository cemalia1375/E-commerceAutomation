# 高光库剧名入口下钻 — 设计 spec

日期：2026-06-16
范围：前端 `flowcut_frontend`，两个组件
- `src/components/material/HighlightAssetLibrary.tsx`（素材库）
- `src/components/creative/HighlightCreativeLibrary.tsx`（成片库）

## 目标

把高光片段从「全部平铺列出」改成「入口卡 → 点进去看内容」的两级结构。入口 = 剧名。点击具体剧名才进入看该剧下的卡片，而不是一次性铺开所有内容。

## 关键决策

- **导航方式**：页面内 state 下钻（不新增路由）。进入某剧后当前面板替换为该剧的卡片网格，顶部「← 返回」回到入口列表。
- **入口卡内容**：只显示「名称 + 数量」（剧名 + 该组条目数，如 `斗破苍穹 12`）。复用现有 Tag 展示数量。
- **应用范围**：
  - 素材库：**仅原片库 tab（`episode_source`）** 改为剧名入口。**数字人库 tab（`digital_human_connector`）保持现状平铺分组不变。**
  - 成片库：全部高光成片按 `sourceDramaName` 分组做剧名入口。
- **移除顶部「按剧名筛选」下拉**：两个组件都移除（入口卡已承担按剧名导航职责，保留会重复且语义混乱）。
- 复用现有卡片 JSX、`styles.grid` / `article` 卡片样式；入口卡相关新样式加到各自 `.module.css`。

## 一、素材库 `HighlightAssetLibrary`

### 状态

- 新增 `activeDrama: string | null`（`null` = 入口列表层；非空 = 已下钻进入该剧）。
- 切换 mode（tab）时重置 `activeDrama = null`（并入现有 `useEffect([mode])`）。

### 渲染分支

1. **原片库 + `activeDrama === null`**：渲染入口卡列表。
   - 数据来源：对 `visibleAssets`（经搜索过滤后）按 `dramaName || '未命名剧集'` 分组，复用/改造现有 `groupAssets`。
   - 每张入口卡显示剧名 + 资产数量，点击 → `setActiveDrama(剧名)`。
   - 搜索框在此层按剧名过滤入口卡。
2. **原片库 + `activeDrama !== null`**：渲染该剧的卡片网格。
   - 顶部加一行：「← 返回」按钮（`setActiveDrama(null)`）+ 当前剧名。
   - 网格内容 = `visibleAssets` 中 `dramaName` 等于 `activeDrama` 的资产，复用现有卡片 JSX 与 `styles.grid`、`styles.card`。
   - 搜索框在此层按资产名/集数过滤当前剧的卡片。
3. **数字人库（任意 `activeDrama`）**：完全走现有 `groupAssets(visibleAssets, mode)` 平铺逻辑，不改动。

### 移除

- 顶部 `mode === 'episode_source'` 时的「按剧名筛选」`<Select>`（含 `dramaFilter` state、`dramaOptions`、`visibleAssets` 中的 `dramaFilter` 过滤分支）。
- 因移除产生的孤立 import / 变量一并清理。

### 不变

- 「删除选中」「上传资产」区、批量删除、单卡删除、时长加载等逻辑不动。
- 「选择本组 / 取消本组」按钮：保留在下钻层的网格区（针对当前剧）。入口卡层不显示该按钮。

## 二、成片库 `HighlightCreativeLibrary`

### 状态

- 新增 `activeDrama: string | null`。

### 渲染分支

1. **`activeDrama === null`**：入口卡列表。
   - 对 `rows`（经搜索 + 类型过滤后的高光成片）按 `sourceDramaName || '未命名剧集'` 分组。
   - 每张入口卡显示剧名 + 成片数量，点击 → `setActiveDrama(剧名)`。
2. **`activeDrama !== null`**：渲染该剧下的成片卡片。
   - 顶部加「← 返回」+ 剧名。
   - 内容 = `rows` 中 `sourceDramaName`（缺省归「未命名剧集」）等于 `activeDrama` 的成片，复用现有 `article` 卡片 JSX。

### 移除

- 顶部「按剧名筛选」`<Select>`（含 `dramaFilter` state、`dramaOptions`）。

### 不变

- 「类型筛选」`<Select>`、搜索框保留，在下钻层内对当前剧的成片生效（入口层用于过滤入口卡/类型）。
- `handleCompose` 合成轮询逻辑不动。

## 样式

- 各自 `.module.css` 新增入口卡相关类（如 `.entryGrid`、`.entryCard`、`.backBar`）。入口卡为可点击的卡片，hover 态、显示剧名与数量 Tag。
- 复用现有 `grid` / `card` / `section` 等类承载下钻层网格。

## 验收标准

- 原片库默认展示剧名入口卡（名称+数量），点击某剧进入只看该剧资产，「← 返回」回到入口列表。
- 数字人库行为与改动前完全一致。
- 成片库默认展示剧名入口卡，点击进入只看该剧成片，可返回。
- 两个组件顶部不再有「按剧名筛选」下拉。
- 搜索框 / 类型筛选在对应层级正常生效。
- `npm run build`（tsc + vite）通过，`npm run lint` 无新增错误。
