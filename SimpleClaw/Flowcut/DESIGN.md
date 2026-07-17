# Flowcut 业务层设计方案

> 基于 2026-05-13 设计讨论 + 同日骨架实现，持续更新。
> 参考文档：`精简版技术调研.md`，参考实现：`SimpleClaw/Mojing/`

---

## 一、核心架构决策

| 决策项 | 结论 | 备注 |
|--------|------|------|
| API 主入口 | Chat + REST 混合 | 对话主流程走 `POST /agent/chat`（SSE），素材 CRUD 等纯数据操作走独立 REST 接口 |
| Agent 类型 | 真 Agent（ReactLoop） | 用户消息经 LLM 理解意图，不做状态机硬编码 |
| SSE 流式输出 | 是 | 前端字符流式接收 Agent 回复，与 Mojing 一致 |
| 数据库 | MySQL（MVP 阶段） | 与 Mojing 共用同一套驱动（aiomysql），等 Phase 2 再评估 pgvector |
| 多租户 | 是，仿照 Mojing | 保留 `tenant_key` 隔离，便于后续多账号扩展 |
| 人工卡点实现 | 方案 A | 用户在聊天框发文字选择（"选脚本 1"），前端右侧面板点选自动填入输入框发出 |
| 视频合成进度 | 方案 B | 合成过程中 Agent 主动推送中间进度卡片（前端已有 `ProgressCard` 组件） |
| 手动替换素材 | REST 接口 | 不经过 Agent，前端直接调 `PATCH /materials/{id}` |
| Workspace 结构 | 完整保留（同 Mojing） | Agent.md / SOUL.md / TOOL.md / compliance.md 均保留，支持运营自定义人格 |
| 素材上传方式 | OSS 直传 | 前端拿预签名 URL 直传 OSS，完成后调 `/materials/{id}/process` 触发后端处理 |

---

## 二、文件结构

```
Flowcut/
  agent/
    main_agent.py          # MainAgent（照搬 Mojing 模式）
    first_token.py         # 快速首 token（MVP 可选）
    postprocess.py         # PostprocessHook
    cold_path.py           # ColdPathHook（结构化记忆）
  api/
    server.py              # FastAPI app，startup / shutdown
    container.py           # AppContainer + build_container()
    routes/
      chat.py              # POST /agent/chat（SSE 主入口）
      materials.py         # 素材 CRUD
      creatives.py         # 成片接口
      qianchuan.py         # OAuth 授权 + 数据回流
      health.py            # GET /health
  context/
    providers.py           # TaskContextProvider（当前任务状态注入 prompt）
  tools/
    decompose_video.py     # durable：爆款视频拆镜（Gemini）
    generate_scripts.py    # coupled：LLM 生成差异化脚本
    search_materials.py    # coupled：按脚本段搜素材库
    compose_video.py       # durable：FFmpeg 拼片 + 评估 Agent 循环
    check_task_status.py   # coupled：查后台任务进度
    publish_to_qianchuan.py # durable：上传成片 + 创建千川计划
  storage/
    database.py            # Database（MySQL，复用 Mojing 实现）
    session_repo.py        # SessionRepository
    material_repo.py       # MaterialRepository
    creative_repo.py       # CreativeRepository
    script_repo.py         # ScriptRepository
    task_repo.py           # RuntimeTaskRepository
    qianchuan_repo.py      # 账号 + token 存储
    session_store.py       # SessionStore
  runtime/
    streams.py             # FlowcutTaskStream 枚举（5 条流）
    executors.py           # 各 durable 工具对应的 executor
    worker.py              # TaskWorker 启动配置
  workspace/
    Agent.md               # Agent 身份与流程规则
    SOUL.md                # 性格与语气
    TOOL.md                # 工具调用指引
    compliance.md          # 合规约束
  config.py
  __init__.py
```

---

## 三、API 列表

### 对话主入口
```
POST /agent/chat              # SSE 流式，用户所有制作指令走这里
```

### 素材管理
```
GET    /materials/upload-token        # 返回 { material_id, presigned_url }，前端直传 OSS
POST   /materials/{id}/process        # 前端直传完成后调用，触发 MATERIAL_PROCESS 入队
GET    /materials                     # 列表（支持 category / status 过滤）
GET    /materials/{id}                # 单条详情 + status（前端轮询进度用）
PATCH  /materials/{id}                # 手动修改命名 / 类别 / 手动替换素材
DELETE /materials/{id}
```

