# FlowCut

抖音千川内容生产工具。核心流程：拿同行爆款 → 拆出画面和文案节奏 → 套上自家素材 → 拼成千川投放视频。

> 当前重点：测试/服务器部署主要覆盖“漫剧跨集高光”功能；素材脚本自动化链路已有实现，但普通拼片、千川账号 OAuth 等仍有待补齐。详细交接请看 [`docs/FlowCut项目交接文档.md`](docs/FlowCut项目交接文档.md)。

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
├── Dockerfile.mj                      # 漫剧部署镜像（当前部署文档使用）
├── docker-compose.flowcut.yml         # 本地开发用
└── docker-compose.flowcut.server.yml  # 服务器部署用
```

**重要：** `SimpleClaw/` 是独立 git 仓库（submodule），提交时要分别 commit。

---

## 前端 `flowcut_frontend/`

**技术栈：** React 19 + TypeScript + Vite + Ant Design 6 + Zustand + CSS Modules

常用命令：

```bash
cd flowcut_frontend
npm run dev
npm run build
npm run lint
```

前端默认通过 `VITE_API_BASE_URL` 访问后端；未配置时使用 `http://localhost:8001`。容器构建时默认把 `VITE_API_BASE_URL` 设为 `/api`，由 nginx 反代到后端。

### Windows 重启后完整启动步骤

#### 第一步：启动依赖服务

重启电脑后，MySQL 和 Qdrant 通常不会自动启动，需要先手动启动。

启动 MySQL：

```powershell
# 如果 MySQL 是 Windows 服务，先检查状态
Get-Service MySQL80

# 如果没在运行，启动它
Start-Service MySQL80
```

启动 Qdrant：

```powershell
# 如果用 Docker 装的，先看容器名
docker ps -a --filter name=qdrant

# 启动 Qdrant 容器
docker start <你的qdrant容器名>
```

如果 Qdrant 是通过 `docker-compose.flowcut.yml` 启动的：

```powershell
cd d:\PyCharm\E-commerceAutomation
docker compose -f docker-compose.flowcut.yml up -d
docker compose -f docker-compose.flowcut.yml up -d --wait
```

如果用本地 exe 安装的 Qdrant，直接双击启动，或从命令行启动对应 exe。

#### 第二步：确认依赖就绪

```powershell
# 确认 MySQL 能连
mysql -u root -p -e "SELECT 1"

# 确认 Qdrant 能连
curl http://localhost:6333/health
```

#### 第三步：启动项目

方式 A：一键 Electron 模式（推荐）

```powershell
cd d:\PyCharm\E-commerceAutomation\flowcut_frontend
pnpm run electron:dev
```

Electron dev 会自动启动 Python 后端，不需要手动启动后端。

方式 B：前后端分开启动（调试用）

终端 1：后端

```powershell
cd d:\PyCharm\E-commerceAutomation\SimpleClaw
uv run python -m uvicorn Flowcut.api.server:app --port 8001 --host 127.0.0.1 --reload
```

如果 8001 被占用，也可以临时换端口：

```powershell
uv run python -m uvicorn Flowcut.api.server:app --port 8010 --host 127.0.0.1 --reload
```

终端 2：前端

```powershell
cd d:\PyCharm\E-commerceAutomation\flowcut_frontend
pnpm run dev
```

浏览器访问 `http://localhost:5173`。这种方式不走 Electron。

#### 首次搭建才需要做的事

以下只在首次搭建时需要，重启电脑后不用重复：

- 安装 Python 3.11+ 和 uv。
- 安装 Node.js 20 和 pnpm。
- 配好 `SimpleClaw/.env`（从 `.env.example` 复制并填写）。
- 在 `flowcut_frontend/` 下执行过 `pnpm install`。
- 在 `SimpleClaw/` 下执行过 `uv pip install -r requirements.txt`。

### 常见端口和残留进程处理

如果 Electron dev 或后端启动异常，先检查 8001 是否被残留进程占用。

