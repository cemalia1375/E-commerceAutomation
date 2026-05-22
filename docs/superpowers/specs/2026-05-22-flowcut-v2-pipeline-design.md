# Flowcut v2 流程改造设计（脚本拆解 + 上传 + 召回 + 导出）

> **日期**：2026-05-22
> **范围**：本轮端到端打通「脚本拆解扩展 → 用户上传脚本 → 素材匹配接通 → 素材导出」四项产品需求。成片预览（时间轴拼接 UI）下轮专项，不在本范围。
> **架构方针**：遵循 `docs/flowcut/process-design.md` 附录 C 的"渐进式 Tool 化"决策——新增业务能力全部 Tool 化，REST 路由是薄壳子。

---

## 1. 背景

产品方提出生成流程的重新拆解：

- **脚本拆解**：视频画面、文案需要同步拆解；可下载/复制/修改；独立音频文件保存
- **素材匹配**：用户可上传脚本（文案 + 画面），允许编辑画面内容后确认 → 触发匹配
- **成片预览**：预览选择/拼接的画面（**下轮专项，不在本 spec**）
- **素材导出**：把素材集合到文件夹打包下载（含素材片段、音频、原爆款视频）

附录 C 已决定走"渐进式 Tool 化"路径，本 spec 是其首次落地。

## 2. 已锁定的需求决策

| 决策 | 选项 | 来源 |
|---|---|---|
| 文案字段来源 | ASR 按分段时间窗截取 | 澄清 1 |
| 音频粒度 | 一份整体音频（原视频抽音轨） | 澄清 2 |
| 用户上传脚本归属 | 独立存在，不绑定爆款 | 澄清 3 |
| 上传格式 | 前端表单逐段填写（不传文件） | 澄清 4 |
| 召回 query | visual 和 copy 各 embed 一次，分别查对应向量池 | 澄清 5 |
| 编辑触发 | 手动点「确认脚本」才重新召回 | 澄清 6 |
| 导出范围 | 召回结果默认全选，运营可勾去 | 澄清 7 |
| 旧数据 | 清库重跑，不做兼容 | 澄清 8 |
| 路由前缀 | 所有新路由 `/flowcut/...` | 用户补充 |
| 导出执行 | 异步（走 stream），前端轮询任务状态 | § 3 |

## 3. 数据模型

### 3.1 新增表：`fc_script`

```sql
CREATE TABLE fc_script (
  id                  BIGINT PRIMARY KEY AUTO_INCREMENT,
  tenant_key          VARCHAR(64) NOT NULL,
  source              ENUM('decomposed', 'uploaded') NOT NULL,
  reference_video_id  BIGINT NULL,
  segments_json       JSON NOT NULL,
  status              ENUM('DRAFT', 'CONFIRMED') NOT NULL DEFAULT 'DRAFT',
  created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_tenant_status (tenant_key, status),
  INDEX idx_ref_video (reference_video_id)
);
```

`segments_json` 元素结构：

```json
{
  "idx": 0,
  "start_time": 0.0,
  "end_time": 3.96,
  "visual": "女生在厨房拿起洗面奶",
  "copy": "每天洗脸都觉得脸特别紧绷"
}
```

字段约束：
- `start_time` / `end_time`：拆镜来源有真实值；用户上传可填或留空（默认 0.0）
- `visual` / `copy`：均为字符串，至少其中一个非空
- `idx`：稳定段序号，前端可拖拽改顺序后重新分配

### 3.2 `fc_reference_video` 新增字段

```sql
ALTER TABLE fc_reference_video ADD COLUMN audio_oss_key VARCHAR(512) NULL;
ALTER TABLE fc_reference_video ADD COLUMN script_id BIGINT NULL;
```

`scene_data_json` 内部结构演进：每段新增 `copy` 字段（ASR 截取的口播）。`content` 字段语义不变（Gemini 写的画面描述）。

### 3.3 状态机

```
fc_script.status:
  DRAFT ──confirm──> CONFIRMED ──reopen──> DRAFT
```

- `DRAFT`：可编辑 segments；不能召回
- `CONFIRMED`：锁定 segments；可调召回；可点"重新编辑"回到 DRAFT
- 再次召回必须重走 `CONFIRMED`（重新编辑会清空之前的召回结果缓存）