直传完整流程：
1. 前端调 `GET /materials/upload-token`，后端预分配 `material_id`，生成含该 id 的 OSS presigned URL
2. 前端直传文件到 OSS（不经后端）
3. 前端调 `POST /materials/{material_id}/process`，后端将该 id 对应的素材入队 `MATERIAL_PROCESS`

### 成片管理
```
GET    /creatives             # 成片列表
GET    /creatives/{id}        # 成片详情
PATCH  /creatives/{id}/label  # 打标：NORMAL / HOT / DEAD
```

### 千川管理（独立管理页面）
```
GET    /qianchuan/accounts          # 已绑定账号列表 + token 状态
GET    /qianchuan/oauth/start       # 发起 OAuth，返回跳转 URL
GET    /qianchuan/oauth/callback    # 千川回调，写 token 入库
POST   /qianchuan/token/refresh     # 手动触发 refresh_token 续期
```

### 数据报表
```
GET    /reports/daily         # T+1 日报（消耗 / 展示 / 转化 / ROI）
```

### 基础
```
GET    /health
```

> **不暴露的接口**：`/scripts` — 脚本生命周期完全在 chat 流里，前端通过 SSE 消息展示，不需要独立 REST。

---

## 四、Agent 工具清单

| 工具 | 类型 | 触发时机 | 说明 |
|------|------|----------|------|
| `decompose_video` | durable | 用户上传爆款视频 | 调 Gemini 拆镜，结果异步写回 |
| `generate_scripts` | coupled | 拆镜完成后 | LLM 生成 3-5 条差异化脚本，直接返回给 Agent |
| `search_materials` | coupled | 脚本确认后 | 按每段画面描述 + 时长搜素材库，返回三档结果 |
| `compose_video` | durable | 素材匹配确认后 | FFmpeg 拼片 + 评估 Agent 循环（最多 3 次），进度推卡片 |
| `check_task_status` | coupled | Agent 轮询进度时 | 查 RuntimeTask 状态，用于 durable 任务完成通知 |
| `publish_to_qianchuan` | durable | 成片确认后 | 上传 OSS 成片到千川 + 创建全域推广计划 |

**coupled vs durable 说明**：
- coupled：工具跑完结果塞回 Agent，Agent 用结果做下一步决策（紧耦合反馈循环）
- durable：工具立刻返回"已提交"，实际任务进后台队列，Agent 不阻塞等待

---

## 五、后台任务流（TaskStream）

共 5 条独立 Stream，每条职责单一、Worker 互不干扰。

| Stream | 负责任务 | 触发时机 | 失败策略 |
|--------|---------|---------|---------|
| `MATERIAL_PROCESS` | ASR 转写 + 自动命名 + 缩略图 / 预览片段生成 | `POST /materials/{id}/process` | 写 FAILED 状态，前端轮询可见 |
| `SCENE_DECOMPOSE` | 爆款视频拆镜（Gemini 多模态） | Agent 调 `decompose_video` | 立刻反馈给用户（Agent 回复错误） |
| `VIDEO_COMPOSE` | FFmpeg 拼片 + 评估 Agent 循环 + 进度推送 | Agent 调 `compose_video` | 立刻反馈给用户（Agent 回复错误） |
| `QIANCHUAN_PUBLISH` | 素材上传千川 + 创建全域推广计划 | Agent 调 `publish_to_qianchuan` | 立刻反馈给用户（Agent 回复错误） |
| `QIANCHUAN_SYNC` | T+1 数据回流（日报写库） | 定时任务（croniter） | 失败入重试队列，连续 3 次告警，不打扰用户 |

> 拆分 `QIANCHUAN_PUBLISH` 和 `QIANCHUAN_SYNC` 的原因：两者错误处理策略不同——Agent 触发的上传失败需立刻通知用户，定时回流失败可静默重试，混在一个 stream 会让 Worker 的重试/上报逻辑复杂化。

---

## 六、Context Provider

`TaskContextProvider`：每轮对话将以下信息注入 prompt：
- 当前制作任务 ID 和步骤（第几步）
- 当前选中脚本内容（各段画面描述 + 台词）
- 素材匹配结果（已匹配 / 低匹配 / 缺失 三档）

