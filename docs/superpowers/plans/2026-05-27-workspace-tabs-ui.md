# Flowcut 工作台 4-Tab 改造实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把"生成"流程从当前的双轨（GenerateTab mock 演示流 + 独立的 `/scripts/:id` 真后端页）合并到一个有状态的工作台：新增 `/workspace/:scriptId?tab=script|match|preview|export` 4-tab 容器，覆盖脚本编辑、素材匹配、顺序连放预览、打包导出全流程；GenerateTab 退化为入口页（含真接 chat agent 的 ChatPanel）。

**Architecture:** 改动跨前后端两侧。后端：`POST /reference-videos` 预建 `fc_script(status=PROCESSING)` 让 script_id 从 t=0 存在；`fc_script.status` 枚举扩展 `PROCESSING/FAILED`；`POST /scripts/{id}/export` payload 由扁平 `material_ids[]` 升级为 `selections: Record<seg_idx, material_ids[]>`，包结构改成 `materials/{mid}.mp4` 去重 + `manifest.json` 顺序清单。前端：新增 `/workspace/:scriptId` 路由，重构 `scriptStore.selectedMaterials` 为按段记录的有序结构，清理 mock UI（保留 ChatPanel），ChatPanel 接入 `POST /agent/chat` SSE + 任务完成后自动 navigate。

**Tech Stack:** Python 3 + FastAPI + aiomysql + React 19 + TypeScript + Vite + Zustand + Ant Design 6 + react-router-dom。

**Spec:** 本计划直接落地，无单独 spec 文档。

**实现顺序原则：** 从最独立、最低风险的后端 schema/契约改动开始（status 扩展、export payload 升级），再做后端预建逻辑（POST /reference-videos 同步建 fc_script），然后前端从状态层（scriptStore）往上一层一层重构（路由→工作台壳→4 个 tab→ChatPanel chat 接入）。最后清理 mock 死代码。每个 Task 一次提交。

**分支策略：** 当前分支 `feature/flowcut-v2-pipeline` 即为本计划工作分支，直接提交。如出现冲突或要回滚单个 Task，使用 `git revert` 而非 reset。

**前置约定（所有 subagent 必读）：**
- 后端 TDD：先写测试再实现，pytest 真接 MySQL/OSS（按本项目惯例不 mock 真集成点），使用 `uv run pytest -m unit` / `-m integration` 区分。
- 前端：`npm run lint && npm run build` 必须通过；新增组件保持 200-400 行；CSS 走 CSS Modules。
- 中文：subagent 面向用户的输出一律中文。
- 必读：`~/.claude/CLAUDE.md` 与 `~/.claude/rules/common/*.md`（subagent 启动时先读）。

---

## File Structure

**后端修改：**
- `Flowcut/storage/database.py` — `ensure_schema()` 不需要改（status 是 VARCHAR(16)，枚举在应用层）
- `Flowcut/storage/script_repo.py` — `create()` 支持 status 参数（默认 DRAFT）；新增 `update_status_to_processing_failed` 之类的便捷方法（如有需要）
- `Flowcut/storage/reference_video_repo.py` — 配合预建 script_id 的回填顺序调整
- `Flowcut/api/routes/reference_videos.py` — `POST /reference-videos` 同步预建 fc_script(status=PROCESSING)，立即回填 ref_video.script_id
- `Flowcut/runtime/executors.py` — `make_scene_decompose_executor` 从 INSERT fc_script 改为 UPDATE 既有行；失败时 update status 为 FAILED
- `Flowcut/api/routes/scripts.py` — `POST /{script_id}/export` payload 改成 `selections: Record<seg_idx, number[]>`；保留旧扁平字段做向后兼容（一个 release 后删除）
- `Flowcut/tools/export_package.py` — `prepare_task()` 透传 selections 结构
- `Flowcut/runtime/executors.py::make_export_package_executor` — 包结构改：`materials/{mid}.mp4` 去重 + `manifest.json`（顺序）+ `script.md` 多素材标注

