# FlowCut 项目交接文档

更新时间：2026-07-18 
交接范围：`flowcut_frontend/`、`SimpleClaw/Flowcut/`、部署文件、当前数据库表结构和已知问题。


## 1. 项目定位

FlowCut 是面向抖音/千川投放的短视频生产工具，当前代码里同时存在两条业务主线：

- 素材脚本自动化：爆款视频拆镜、脚本生成、素材上传、素材匹配、素材包导出。
- 漫剧跨集高光：上传剧集素材，自动找高光起点，向后拼接约 1 分钟候选视频，支持数字人/前贴/导出。

目前从部署文档看，线上/测试部署重点是“跨集高光功能”；素材脚本自动化代码已大量实现，但部署范围和稳定性需要再确认。

## 2. 仓库结构

```text
E-commerceAutomation/
  flowcut_frontend/                 # React + Vite + Electron 前端
  SimpleClaw/
    simpleclaw/                     # 通用 Agent/工具/任务队列框架
    Flowcut/                        # FlowCut 业务后端
    Mojing/                         # 魔镜业务参考实现，FlowCut 不应直接写业务依赖
    tests/                          # 单测/集成测试
  docs/                             # 部署、压测、链路日志方案等文档
  Dockerfile.flowcut                # FlowCut 前后端单镜像
  Dockerfile.mj                     # 漫剧部署镜像，当前部署文档使用它
  docker-compose.flowcut*.yml       # 本地/服务器 compose
```

关键约束：`simpleclaw/` 是共享编排层，FlowCut 业务逻辑应该留在 `SimpleClaw/Flowcut/`。

## 3. 技术栈与启动

前端：

- React 19、TypeScript、Vite、Ant Design 6、Zustand、Electron。
- 本地开发：`cd flowcut_frontend && npm run dev`。
- 默认 API 地址来自 `VITE_API_BASE_URL`，未配置时是 `http://localhost:8001`。

后端：

- FastAPI + uvicorn，Python 3.11。
- MySQL、Redis/InMemoryTaskQueue、Qdrant、OSS、Gemini/Volcengine、FFmpeg。
- 本地后端：`cd SimpleClaw && uv run python -m uvicorn Flowcut.api.server:app --reload --port 8001`。
- 健康检查：`GET /health`，容器内经 nginx 访问为 `/api/health`。

容器：

- nginx 监听 80，`/api/*` 反代到 `127.0.0.1:8001/*`，SSE 关闭缓冲。
- supervisord 同时管理 nginx 和 uvicorn。
- `docker-compose.flowcut.yml` 额外启动 qdrant、redis，并把 `QDRANT_URL`/`REDIS_URL` 指向容器服务。

## 4. 后端入口和主要模块

后端入口：

- `SimpleClaw/Flowcut/api/server.py`：注册所有路由，startup 时初始化 OSS CORS、MySQL schema、Repository、LLM、Qdrant、Worker。
- `SimpleClaw/Flowcut/api/container.py`：依赖组装中心。
- `SimpleClaw/Flowcut/runtime/worker.py`：根据 stream 创建 TaskWorker。
- `SimpleClaw/Flowcut/runtime/executors.py`：多数后台任务逻辑仍集中在一个大文件。

主要 API：

- `/auth/*`：登录、登出、当前用户。
- `/agent/chat`：Agent SSE 对话。
- `/sessions/*`：会话管理。
- `/materials/*`：素材上传、zip 解析、列表、树、匹配、删除。
- `/reference-videos/*`：爆款参考视频上传、拆镜。
- `/flowcut/scripts/*`：脚本上传、编辑、确认、预览、匹配、导出。
- `/creatives/*`：成片上传、列表、标签、下载、跨集高光 compose/export。
- `/highlight-assets/*`：漫剧剧集、数字人、前贴素材管理。
- `/highlight-batches/*`：新跨集高光 batch 管道创建、查询、取消、重试。
- `/flowcut/tasks/{task_id}`：长任务轮询；支持普通 task_id 和 `batch:{batch_id}`。
- `/qianchuan/*`：账户汇总和手动同步可用，OAuth/账号管理仍是 501。

