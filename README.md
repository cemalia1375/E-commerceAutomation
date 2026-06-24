# FlowCut

抖音千川内容生产工具。核心流程：拿同行爆款 → 拆出画面和文案节奏 → 套上自家素材 → 拼成千川投放视频。

---

## 整体结构

```
E-commerceAutomation/
├── flowcut_frontend/        # React 前端
├── SimpleClaw/              # Python 后端（git submodule）
│   ├── simpleclaw/          # 通用 Agent 编排库（不含业务逻辑）
│   ├── Flowcut/             # FlowCut 业务代码（主战场）
│   ├── Mojing/              # 魔镜业务代码（参考架构，别改）
│   ├── admin/               # 魔镜 Admin 调试页
│   └── tests/               # 共享测试套件
├── Dockerfile.flowcut                 # 单镜像构建（前端 + 后端 + nginx）
├── docker-compose.flowcut.yml         # 本地开发用
└── docker-compose.flowcut.server.yml  # 服务器部署用
```

**重要：** `SimpleClaw/` 是独立 git 仓库（submodule），提交时要分别 commit。

---

## 前端 `flowcut_frontend/`

**技术栈：** React 19 + TypeScript + Vite + Ant Design 6 + Zustand + CSS Modules

```
src/
├── api/              # 所有后端请求封装（每个资源一个文件）
│   ├── client.ts          # axios 实例，统一处理 baseURL / auth header
│   ├── materials.ts       # 素材相关接口
│   ├── referenceVideos.ts # 爆款视频接口
│   ├── script.ts          # 脚本接口
│   ├── creatives.ts       # 成片接口
│   ├── qianchuan.ts       # 千川账号接口
│   └── highlightAssets.ts # 高光素材接口
│
├── components/       # 按功能模块组织
│   ├── generate/     # 生成 Tab（核心）：上传爆款、脚本编辑、拆镜流程
│   ├── material/     # 素材 Tab：素材库管理、上传
│   ├── creative/     # 成片 Tab：成片库、高光成片
│   ├── workspace/    # 工作区子 Tab（脚本/匹配/预览/导出/高光）
│   ├── layout/       # AppShell、Header
│   └── common/       # 通用组件（MediaPreview、FilterChips 等）
│
├── stores/           # Zustand 状态管理（每个资源一个 store）
│   ├── materialStore.ts
│   ├── creativeStore.ts
│   ├── scriptStore.ts
│   ├── productTreeStore.ts  # 产品树（用于按产品筛选）
│   ├── authStore.ts
│   └── uiContextStore.ts
│
├── types/index.ts    # 全局类型定义
└── router.tsx        # 路由配置（/ 生成、/material 素材、/creative 成片）
```

---

## 后端核心 `SimpleClaw/Flowcut/`

FlowCut 的所有业务逻辑都在这里。