### 3.4 清库迁移

一次性手工脚本（**不放进 ensure_schema，避免每次重启清库**）：

新增 `SimpleClaw/Flowcut/scripts/reset_db.py`：

```python
# 伪代码示意
async def reset():
    await db.execute("DROP TABLE IF EXISTS fc_creative")
    await db.execute("DROP TABLE IF EXISTS fc_material_usage")
    await db.execute("DROP TABLE IF EXISTS fc_material")
    await db.execute("DROP TABLE IF EXISTS fc_reference_video")
    await db.execute("DROP TABLE IF EXISTS fc_script")
    await ensure_schema(db)  # 重新建表
```

调用方式：`uv run python -m Flowcut.scripts.reset_db`

`ensure_schema()` 仍保持"if not exists"语义，只在表缺失时建。新表 `fc_script` 和两个新列加进 `ensure_schema` 的 DDL 里。

> ⚠️ 仅限开发库。生产环境的迁移策略不在本 spec 范围。

## 4. 后端组件

### 4.1 改造：`SCENE_DECOMPOSE` executor

文件：`Flowcut/runtime/executors.py::make_scene_decompose_executor`

新增 3 个动作：

1. **抽音轨**：FFmpeg 从原视频抽 mp3 → 上传 OSS → `fc_reference_video.audio_oss_key` 写入
2. **ASR 按段截取**：保留分词时间戳，按 `scene_data` 的 `start_time/end_time` 截取每段 copy 文本 → 写入 `scene_data.copy`
3. **产出 `fc_script` 记录**：转换为 segments → 写 `fc_script`（source=`decomposed`，reference_video_id 绑定），并把 script_id 回填到 `fc_reference_video.script_id`

失败容错（与 § 7 错误处理一致）：
- ASR 失败 → `copy` 字段为空字符串；不阻塞流程
- 抽音轨失败 → `audio_oss_key` 为 NULL；不阻塞流程
- fc_script 写入失败 → 整体任务标记失败（因为这是核心产物之一）

### 4.2 改造：`material_matcher.match_segment`

文件：`Flowcut/services/material_matcher.py::match_segment`

签名变化：

```python
# 改造前
match_segment(seg, ...) → embed(seg.description) → vector_store.search(qv, qv, ...)

# 改造后
match_segment(seg, ...) →
    visual_vec = embed(seg.visual)
    copy_vec   = embed(seg.copy)
    vector_store.search(visual_vec, copy_vec, ...)  # 已支持 desc + transcript 两个 query
```

`vector_store.search` 底层逻辑不动（接口本来就接受两个 query vector）。

边界：
- `seg.visual` 为空时跳过该路（仅用 copy_vec）
- `seg.copy` 为空时跳过该路（仅用 visual_vec）
- 两者都为空 → 跳过该段，结果记 `error: "段缺 visual 和 copy"`

### 4.3 新增 Tool（4 个）

| Tool 类 | 文件 | 职责 |
|---|---|---|
| `UploadScriptTool` | `Flowcut/tools/upload_script.py` | 创建 fc_script（source=uploaded），segments 数组从 payload 来 |
| `UpdateScriptTool` | `Flowcut/tools/update_script.py` | 更新 fc_script.segments_json，要求 status=DRAFT |
| `MatchByScriptTool` | `Flowcut/tools/match_by_script.py` | 拿 script_id，读 segments，调 match_segments_parallel，返回召回结果 |
| `ExportPackageTool` | `Flowcut/tools/export_package.py` | 接收 script_id + material_ids → 入队 EXPORT_PACKAGE 异步任务 |

所有 Tool 在 `Flowcut/api/container.py` 的 `tool_factories` 注册（Agent 轨道自动激活）。

### 4.4 新增 stream：`EXPORT_PACKAGE`

定义：`Flowcut/runtime/streams.py::FlowcutTaskStream.EXPORT_PACKAGE = "flowcut:export_package"`

消费者：`Flowcut/runtime/executors.py::make_export_package_executor`