```powershell
# 查谁占用 8001（含命令行，能区分是 App 还是 uvicorn）
Get-NetTCPConnection -LocalPort 8001 -State Listen |
  ForEach-Object {
    Get-CimInstance Win32_Process -Filter "ProcessId=$($_.OwningProcess)" |
    Select-Object ProcessId, Name, CommandLine
  }

# 结束占用进程
Stop-Process -Id <PID> -Force

# 一次性清掉残留的打包后端（谨慎：会杀掉所有安装包后端）
Get-Process flowcut_server -ErrorAction SilentlyContinue | Stop-Process -Force
```

如果只是想清理所有残留 Python 后端：

```powershell
# 谨慎：会杀掉所有 python.exe
taskkill /F /IM python.exe
```

然后重新启动：

```powershell
cd d:\PyCharm\E-commerceAutomation\flowcut_frontend
pnpm run electron:dev
```

### 开发环境与安装包 App 切换

核心原则：开发后端和安装包后端都可能抢占 `8001`。每次切换时，必须先把上一套停干净，确认 `8001` 空了，再启动下一套。

前置（只需做一次）：如果安装包 App 登录失败，检查安装包后端配置是否缺少 `MYSQL_HOST`。编辑 `D:\App\Flowcut\resources\backend\.env`，确保存在：

```dotenv
MYSQL_HOST=118.145.101.96
```

#### 流程 A：本地开发 → 测试安装包 App

目标：停掉开发后端和 Electron dev，释放 `8001`，让安装包 App 独占。

```powershell
# 1. 停开发前端：在跑 pnpm run electron:dev 的终端按 Ctrl+C，并关闭弹出的 Electron 窗口
# 2. 停开发后端：在跑 uvicorn 的终端按 Ctrl+C

# 3. 确认 8001 已释放
Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue |
  ForEach-Object {
    Get-CimInstance Win32_Process -Filter "ProcessId=$($_.OwningProcess)" |
    Select-Object ProcessId, Name, CommandLine
  }

# 无输出 = 干净，可以继续
# 如果还有 python/uvicorn 残留：
Stop-Process -Id <PID> -Force
```

然后启动安装包 App：双击 `D:\App\Flowcut\FlowCut.exe`（或开始菜单图标）。App 启动后会自己在 `8001` 起打包后端 `flowcut_server.exe`。

#### 流程 B：测完安装包 App → 回本地开发

目标：彻底退出 App 和它的后端进程，释放 `8001`，再启动开发环境。

```powershell
# 1. 正常退出 App：关闭 FlowCut 窗口 / 托盘右键退出

# 2. 确认 App 后端真的没了
Get-Process flowcut_server -ErrorAction SilentlyContinue |
  Select-Object Id, StartTime, Path

# 如果还有残留：
Stop-Process -Name flowcut_server -Force

# 3. 再确认 8001 空了
Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue
```

确认无输出后，启动开发环境。

终端 1：开发后端

```powershell
cd d:\PyCharm\E-commerceAutomation\SimpleClaw
uv run python -m uvicorn Flowcut.api.server:app --port 8001 --host 127.0.0.1 --reload
```

终端 2：开发前端

```powershell
cd d:\PyCharm\E-commerceAutomation\flowcut_frontend
pnpm run electron:dev
```

Electron dev 会探测 `8001` 上健康且配置匹配的开发后端，能复用则直接复用。

#### rebuild 安装包前同步环境变量

以后 rebuild 前，先把开发机 `.env` 同步到 `backend-dist/.env`，避免把过期 key 打包进去：

```powershell
copy SimpleClaw\.env backend-dist\.env
cd flowcut_frontend
npx electron-builder --win --x64
```

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
│   ├── streams.py     # 任务流名称常量（素材/拆镜/导出/跨集高光等）
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

### 后台任务流