---

## 七、待确认事项

| # | 议题 | 状态 |
|---|------|------|
| 1 | 素材上传方式 | 已确认：OSS 直传，`upload-token` + `/{id}/process` 两步 |
| 2 | TaskStream 划分 | 已确认：5 条（含拆分后的 QIANCHUAN_PUBLISH / QIANCHUAN_SYNC） |
| 3 | 千川管理页面位置 | 已确认：单独管理页面 |

---

## 八、实现状态（2026-05-13 骨架完成）

### 已完成

所有文件骨架已创建，import 链路全部可用，服务可启动。

| 层 | 实现状态 |
|----|---------|
| `config.py` + `workspace/*.md` | 完整实现（env var 读取、workspace 加载） |
| `storage/` DB Schema | 5 张 fc_* 表已在 `ensure_schema()` 中定义，启动时自动建表 |
| `storage/` Repos（material/creative/script/qianchuan） | 骨架：方法签名完整，方法体 `raise NotImplementedError` |
| `storage/` 共享 Repos（session/task/session_store） | 完整实现（从 Mojing 迁移，import 路径已修正） |
| `runtime/streams.py` | 完整实现（5 条 FlowcutTaskStream 枚举） |
| `runtime/executors.py` | 骨架：5 个 stub executor，体 `raise NotImplementedError` |
| `runtime/worker.py` | 完整实现（make_workers 工厂，executors={} 待填充） |
| `tools/` 6 个工具 | 骨架：class 字段 + 方法签名完整，`execute`/`prepare_task` 体 `raise NotImplementedError` |
| `context/providers.py` | 骨架：`TaskContextProvider.__init__` 正常初始化，`collect_dynamic_context` 返回 `[]`（空） |
| `agent/` | 完整实现（可实例化、可启动；工具注入路径完整） |
| `api/` server + container | 完整实现（startup/shutdown 生命周期、5 个 worker 启动） |
| `api/routes/chat.py` | 完整实现（SSE 流式，含错误处理） |
| `api/routes/` 其余 4 个 | 骨架：端点存在，体为 `raise HTTPException(501)` |

### 实现中发现的架构调整

1. **`TaskWorker` 提升至 simpleclaw 层**
   - 原位置：`Mojing/runtime/worker.py`
   - 新位置：`simpleclaw/runtime/worker.py`（通用逻辑，无业务依赖）
   - `Mojing/runtime/worker.py` 改为从 simpleclaw re-export，保持向后兼容
   - Flowcut 直接从 `simpleclaw.runtime.worker` 导入

2. **`FirstTokenAgent` 不依赖 Mojing 的 `LLMCacheRepository`**
   - 原设计依赖 `Mojing.storage.llm_cache_repo`，违反架构隔离
   - 修复：在 `Flowcut/agent/first_token.py` 内部定义 `_CacheRepo` Protocol（最小接口）
   - `cache_repo` 在 `build_container()` 中传 `None`，等 Flowcut 实现自己的缓存层后替换

3. **`MainAgent` 无 journey 阶段**
   - Flowcut 不需要 novice/explore/mature 三阶段
   - `_stable_cache` 只预热 `"default"` 一个 key
   - `make_context_builder()` / `make_tool_registry()` 的 `stage` 参数默认 `"default"`

4. **`TaskContextProvider` 当前返回空列表**
   - `collect_dynamic_context()` 返回 `[]`（不注入任何内容）
   - 等 `material_repo` / `script_repo` / `creative_repo` 实现后，填充实际任务状态

### 下一步开发顺序（建议）

1. **实现 `material_repo.py`** — CRUD + status 更新，是最多接口的依赖
2. **实现 `storage/` 其余 repo** — creative / script / qianchuan
3. **实现 `runtime/executors.py`** — 按 stream 逐个填充：先 `MATERIAL_PROCESS`（最独立），再 `SCENE_DECOMPOSE`
4. **实现工具的 `execute`/`prepare_task`** — 依赖 executor 就绪
5. **实现 `TaskContextProvider.collect_dynamic_context`** — 依赖 repo 就绪
6. **补全 REST 端点** — materials / creatives / qianchuan 的 501 占位