```
Flowcut/
├── api/
│   ├── server.py      # FastAPI 应用入口，挂载所有路由
│   ├── container.py   # AppContainer：依赖注入，初始化所有 repo / service / worker
│   ├── deps.py        # FastAPI Depends 工具（数据库连接、鉴权）
│   └── routes/        # REST 路由（每个资源一个文件）
│       ├── materials.py
│       ├── reference_videos.py
│       ├── scripts.py
│       ├── creatives.py
│       ├── highlight_assets.py
│       ├── qianchuan.py
│       ├── tasks.py        # 轮询任务状态
│       ├── chat.py         # Agent 对话（POST /agent/chat，SSE 流式，前端已接入）
│       ├── sessions.py     # 会话列表管理（创建 / 查询 session）
│       ├── auth.py         # 登录 / 登出
│       └── health.py       # 健康检查
│
├── storage/           # 数据层（MySQL + Qdrant + OSS）
│   ├── database.py         # MySQL 连接池 + ensure_schema（建表）
│   ├── material_repo.py
│   ├── reference_video_repo.py
│   ├── script_repo.py
│   ├── creative_repo.py
│   ├── highlight_asset_repo.py
│   ├── qianchuan_repo.py
│   ├── user_repo.py
│   ├── session_repo.py
│   ├── session_store.py    # 内存会话状态（ReactLoop 实例缓存）
│   ├── task_repo.py        # 异步任务状态持久化
│   ├── vector_store.py     # Qdrant 向量库封装
│   └── oss_client.py       # 火山引擎 OSS / 兼容 S3 封装
│
├── services/          # 原子业务能力
│   ├── gemini_video.py      # Gemini 视频语义拆镜
│   ├── scene_align.py       # PySceneDetect 物理切点对齐
│   ├── script_generator.py  # Gemini 脚本生成
│   ├── embedding.py         # 向量化（支持 Ollama/bge-m3、OpenAI-compatible、火山 Ark）
│   ├── material_matcher.py  # 双向量召回（visual + copy）
│   ├── zip_parser.py        # ZIP 素材包解析
│   ├── clip_planner.py      # 高光切片规划
│   ├── douyin_client.py     # 抖音 API 客户端
│   ├── qianchuan_publisher.py  # 千川广告发布（stub）
│   └── qianchuan_scraper.py    # 千川数据回流（stub）
│
├── runtime/           # 异步任务调度
│   ├── streams.py     # 任务流名称常量（8 条流）
│   ├── executors.py   # 每条流的实际业务逻辑
│   ├── worker.py      # TaskWorker 实例化
│   └── reconcile.py   # 向量修复对账
│
├── agent/             # Agent 对话轨道（前端已接入，通过 ChatPanel 与 Agent 交互）
│   ├── main_agent.py
│   ├── first_token.py
│   ├── postprocess.py
│   ├── cold_path.py
│   └── capabilities.py
│
├── tools/             # Agent 工具（对话轨使用，前端可通过 Agent 触发所有 Tool）
├── context/           # Agent 动态上下文（注入 UI 位置等运行时信息）
├── workspace/         # Agent 系统 prompt（Agent.md / SOUL.md / TOOL.md）
├── browser/           # 千川网页自动化（cookie 采集、流量录制）
├── auth/              # JWT 鉴权逻辑
├── skills/            # 高光分析技能
└── config.py          # 所有环境变量读取入口
```

### 双轨架构

后端有两条并行入口：

| 轨道 | 入口 | 当前状态 |
|------|------|---------|
| **UI 轨** | `Flowcut/api/routes/*` → 直接调 services / 入队 | 已接入，前端按钮走这里 |
| **对话轨** | `POST /agent/chat` → ReactLoop → Tools | 已接入，前端 ChatPanel 可与 Agent 对话并触发所有 Tool |

业务逻辑统一写在 `Tools` 里，REST 路由只做薄薄一层 HTTP 适配，保证两条轨共用同一份实现。

### 后台任务流（8 条）

| 流名称 | 职责 | 状态 |
|--------|------|------|
| `flowcut:material_process` | 素材 ASR + 描述 + 向量索引 | 已实现 |
| `flowcut:scene_decompose` | 爆款视频拆镜 → 生成脚本 + 提取音轨 | 已实现 |
| `flowcut:video_compose` | FFmpeg 拼片 | **Stub** |
| `flowcut:qianchuan_publish` | 素材上传千川 + 创建广告计划 | **Stub** |
| `flowcut:qianchuan_sync` | 千川 T+1 数据回流 | **Stub** |
| `flowcut:vector_repair` | 补建 Qdrant 向量索引 | 已实现 |
| `flowcut:export_package` | 素材打包 zip 下载 | 已实现 |
| `flowcut:highlight_plan` | 跨集高光切片规划 | 已实现 |

---

## 通用编排层 `SimpleClaw/simpleclaw/`

从魔镜抽出的通用库，Flowcut 和 Mojing 共用。**不要在这里写业务逻辑。**

| 模块 | 作用 |
|------|------|
| `core/loop.py` | `ReactLoop`：ReAct 对话+工具循环引擎 |
| `llm/` | LLM Provider 抽象（GeminiLLM、VolcengineLLM） |
| `tools/` | Tool 基类、ToolRegistry |
| `runtime/` | TaskQueue（内存/Redis）、TaskWorker、ScopeLock |
| `context/builder.py` | 系统 prompt 拼装、prefix cache |