| 流名称 | 职责 | 状态 |
|--------|------|------|
| `flowcut:material_process` | 素材处理、描述/转写、封面、向量索引 | 已实现 |
| `flowcut:scene_decompose` | 爆款视频拆镜 → 生成脚本 + 提取音轨 | 已实现 |
| `flowcut:video_compose` | 高光 compose / export 相关任务；普通 `compose_video` 工具尚未补齐 | 部分实现 |
| `flowcut:qianchuan_publish` | 浏览器自动化上传千川 + 创建计划 | 依赖 CDP，需联调 |
| `flowcut:qianchuan_sync` | 千川 T+1 数据回流 | 部分实现 |
| `flowcut:vector_repair` | 补建 Qdrant 向量索引 | 已实现 |
| `flowcut:export_package` | 素材打包 zip 下载 | 已实现 |
| `flowcut:highlight_plan` | 旧单体跨集高光链路 | 已实现，保留兼容 |
| `flowcut:highlight_batch` | 新跨集高光 batch 编排器 | 已实现 |
| `flowcut:highlight_episode_prepare` | 单集下载 + 归一化 | 已实现 |
| `flowcut:highlight_merge_decompose` | 合并 + 粗拆镜 | 已实现 |
| `flowcut:highlight_start_select` | 高光起点选择 | 已实现 |
| `flowcut:highlight_span_plan` | 候选片段细拆 + 规划 + 产出成片 | 已实现 |

---

## 数据库与部署现状

Schema 由 `SimpleClaw/Flowcut/storage/database.py::ensure_schema()` 在启动时创建/补迁移。当前仍会创建较多 `nb_*` 历史/共享表，以及 FlowCut 专属 `fc_*` 表。主要 FlowCut 表包括：

- `fc_reference_video`：参考/爆款视频。
- `fc_material`：普通素材库。
- `fc_highlight_asset`：漫剧原片、数字人、前贴等高光素材。
- `fc_script`：脚本和拆镜段。
- `fc_creative`：成片和高光成片。
- `fc_highlight_batch`、`fc_highlight_stage`：新跨集高光批量管道。
- `fc_client_event_log`、`fc_highlight_event_log`：客户端和高光链路日志。

部署方式：

- 本地 compose：`docker compose -f docker-compose.flowcut.yml up -d --build --wait`。
- 漫剧服务器部署文档：[`docs/漫剧服务器部署步骤.md`](docs/漫剧服务器部署步骤.md)。
- nginx 对外暴露 80，`/api/*` 反代到 uvicorn `127.0.0.1:8001/*`。

注意：当前仓库有 `pnpm-lock.yaml`，但 `Dockerfile.flowcut` / `Dockerfile.mj` 仍复制 `flowcut_frontend/package-lock.json`。如果没有补齐 `package-lock.json` 或改为 pnpm 构建，Docker build 会失败。

## 当前已知重点问题

- 数据库未完全重构：FlowCut 表和 Mojing/历史 `nb_*` 表仍混在同一个 `ensure_schema()`。
- 中转 API 待验证：`GEMINI_BASE_URL` 中转模式主要走 inline base64 视频，需压测 413、500、并发和 JSON 稳定性。
- 旧串行链路未清理：跨集高光同时保留旧 `highlight_plan` 和新 batch 管道。
- 空镜断句待优化：Gemini 拆镜、copy 分段、空镜过滤和 ASR/Gemini 分工还需用线上 bad case 校准。
- 普通 `compose_video` 工具未实现：Agent 触发普通脚本拼片会遇到 `NotImplementedError`。
- 千川账号 OAuth/refresh 接口仍返回 501，发布链路需要继续联调。
- 后续需要产品化配置生成数量、剧集范围、候选数和目标耗时，并沉淀阶段耗时看板。

更多问题、表结构和 TODO 见 [`docs/FlowCut项目交接文档.md`](docs/FlowCut项目交接文档.md)。

## 通用编排层 `SimpleClaw/simpleclaw/`

从魔镜抽出的通用库，Flowcut 和 Mojing 共用。**不要在这里写业务逻辑。**

| 模块 | 作用 |
|------|------|
| `core/loop.py` | `ReactLoop`：ReAct 对话+工具循环引擎 |
| `llm/` | LLM Provider 抽象（GeminiLLM、VolcengineLLM） |
| `tools/` | Tool 基类、ToolRegistry |
| `runtime/` | TaskQueue（内存/Redis）、TaskWorker、ScopeLock |
| `context/builder.py` | 系统 prompt 拼装、prefix cache |