**前端修改：**
- `flowcut_frontend/src/router.tsx` — 新增 `/workspace/:scriptId` 路由，移除 `/scripts/:scriptId` 和 `/scripts/:scriptId/preview`
- `flowcut_frontend/src/stores/scriptStore.ts` — `selectedMaterials` 由 `Set<number>` 重构为 `Record<seg_idx, number[]>`；默认选中逻辑改为"每段 phase1 第 1 名（无则 phase2 第 1 名）"
- `flowcut_frontend/src/api/script.ts` — `match` / `export` 调整签名以适配新 selections 结构
- `flowcut_frontend/src/components/generate/GenerateTab.tsx` — 重写为入口页（左 ChatPanel + 右"上传爆款 / 上传脚本 / 打开已有脚本"卡片）
- `flowcut_frontend/src/components/generate/ChatPanel.tsx` — 接入 `POST /agent/chat` SSE，渲染 token 流和 tool 调用卡片；监听拆镜任务完成自动 navigate `/workspace/:scriptId`
- `flowcut_frontend/src/components/generate/UploadEntry.tsx`（新建）— 入口页右侧的上传/打开卡片
- `flowcut_frontend/src/components/generate/ExistingScriptsModal.tsx`（新建）— "打开已有脚本"列表 Modal

**前端新建（工作台）：**
- `flowcut_frontend/src/components/workspace/WorkspaceLayout.tsx` — `/workspace/:scriptId` 顶层壳，含 tab bar + URL 同步 + 轮询 status
- `flowcut_frontend/src/components/workspace/WorkspaceTabBar.tsx` — 4 tab 切换条 + 软 gate 提示
- `flowcut_frontend/src/components/workspace/ScriptTab.tsx` — 接管原 ScriptEditor 职责 + 顶部"复制为 Markdown"按钮 + status badge + PROCESSING/FAILED 状态展示
- `flowcut_frontend/src/components/workspace/MatchTab.tsx` — 接管原 MaterialPreview 的"召回卡片 + 勾选"部分，按段维护选择
- `flowcut_frontend/src/components/workspace/PreviewTab.tsx` — HTML5 video 顺序连放器（一期无音轨、无时长对齐）
- `flowcut_frontend/src/components/workspace/ExportTab.tsx` — 触发 export + 轮询 + 下载链接

**前端删除：**
- `flowcut_frontend/src/components/generate/StepBar.tsx` + `.module.css`
- `flowcut_frontend/src/components/generate/steps/ScriptStep.tsx`
- `flowcut_frontend/src/components/generate/steps/MatchingStep.tsx`
- `flowcut_frontend/src/components/generate/steps/ConfirmStep.tsx`
- `flowcut_frontend/src/components/generate/steps/PlaceholderStep.tsx`
- `flowcut_frontend/src/components/generate/steps/Step.module.css`（若不再被 UploadStep 引用则删）
- `flowcut_frontend/src/components/generate/ScriptEditor.tsx`
- `flowcut_frontend/src/components/generate/MaterialPreview.tsx`
- `flowcut_frontend/src/components/generate/ContentPanel.tsx` + `.module.css`
- `flowcut_frontend/src/stores/generateStore.ts`

**保留不动：**
- `flowcut_frontend/src/components/generate/ChatPanel.tsx`（结构保留，内部改成真接 chat agent）
- `flowcut_frontend/src/components/generate/ExportButton.tsx`（可以并入 ExportTab，或保留为可复用组件）
- `flowcut_frontend/src/components/generate/steps/UploadStep.tsx`（改造成入口页的真上传组件，迁移到 `components/generate/UploadEntry.tsx`）