执行步骤：
1. 拉所有选中素材文件到本地临时目录（OSS presigned GET）
2. 拉 reference_video + audio（仅当 fc_script.source=decomposed 且对应字段非 NULL）
3. 写脚本 `script.json`（完整结构）和 `script.md`（人读版）
4. 打 zip
5. 上传 zip 到 OSS（`exports/{tenant}/{ts}_{script_id}.zip`）
6. 写入 `RuntimeTaskRepository`：result_url = presigned GET URL（24h 过期）

Zip 结构：

```
export_<timestamp>_<script_id>.zip
├── script.json                  完整 JSON（含 visual + copy + 时间轴）
├── script.md                    人读版
├── clips/
│   ├── seg00_<material_id>.mp4    前缀 segNN 是脚本段索引；同段多素材按 score 顺序加 _a/_b 后缀
│   ├── seg00_<material_id>_b.mp4
│   ├── seg01_<material_id>.mp4
│   └── ...
├── audio.mp3                    仅 source=decomposed 有
├── reference.mp4                仅 source=decomposed 有
└── missing_materials.txt        仅当有素材拉取失败时存在
```

### 4.5 新增 storage：`ScriptRepository`

文件：`Flowcut/storage/script_repo.py`

```python
class ScriptRepository:
    async def create(self, *, tenant_key: str, source: str,
                     segments: list[dict],
                     reference_video_id: int | None = None) -> dict
    async def get(self, script_id: int) -> dict | None
    async def list_by_tenant(self, tenant_key: str, *,
                             status: str | None = None,
                             source: str | None = None) -> list[dict]
    async def update_segments(self, script_id: int, segments: list[dict]) -> None  # 仅 DRAFT
    async def update_status(self, script_id: int, status: str) -> None
```

`update_segments` 在 status != DRAFT 时抛 `StatusConflictError`，路由层翻译成 409。

### 4.6 不动的部分

- `CLIP_CREATE` executor 不动（分类确认 → 切片入素材库 这条线保留，与 fc_script 产出并行）
- 现有 6 个 Tool 不动（按附录 C，不去校对，等编排层评估时再处理）
- 4 个 Tab 的主骨架不动

## 5. API 设计

所有**本轮新路由**前缀 `/flowcut`。

> ⚠️ 现有路由（`/sessions`、`/materials`、`/reference-videos`、`/creatives`、`/qianchuan`）保持裸前缀不动。本轮形成混合命名约定，是用户明确选择的（避免日后多 product 路由冲突）。未来如需统一，可在另一轮迁移老路由到 `/flowcut/` 下。

| 路由 | 方法 | Tool / 实现 | payload / 返回 |
|---|---|---|---|
| `/flowcut/scripts` | POST | UploadScriptTool | body: `{tenant_key, segments: [{visual, copy, start_time?, end_time?}]}` → `{ok, script_id}` |
| `/flowcut/scripts` | GET | 直接 repo | query: `?source=uploaded\|decomposed&reference_video_id=N` → `{ok, scripts: [...]}` |
| `/flowcut/scripts/{id}` | GET | 直接 repo | → `{id, source, segments, status, reference_video_id, ...}` |
| `/flowcut/scripts/{id}` | PATCH | UpdateScriptTool | body: `{segments: [...]}` → `{ok}`；409 若 status != DRAFT |
| `/flowcut/scripts/{id}/confirm` | POST | 直接 repo update_status | → `{ok, status: 'CONFIRMED'}` |
| `/flowcut/scripts/{id}/reopen` | POST | 直接 repo update_status | → `{ok, status: 'DRAFT'}` |
| `/flowcut/scripts/{id}/match` | POST | MatchByScriptTool | body: `{product?}` → `{ok, results: [{seg_idx, visual, copy, phase1, phase2}]}` |
| `/flowcut/scripts/{id}/export` | POST | ExportPackageTool | body: `{material_ids: [int]}` → `{ok, task_id}` |
| `/flowcut/reference-videos/{id}/script` | GET | 直接 repo | 拿 ref_video 关联的 fc_script |
| `/flowcut/tasks/{task_id}` | GET | 直接 RuntimeTaskRepository | 轮询任务状态（含导出任务），含 result_url。**本轮新建**（现有代码无此路由） |