## 5. 当前数据库表结构

建表集中在 `SimpleClaw/Flowcut/storage/database.py::ensure_schema()`，启动时 `CREATE TABLE IF NOT EXISTS` 加一批手写迁移。

共享/历史 `nb_*` 表仍会创建，包括：

- `nb_tenants`
- `nb_tenant_state`
- `nb_cron_jobs`
- `nb_sessions`
- `nb_session_messages`
- `nb_tenant_documents`
- `nb_tenant_document_versions`
- `nb_image_analysis_jobs`
- `nb_skin_diary_results`
- `nb_skin_diary_sessions`
- `nb_slow_model_reports`
- `nb_deep_analysis_reports`
- `nb_agent_field_reports`
- `nb_topic_tracking`
- `nb_memory_entries`
- `nb_runtime_tasks`
- `nb_agent_tool_invocations`
- `nb_tenant_skin_profiles`
- `nb_tenant_profile_block_meta`
- `nb_tenant_memory_events`
- `nb_llm_prefix_caches`
- `nb_llm_session_caches`

FlowCut 专属 `fc_*` 表：

- `fc_reference_video`：爆款/参考视频，含 `oss_key`、`thumbnail_url`、`product`、`scene_data_json`、`audio_oss_key`、`script_id`、`status`。
- `fc_material`：普通素材库，含 `transcript`、`description`、`category`、`product`、`scene_role`、`parent_material_id`、`source_video_id`、`vector_indexed`。
- `fc_highlight_asset`：漫剧高光素材库，含 `asset_type`、`drama_name`、`episode_no`、`connector_role`、`metadata_json`。
- `fc_script`：脚本，含 `source`、`reference_video_id`、`product`、`segments_json`、`status`。
- `fc_creative`：成片，含普通成片和高光成片字段；基础表里有 `creative_type`、`batch_id`、`source_asset_id`、`connector_asset_id`、`highlight_start/end`、`compose_plan_json`、`clip_plan_json`、千川 id。
- `fc_material_usage`：素材与成片多对多关系。
- `fc_qianchuan_account`：千川账号 token。
- `fc_user`、`fc_login_session`：FlowCut 登录用户和 cookie 会话。
- `fc_client_event_log`：客户端行为日志。
- `fc_highlight_event_log`：跨集高光链路事件日志。
- `fc_highlight_batch`：新高光批量管道主表。
- `fc_highlight_stage`：新高光批量管道阶段表。
- `fc_qianchuan_orphan`：千川回流无法匹配本地成片的物料记录。

注意：`fc_creative` 当前通过迁移又补了 `qc_*`、`preroll_asset_id` 等列，但基础建表 SQL 里没有全部展开。真实线上表结构要以已跑过的迁移结果为准。

## 6. 后台任务流

- `flowcut:material_process`：素材处理，视频走 Gemini 描述/转写、封面、向量索引；图片/音频直接 READY。
- `flowcut:scene_decompose`：参考视频拆镜，生成/更新 `fc_script`。
- `flowcut:video_compose`：当前挂的是高光 compose/export executor，不是完整普通素材拼片链路。
- `flowcut:qianchuan_publish`：浏览器自动化发布千川，依赖 CDP。
- `flowcut:qianchuan_sync`：千川数据回流。
- `flowcut:vector_repair`：定时修复未索引素材。
- `flowcut:export_package`：脚本+素材导出 zip。
- `flowcut:highlight_plan`：旧单体跨集高光管道，保留兼容。【由于甲方有生成效率要求，这部分可以在新的并行链路完成后清理旧链路】
- `flowcut:highlight_batch`：新 batch 编排器。
- `flowcut:highlight_episode_prepare`：单集下载/归一化。
- `flowcut:highlight_merge_decompose`：合并/粗拆镜。
- `flowcut:highlight_start_select`：Gemini 选择高光起点。
- `flowcut:highlight_span_plan`：候选片段细拆/规划/产出 creative。