**测试新建（后端）：**
- `SimpleClaw/tests/Flowcut/test_reference_videos_prebuild_script.py` — 上传爆款后立即有 fc_script 行
- `SimpleClaw/tests/Flowcut/test_scene_decompose_update_script.py` — executor 走 UPDATE 而非 INSERT，失败时 status=FAILED
- `SimpleClaw/tests/Flowcut/test_export_selections_payload.py` — selections 结构端到端打包
- `SimpleClaw/tests/Flowcut/test_export_package_dedupe.py` — 同一 material 跨段只打一份 + manifest 顺序正确

---

## Tasks

### Phase 1：后端契约升级（schema 不动，应用层加状态）

- [ ] **Task 1：扩展 `fc_script.status` 应用层枚举**
  - 在 `Flowcut/storage/script_repo.py` 中允许 `PROCESSING` / `FAILED` 两个新状态值，无需 DDL 改动（VARCHAR(16) 已能存）。
  - `create()` 接受 `status: str = "DRAFT"`，让上游决定初始状态。
  - 写单测：repo.create(status="PROCESSING") + repo.update_status(id, "FAILED") + repo.get(id) 状态正确。
  - 验证：`uv run pytest tests/Flowcut/test_script_repo_status.py`

- [ ] **Task 2：`POST /reference-videos` 同步预建 fc_script(PROCESSING) + 回填 script_id**
  - 在 `Flowcut/api/routes/reference_videos.py` 的上传路由里：拿到 ref_video_id 后**同请求内顺序执行**：`script_repo.create(source="decomposed", reference_video_id=..., segments_json=[], status="PROCESSING", product=None)` → `ref_video_repo.set_script_id(ref_video_id, script_id)`。无需显式事务（两步失败概率极低；若 set_script_id 失败，孤儿 script 后续可通过 `reference_video_id IS NOT NULL` 反查清理，一期不实现清理）。
  - 响应中返回 `script_id` 给前端立即跳转。
  - 写集成测试：POST /reference-videos → 响应含 script_id → GET /scripts/{script_id} 返回 status=PROCESSING + segments=[]。
  - 验证：`uv run pytest tests/Flowcut/test_reference_videos_prebuild_script.py`

- [ ] **Task 3：`scene_decompose_executor` 从 INSERT 改 UPDATE**
  - `make_scene_decompose_executor` 当前调用 `script_repo.create(...)` 写新行；改为 `script_repo.update_segments_and_status(ref_video_id 关联的 script_id, segments_json=..., status="DRAFT")`。
  - 失败路径：捕获异常后 `script_repo.update_status(script_id, "FAILED")` 而不是只更新 ref_video.status。
  - 删除 `ref_video_repo.set_script_id` 在 executor 内的调用（已由 Task 2 提前完成）。
  - 写单测 + 集成：模拟拆镜成功/失败两条路径，验证 status 流转正确。
  - 验证：`uv run pytest tests/Flowcut/test_scene_decompose_update_script.py`

- [ ] **Task 4：`POST /scripts/{id}/export` payload 升级为 `selections`**
  - `Flowcut/api/routes/scripts.py::export_script` 接受新字段 `selections: dict[str, list[int]]`（key 是 seg_idx 字符串），同时保留旧的 `material_ids: list[int]` 字段做一个版本的向后兼容（内部 normalize 到 selections）。
  - `Flowcut/tools/export_package.py::prepare_task` 接受并透传 `selections`。
  - TaskEnvelope.payload 用 selections 取代 material_ids。
  - 验证：`uv run pytest tests/Flowcut/test_export_selections_payload.py`

- [ ] **Task 5：`export_package` 执行器改包结构**
  - `make_export_package_executor`：遍历 selections 收集所有用到的 material_ids 集合（去重），下载到 `materials/{mid}.mp4`。
  - 写 `manifest.json`：`[{seg_idx: 0, material_ids: [12, 7]}, {seg_idx: 1, material_ids: [12]}, ...]`，按 seg_idx 升序。
  - `script.md` 每段末尾追加一行"使用素材：mid1, mid2"。
  - 保留 `audio.mp3` / `reference.mp4` / `script.json` 原逻辑（仅 source=decomposed 且对应文件存在时打入）。
  - 验证：`uv run pytest tests/Flowcut/test_export_package_dedupe.py`

