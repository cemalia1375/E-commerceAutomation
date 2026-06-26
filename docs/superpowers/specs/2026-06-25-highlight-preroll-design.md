# 高光成片前贴叠加功能 设计

## 背景

跨集高光成片（`continuous_cross_episode`）目前的拼接链路是：剪辑（1 分钟跨集片段）→（可选）数字人。本功能新增「前贴」——一张带透明背景的全幅 PNG（角标、底部文字等视觉元素已经画在图里），可叠加显示在成片的某一段或全片，用于品牌标识/免责声明等场景。

前贴本身**不是**独立拼接的视频片段，而是叠加在现有画面上的图层。前贴**只叠加在剪辑段**，数字人段不叠加。

## 范围

仅覆盖当前唯一在用的高光成片线——跨集高光（`continuous_cross_episode`）。不涉及已非活跃的 `highlight_original`/`highlight_digital_human` 路径，也不涉及普通成片库（`CreativeVideoLibrary`）。

## 1. 数据模型

### 前贴素材

复用现有 `fc_highlight_asset` 表，新增 `asset_type = 'preroll'`（与现有 `episode_source`/`digital_human_connector` 并列）。不新建表。

字段沿用现表结构：
- `oss_key` / `oss_url` — 图片存储位置
- `name` — 前贴名称
- `duration` — 固定为 0（图片无时长概念）
- `drama_name` / `episode_no` / `connector_role` — 前贴场景下不使用，留空

### 成片关联

`fc_creative` 新增一个字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `preroll_asset_id` | int, nullable | 引用 `fc_highlight_asset.id`（`asset_type='preroll'`）；null = 不使用前贴 |

与现有 `connector_asset_id` 的语义一致：只是记录用户的选择，不会立即触发任何合成动作，真正的烧录发生在导出（`export-highlight`）时。

## 2. 后端 — 合成/导出管线

### 现状

`make_highlight_export_executor`（`Flowcut/runtime/executors.py`）在用户点击「导出」时：
1. 下载 clip（已合成的 1 分钟跨集片段）+ 数字人源文件
2. 分别用 `_ffmpeg_normalize_clip` 归一化编码
3. `_ffmpeg_concat` 拼接
4. 上传产出，返回下载链接

「纯片下载」（未选数字人）走 `download_creative` 路由，直接 302 跳转到原始文件的 presigned URL，不经过 ffmpeg。

### 改动

**归一化阶段插入 overlay 滤镜**：若 `preroll_asset_id` 不为空，在 clip 段的归一化步骤中追加 `ffmpeg overlay` 滤镜，将前贴 PNG 缩放铺满画面、持续整段时长。数字人段不做任何 overlay 处理。

executor 需处理两条分支：
- **仅前贴（无数字人）**：下载 clip → 对 clip 应用 overlay 归一化 → 直接上传产出（不 concat）
- **前贴 + 数字人**：下载 clip + 数字人 → 对 clip 应用 overlay 归一化、数字人正常归一化 → concat → 上传产出

**纯片下载快路径收紧**：`download_creative` 路由判断逻辑改为——只要 `preroll_asset_id` 不为空（即使未选数字人），也不能再走直接 302 快路径，必须走 `export-highlight` 异步任务产出带前贴的文件。未选前贴时维持现有快路径不变。

`export-highlight` 路由的前置校验（`connector_asset_id is None` 时报错）需要放宽：只要 `connector_asset_id` 或 `preroll_asset_id` 任一不为空，就允许走导出流程。

### 新增接口

`PATCH /creatives/{creative_id}/preroll`
```json
{ "preroll_asset_id": 123 }
```
持久化前贴选择，逻辑与现有 `PATCH /creatives/{id}/connector` 一致（仅落库，不触发合成）。

## 3. 前端 — 素材库前贴库 Tab

`HighlightAssetLibrary.tsx` 的 `Segmented` 切换器新增第三个选项「前贴库」（对应 `asset_type='preroll'`）。复用现有上传/列表/删除逻辑，差异点：
- 上传 `accept="image/*"`，不读取/展示 `duration`
- 不需要剧名/集数/数字人角色分组字段，前贴用简单平铺列表 + 名称搜索
- 卡片缩略图用 `<img>` 而非 `<video>`

`types/index.ts` 中 `HighlightAssetType` 扩展为 `'episode_source' | 'digital_human_connector' | 'preroll'`。

## 4. 前端 — 成品库选前贴 + 顺序预览叠加

### 选择器

`HighlightCreativeLibrary.tsx` 的 `renderCrossEpisodeCreative` 中，数字人 `Select` 旁新增前贴 `Select`：
- 选项：不使用前贴 / 前贴素材列表
- 变更后调用 `PATCH /creatives/{id}/preroll` 持久化，与现有 `handleSelectConnector` 的模式一致

### 顺序预览叠加效果

不在预览阶段调用 ffmpeg。在 `SequentialPreview` 组件内，用绝对定位的 `<img>` 叠在 `<video>` 上方模拟效果：只要选了前贴，仅在播放第 1 段（剪辑）时显示；播放第 2 段（数字人）时不显示。

真正烧录进文件由后端导出时的 ffmpeg 完成；预览只是前端视觉模拟，两者职责分离，互不依赖。

## 错误处理

- 上传前贴图片：非图片格式拒绝（复用现有 `Upload` 组件的 `accept` 约束 + 后端 MIME 校验）
- 导出时前贴素材已被删除（`preroll_asset_id` 指向不存在的记录）：executor 内校验，返回 `TaskExecutionResult.failed`，提示前贴素材不存在

## 测试

- 后端：`export-highlight` executor 的 overlay 滤镜参数构造单测，覆盖两条分支（仅前贴/前贴+数字人）；`download_creative` 快路径收紧后的分支测试（有/无前贴 × 有/无数字人四种组合）
- 前端：`SequentialPreview` 叠加显示逻辑的组件测试（选前贴时 clip 段显示 `<img>`，DH 段不显示；未选前贴时均不显示）