召回结果 (`/match`) 返回格式：

```json
{
  "ok": true,
  "results": [
    {
      "seg_idx": 0,
      "visual": "...",
      "copy": "...",
      "phase1": [
        {
          "material_id": 1,
          "name": "...",
          "score": 0.87,
          "preview_url": "...",
          "duration": 3.5,
          "scene_role": "..."
        }
      ],
      "phase2": []
    }
  ]
}
```

前端拿到 results 后**默认所有 material 处于"已选中"状态**，运营勾去后调 `/export`。

## 6. 前端改造

仅动「生成」Tab 内部页面流转 + 新增 2 个独立页。4 Tab 主骨架不动。

### 6.1 路由变化

```
/ (生成 Tab)
  └─ 入口选择 (NEW: 加 "直接编写脚本" 入口)
       ├─ 入口 A: 上传爆款视频 (现有)
       │    → 拆镜进度 (现有)
       │    → 分类确认 (现有，加 "查看脚本" 跳转)
       │    → /scripts/:id 脚本编辑页 (NEW)
       │
       └─ 入口 B: 直接编写脚本 (NEW)
            → /scripts/:id 脚本编辑页 (NEW)，空表单状态

  └─ /scripts/:id  脚本编辑页 (NEW)
       → 「确认脚本并匹配」按钮 → /scripts/:id/preview

  └─ /scripts/:id/preview  素材预览页 (NEW)
       → 「导出素材包」按钮 → 异步任务 → 弹窗下载链接
```

### 6.2 新建组件

| 组件 | 路径 | 职责 |
|---|---|---|
| `ScriptEditor` | `src/components/generate/ScriptEditor.tsx` | 脚本表单编辑（动态行 + textarea + 拖拽排序） |
| `MaterialPreview` | `src/components/generate/MaterialPreview.tsx` | 每段一个 card，展示 phase1/phase2 素材网格，复选框默认勾选 |
| `ExportButton` | `src/components/generate/ExportButton.tsx` | 触发导出 + 轮询任务 + 进度展示 + 下载链接弹窗 |

新增 Zustand store：`src/stores/scriptStore.ts` — 当前脚本、segments、召回结果、勾选状态。

### 6.3 关键交互

| 场景 | 行为 |
|---|---|
| 编辑了 visual/copy 未保存就离开页 | `beforeunload` 拦截 + Ant Modal 提示 |
| 在素材预览页想改脚本 | 不允许直接编辑；要点"重新编辑"回到 ScriptEditor（status: CONFIRMED → DRAFT） |
| 导出进行中关闭页 | 本轮简化：alert "导出中请勿关闭"；任务列表 UI 留给下轮 |
| 导出失败 | 弹错误提示 + "重试"按钮 |
| 导出超时（轮询 5 分钟未完成） | 提示"耗时较久，稍后再来"；任务在后台继续 |

### 6.4 不动的部分

- 素材 Tab / 成片 Tab / 看板 Tab — 不动
- 现有分类确认页 — 不动，仅在底部多一个跳转按钮
- 现有 Material 上传 / 搜索 — 不动

## 7. 错误处理

| 场景 | 处理 |
|---|---|
| 拆镜执行中 ASR 失败 | `scene_data` 仍产出，`copy` 字段为空；fc_script 仍创建，运营手动补 |
| 抽音轨 FFmpeg 失败 | `audio_oss_key` 为 NULL；不阻塞；导出 zip 不含 `audio.mp3` |
| 上传脚本字段校验失败 | 422，明确指出哪一段缺什么 |
| 召回 embedding 服务宕机 | 返回 `{phase1: [], phase2: [], error: "..."}`；前端展示"召回失败 + 重试" |
| 导出时部分素材 OSS 拉取失败 | 跳过失败素材，zip 内加 `missing_materials.txt`；整体仍成功 |
| 导出 zip > 500 MB | OSS 分片上传；前端进度条只看任务状态 |
| 编辑 CONFIRMED 脚本 | PATCH 返回 409；前端提示"请先点重新编辑" |

## 8. 测试策略

### 8.1 单元测试（pytest）