### Phase 2：前端基础重构

- [ ] **Task 6：重构 `scriptStore.selectedMaterials` 为按段有序结构**
  - 类型从 `Set<number>` 改成 `Record<number, number[]>`（key=seg_idx）。
  - `toggleMaterial(segIdx, materialId)`：在该段数组中 toggle，保持勾选顺序。
  - `setMatchResults(results)`：默认每段勾 phase1[0]，若无 phase1 则 phase2[0]，若都无则空数组。
  - 重置 / 清空 / 序列化方法对应调整。
  - 写单测覆盖 toggle 顺序、默认勾选、重复段去重不去全局。
  - 验证：`npm run lint && npm run build`（store 单元测试若有则跑）

- [ ] **Task 7：新增 `/workspace/:scriptId` 路由 + 工作台壳 + URL 同步**
  - `router.tsx` 加 `<Route path="/workspace/:scriptId" element={<WorkspaceLayout />} />`，删 `/scripts/:scriptId` 和 `/scripts/:scriptId/preview`。
  - `WorkspaceLayout`：useParams 拿 scriptId，启动 `GET /scripts/{id}` 3s 轮询直到非 PROCESSING；用 `useSearchParams` 同步 `?tab=...`，默认 `script`。
  - `WorkspaceTabBar`：4 个 tab，按 script.status 软 gate（PROCESSING/FAILED 时其他 tab disabled+提示，DRAFT 时其他 tab 可点但显示"建议先确认脚本"提示）。
  - 渲染 `<Outlet />` 或条件渲染 4 个 TabComponent。
  - 验证：手动 `/workspace/123?tab=match` 能直接进到匹配 tab，刷新保留位置。

- [ ] **Task 8：实现 4 个 Tab 组件**
  - `ScriptTab`：从原 `ScriptEditor` 迁移编辑逻辑；顶部加"复制为 Markdown"按钮（`navigator.clipboard.writeText(buildMarkdown(script))`）+ status badge；PROCESSING 显示"拆镜中… 已耗 Xs"+ spinner；FAILED 显示错误原因（status 列没有 error 字段则显示通用提示，提示回入口页重传）。
  - `MatchTab`：从原 `MaterialPreview` 迁移召回 + 勾选逻辑；按段渲染卡片墙；勾选写入新的 scriptStore；段内显示有序徽章条 `已选 [12 → 7]`。
  - `PreviewTab`：HTML5 `<video>` ref + 按 seg_idx 升序遍历 selections，每段内按 material_ids 数组顺序播放（即勾选顺序），形成扁平播放队列 `[{seg:0, mid:12}, {seg:0, mid:7}, {seg:1, mid:12}, ...]`；监听 `onEnded` 切下一个；未选段（`selections[seg_idx]` 为空数组）插入黑屏 1s 占位 + 段标签。**不挂音轨，不强制对齐**。
  - `ExportTab`：触发 `POST /scripts/{id}/export` 用新 selections payload；轮询 `/tasks/{task_id}`；完成显示下载链接（`window.open(result_url)`）+ missing_materials 列表。
  - 验证：每个 tab 独立可用，切换不丢状态。

### Phase 3：入口页改造与 chat 接入

- [ ] **Task 9：`GenerateTab` 改造成入口页**
  - 保留左侧 ChatPanel 容器宽度不变。
  - 右侧新建 `UploadEntry` 组件：3 个卡片——"上传爆款视频"（真接 `POST /reference-videos`，拿到 script_id 后 `navigate('/workspace/'+id)`）、"上传脚本"（表单输入 segments JSON 或粘贴文本，POST /scripts，navigate）、"打开已有脚本"（点开 `ExistingScriptsModal` 列表）。
  - `ExistingScriptsModal`：调 `GET /scripts?tenant_key=default` 列出所有 script，点选 navigate。
  - 验证：3 条入口都能跳到对应 `/workspace/:id`。