Worker 并发度由环境变量控制，如 `FLOWCUT_MATERIAL_PROCESS_CONCURRENCY`、`FLOWCUT_HIGHLIGHT_EP_PREP_CONCURRENCY`、`FLOWCUT_HIGHLIGHT_SPAN_PLAN_CONCURRENCY`。

## 7. 已知问题清单

### P0：部署构建文件与依赖锁不一致

`Dockerfile.flowcut` 和 `Dockerfile.mj` 都执行：

```dockerfile
COPY flowcut_frontend/package.json flowcut_frontend/package-lock.json ./
RUN npm install
```

但当前仓库没有 `flowcut_frontend/package-lock.json`，只有 `pnpm-lock.yaml`。这会导致 Docker build 在 COPY 阶段失败。需要二选一：

- 补齐并提交 `package-lock.json`；
- 或改 Dockerfile 使用 pnpm/corepack，并复制 `pnpm-lock.yaml`。

### P0：数据库未完全重构，FlowCut 与 Mojing/nb 表混在一起

`ensure_schema()` 会创建大量 `nb_*` 魔镜/共享历史表，包括皮肤日记、深度报告、memory 等，与 FlowCut 当前业务无关。风险：

- 新环境初始化时表很多，交接和排障成本高。
- FlowCut 的登录/任务/会话和历史 nb 表边界不清。
- 手写迁移散落在 `ensure_schema()` 末尾，缺少版本化迁移工具，线上 schema 演进不可审计。
- `nb_cron_jobs` 遇到旧 `job_id` schema 会直接 `DROP TABLE`，如果线上有有效数据会有风险。

建议后续把 schema 拆为：

- `simpleclaw` 必需共享表；
- FlowCut 专属表；
- Mojing 历史兼容表。

再引入 Alembic 或等价迁移机制，停止把所有迁移堆在启动函数里。

### P0：千川账号管理 API 未完成

`/qianchuan/account-summary` 和 `/qianchuan/sync` 可用，但以下接口仍返回 501：

- `GET /qianchuan/accounts`
- `GET /qianchuan/oauth/start`
- `GET /qianchuan/oauth/callback`
- `POST /qianchuan/token/refresh`

当前千川发布依赖浏览器自动化和 CDP，账号授权、token 生命周期、正式计划创建能力仍需补齐。`qianchuan_publish` executor 虽有流程，但真实可用性依赖 `Flowcut.services.qianchuan_publisher` 和运行环境浏览器。

### P0：普通 compose_video 工具仍未实现

`SimpleClaw/Flowcut/tools/compose_video.py` 的 `prepare_task()` 仍是 `raise NotImplementedError`。也就是说 Agent 调 `compose_video` 做普通“脚本+素材拼片”会失败。

同时 `flowcut:video_compose` worker 当前注册的是 `highlight_compose` 和 `highlight_export`，偏跨集高光成片，而不是早期设计里的普通素材拼片。需要决定：

- 普通拼片是否继续做；
- 若继续做，补 `compose_video` task envelope、executor、前端入口；
- 若不做，清理 Agent 工具描述，避免模型误调用。

### P1：旧串行链路和新 batch 管道并存，未及时清理

跨集高光现在有两条链路：

- 旧链路：`highlight_plan`，单体 executor，代码在 `runtime/executors.py`。
- 新链路：`highlight_batch` + episode/merge/start/span 多阶段 worker。

`create_cross_episode_highlights` 默认走新 batch，但全部提交失败时会自动回退旧链路。风险：

- 两套状态机、两套任务进度和两套错误处理并存。
- 前端列表里也兼容 `highlight_plan` 和 `highlight_batch`。
- 旧链路代码仍包含串行逐集拆镜逻辑，可能拖慢生成时间。

建议稳定新 batch 后，设置明确的开关和下线计划；至少把旧链路只作为受控 fallback，不要默默回退。

### P1：中转 API 视频能力仍有不确定性

`gemini_video.py` 支持 `GEMINI_BASE_URL` 中转模式。中转模式始终倾向 inline base64 视频，并用较低 `GEMINI_BASE_URL_INLINE_MAX_MB` 控制请求体大小。现有文档 `docs/xmsmartlink-video-benchmark.md` 说明中转能力还需要专项验证。