| 模块 | 测试文件 |
|---|---|
| `ScriptRepository` | `tests/test_script_repo.py` |
| `UploadScriptTool` | `tests/test_upload_script_tool.py` |
| `UpdateScriptTool` | `tests/test_update_script_tool.py` |
| `MatchByScriptTool` | `tests/test_match_by_script_tool.py` |
| `ExportPackageTool` | `tests/test_export_package_tool.py` |
| `match_segments_parallel`（改造后） | 扩展 `tests/test_material_matcher.py` |

### 8.2 集成测试

| 场景 | 测试文件 |
|---|---|
| 拆镜端到端（含 fc_script + audio 产出） | 扩展 `tests/test_scene_decompose_e2e.py` |
| 导出端到端（mock OSS） | 新增 `tests/test_export_package_e2e.py` |

### 8.3 本轮不测

- 前端组件测试（暂无基础设施）
- 大文件压力测试
- ASR 词级时间戳真实可用性 — 通过 § 9.1 的 spike 覆盖

## 9. 已知风险

### 9.1 ASR 词级时间戳可行性

现有 ASR 服务（字节跳动 BigModel WebSocket）的返回结构未确认是否包含词级时间戳。本 spec 假定可以拿到，但**实施前必须先做技术验证**：

- 调一次现有 ASR，dump 返回结构
- 确认存在 `word_offset` / `word_duration` 或等价字段
- Fallback 方案：
  - (a) Gemini 拆镜时同时输出该段口播文本（增加 Gemini 单次调用 token，但精度高）
  - (b) 整段 ASR + 按 scene_data 时间比例近似分配（精度差，按字符比例近似）

如果验证失败，**默认走 fallback (a)**，因为 Gemini 已经在拆镜了，加一个字段比另起 ASR 处理便宜。

**验证结论（2026-05-22）：** 词级时间戳可用。
**依据：** 实跑探针 `Flowcut/scripts/spike_asr_response.py` 对样例视频 15s 音频做 ASR，baseline config（无需任何额外 flag）即返回 `result.utterances[].words[]`，每个 word 含 `start_time` / `end_time`（毫秒）与 `text`，例如 `{"end_time": 120, "start_time": 40, "text": "如果"}`。`utterances` 本身也带 `start_time` / `end_time` / `definite`。
**Task 4.2 走主路径**（直接按 scene_data 时间窗截取 ASR words 拼成 `copy` 字段），不需要 Gemini fallback。

### 9.2 导出 zip 大小不可控

如果运营选了几十段、每段几个素材，加上原视频，zip 可能上 GB。

- 现状：依赖 OSS 分片上传，前端进度条不显示字节数
- 未来：可加导出前的容量预估 + 提示

### 9.3 fc_script 和 scene_data 数据冗余

拆镜产物在两处冗余了一份（fc_reference_video.scene_data_json 的 copy 字段 + fc_script.segments_json）。

- 现状：两者各自演进路径不同（scene_data 给切片用、script 给运营编辑用），冗余可接受
- 未来：如果切片流程也想用编辑后的脚本，可考虑去冗余，但本轮不做

## 10. 实施步骤（粗）

详细 plan 由 writing-plans skill 产出。本节仅给出粗略阶段：

1. **Spike**：技术验证 ASR 词级时间戳，确定 § 9.1 走主路径还是 fallback
2. **数据层**：ScriptRepository + ensure_schema 改造 + 清库
3. **后端能力层**：matcher 改造 + 4 个新 Tool + 1 个新 stream
4. **REST 路由**：8 个新路由（薄壳子）
5. **executor 改造**：SCENE_DECOMPOSE 多产出 + EXPORT_PACKAGE executor
6. **测试**：单测 + 集成测试
7. **前端**：路由 + 3 个新组件 + 1 个新 store
8. **端到端联调**

## 11. 不在本范围

明确不做：

- 成片预览（时间轴拼接 UI）— 下轮专项
- 千川投放 — 长期不在 Flowcut v2 范围
- 任务列表 UI — 本轮以 alert + 弹窗替代
- 老 6 个 Tool 与 REST 路由的等价性校对 — 等编排层评估时再做
- 旧拆镜数据迁移 — 清库重跑
- 大文件容量预估、token 成本预估