- [ ] **Task 10：ChatPanel 真接 `POST /agent/chat` SSE**
  - 新建 `api/chat.ts` 用 fetch + ReadableStream 解析 SSE。
  - ChatPanel：维护 session_key（localStorage 持久化）+ 消息历史 + 输入框；提交后 stream 渲染 token 流。
  - tool 调用结果以特殊卡片显示（如"已提交拆镜任务 #task_abc"）。
  - 监听 SSE 的 `ToolResultEvent`（参考 `simpleclaw/core/events.py`）：当 tool_name=`decompose_video` 且返回包含 `task_id` 时，启动后台轮询 `GET /tasks/{task_id}`；任务完成后从 `details.script_id` 取值，调用 `navigate('/workspace/'+id)`。同样兼容 `upload_script` tool（直接返回 script_id）。
  - 验证：手动在 ChatPanel 输入"帮我拆这个视频 https://..."，agent 调用 decompose_video 后自动跳工作台。

### Phase 4：清理 mock 死代码

- [ ] **Task 11：删除 mock UI + store**
  - 删除 `StepBar`、`steps/ScriptStep`、`steps/MatchingStep`、`steps/ConfirmStep`、`steps/PlaceholderStep`、`steps/Step.module.css`（如无引用）。
  - 删除 `ScriptEditor.tsx`、`MaterialPreview.tsx`、`ContentPanel.tsx` + `.module.css`。
  - 删除 `stores/generateStore.ts` + 其在 `types/index.ts` 中的相关类型（GenerateStep 等）。
  - `steps/UploadStep.tsx` 内容迁移到新的 `UploadEntry.tsx` 后删除原文件 / 或重命名移动。
  - 全文搜索确保无残留 import。
  - 验证：`npm run build` 通过，无 dead import。

- [ ] **Task 12：端到端冒烟**
  - 启动后端 `uv run python -m uvicorn Flowcut.api.server:app --reload --port 8001`。
  - 启动前端 `npm run dev`。
  - 完整走一次：入口页上传爆款 → 立刻跳工作台看 PROCESSING → 拆镜完成自动进 DRAFT → 脚本 tab 编辑 + 确认 → 匹配 tab 勾选（含同段多选、跨段同 material 选）→ 预览 tab 顺序连放 → 导出 tab 下载 zip → 解压验证 manifest.json + materials 去重 + script.md 多素材标注。
  - 验证：上述 7 步全部通过。

---

## 已知 follow-up（非阻塞，二期处理）

- **/upload 中间步骤失败留孤儿**（Task 2 code review）：`script_repo.create / set_script_id / submit_task` 任一步失败会留下 PROCESSING 状态的孤儿 script，前端轮询无限卡死。补偿方案待定（外层 try/except 标 FAILED，或定期清扫 task）。

## 显式不做（一期范围外）

- FFmpeg 真合成成片 + 合成产物预览（compose_video 保持 stub）
- 同 copy 跨 `fc_creative` 反查并打入 zip（fc_creative 暂无 copy 字段、暂无真实数据）
- 拆镜失败重试入口（FAILED 后用户回入口页重传）
- 段内拖拽 reorder（勾选顺序＝播放顺序，想换序就取消重勾）
- 导出历史记录表 / 重新下载入口（每次导出生成新 zip）
- ChatPanel 跟随到工作台（一期只在入口页，工作台靠 UI 操作）
- 预览 tab 音轨同步播放（HTML5 audio + video 同步精度不足，等 compose 真做完再处理）
- 预览 tab 素材时长与脚本段时长强制对齐（提示用户但不裁剪/变速）
- 默认选中的 score 阈值过滤（无阈值，phase1[0] 一律勾）