已知风险：

- 第三方中转常不支持 Gemini Files API，只能 inline，视频稍大容易 413。
- 代码有多档压缩兜底，但压缩过狠会影响镜头/台词/空镜判断质量。
- 并发 3 以上的稳定性要靠压测确认。

替换中转前至少确认：文本成功率、小视频成功率、30 秒视频成功率、并发 3 稳定性、P95 耗时、JSON 可解析率。

### P1：空镜/断句/台词分段需要继续优化

当前拆镜 prompt 要求每段 2-5 秒，`copy` 为空代表空镜/产品展示；高光起点选择 prompt 会排除片头、纯空镜、极短 copy。仍可能存在：

- 空镜段和旁白段边界不准，导致 `copy` 被切散或错挂到相邻镜头。
- 纯画面高情绪但无台词的段落被起点选择排除过强。
- 视频压缩去音轨后，Gemini 只能基于画面推断，普通素材处理里现在注释写明“不再走字节 ASR”，这会影响逐字口播准确度。
- 旧的 ASR WebSocket 函数仍留在 `runtime/executors.py`，但 `_process_video()` 当前走 Gemini 同时返回 visual/copy，语音链路需要统一。

TODO：补充线上实际表现样例，特别是“空境断句”的具体 bad case、期望切法和验收标准。

### P1：生成时间和生成数量需要产品化配置

当前高光候选数量 `num_candidates` 默认 3，但工具描述写“不设上限”。生成时间受这些因素影响：

- 视频下载和 FFmpeg 归一化。
- Gemini 视频拆镜和起点选择。
- episode prepare/span plan 并发度。
- 中转 inline 大小限制和重压缩。
- 候选数量、剧集范围、数字人/前贴组合。

建议后续做：

- 按剧集数、候选数、视频时长预估耗时，并在前端展示。
- 设置单任务最大候选数、最大剧集范围、最大视频时长。
- 对 `num_candidates` 做套餐化：快速 1 条、标准 3 条、批量 N 条。
- 记录每个阶段耗时到 `fc_highlight_event_log`，形成 P50/P95 看板。

### P1：动态上下文仍是空实现

`TaskContextProvider.collect_dynamic_context()` 当前直接返回 `[]`，未把当前脚本、素材匹配、任务状态注入 Agent。影响：

- Agent 对当前 UI/任务状态主要依赖用户消息和 `UIContextAttentionProvider`。
- 多轮对话中容易不知道当前脚本或任务进度。
- `check_task_status` 之外缺少主动上下文。

建议补齐 session 最新 script/creative/task 摘要注入。


### P2：任务/日志表结构还需治理

已经有 `fc_client_event_log`、`fc_highlight_event_log`、`nb_runtime_tasks`、`nb_agent_tool_invocations`，但事件写入覆盖面需要确认。


## 8. 当前部署状态

现有 `docs/漫剧服务器部署步骤.md` 写明：

- 当前部署范围：跨集高光功能。
- 测试服务器：`118.145.77.153`。
- 镜像构建使用 `Dockerfile.mj`。
- 容器暴露 80，nginx 反代 `/api` 到 uvicorn `8001`。
- 最小依赖：MySQL、OSS、Gemini、FFmpeg；Redis 在部署文档里写“可选留空”，但 compose 版本会启动 Redis。


## 9. 后续

1. 修复 Dockerfile 锁文件问题，保证新机器可一键 build。
2. 完成数据库拆分/迁移治理，至少先把 FlowCut 表结构单独导出成文档。
4. 关闭或显式开关旧 `highlight_plan` 串行链路，避免不透明 fallback。
5. 补全千川账号/OAuth/token refresh，或在 UI 上隐藏未完成入口。
6. 优化空镜断句：收集 bad case，调整 prompt、ASR/Gemini 分工
7. 做生成耗时优化：阶段耗时日志、并发参数、候选数量限制、视频压缩策略 A/B。
8. 补 TaskContextProvider，让 Agent 能感知当前制作状态。


