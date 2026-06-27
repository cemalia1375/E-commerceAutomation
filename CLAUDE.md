# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Layout

This is a monorepo for the **FlowCut** e-commerce automation project, with the following structure:

```
E-commerceAutomation/
  flowcut_frontend/     # React + Vite frontend
  SimpleClaw/
    simpleclaw/         # Harness core — shared LLM orchestration library
    Mojing/             # Existing business: 魔镜 (reference architecture)
    Flowcut/            # New business: FlowCut MVP
    tests/              # Test suite (shared)
    script/             # Benchmark & runner scripts
    admin/              # Admin debug panel
    dev.sh              # Mojing dev server launcher
```

**Critical architecture rule:** `simpleclaw/` is the shared harness layer. Business modules (`Mojing/`, `Flowcut/`) import `simpleclaw` as a library. Traditional backend services are built as separate services that interact with the harness via its chat/conversation API.

## Frontend — `flowcut_frontend/`

### Tech Stack
- React 19 + TypeScript + Vite
- Ant Design 6 + react-router-dom
- Zustand for state management
- CSS Modules for styling

### Commands
```bash
cd flowcut_frontend
npm install
npm run dev          # Vite dev server
npm run build        # tsc + vite build
npm run lint         # ESLint
```

### Structure
- `src/components/` — UI components organized by feature: `generate/`, `material/`, `creative/`, `layout/`, `common/`
- `src/stores/` — Zustand stores: `generateStore.ts`, `materialStore.ts`, `creativeStore.ts`
- `src/types/index.ts` — Shared TypeScript types (Material, Creative, Script, ChatMessage, etc.)
- `src/mocks/` — Mock data for development
- `src/router.tsx` — React Router routes: `/`, `/material`, `/creative`
- `src/theme.ts` — Ant Design theme config (colorPrimary: `#2563eb`)

## Backend — `SimpleClaw/`

### Tech Stack
- Python + FastAPI + uvicorn
- **uv** for Python environment management (instead of pip/conda)
- aiomysql / PyMySQL for database
- openai SDK (for Volcengine / Doubao LLM)
- loguru + pydantic + httpx
- redis + croniter (task queues & scheduling)
- pytest + pytest-asyncio for testing

### Commands
```bash
cd SimpleClaw

# Install dependencies with uv
uv pip install -r requirements.txt

# Start Mojing dev server (with hot reload)
./uvdev.sh [PORT]

# Start SimpleClaw dev server
uv run python -m uvicorn simpleclaw.api.server:app --reload

# Run tests
uv run pytest -m unit          # Fast unit tests
uv run pytest -m integration   # Multi-component tests
uv run pytest -m smoke         # End-to-end health checks
uv run pytest -m external      # Tests calling LLM/API providers

# Run a single test file
uv run pytest tests/test_runtime_pipeline.py -v

# Run smoke test manually
uv run python -m tests.smoke_test
```

### Test Markers (from `pyproject.toml`)
- `unit`, `integration`, `smoke`, `external`, `e2e`

### Environment
Copy `.env.example` to `.env` and fill in (Flowcut requires Google Gemini; Mojing uses Volcengine/Doubao):

**Google Gemini (Flowcut):**
- `GOOGLE_API_KEY`, `GOOGLE_MODEL` (default: `gemini-3.5-flash`)
- `FLOWCUT_HOOK_MODEL`, `FLOWCUT_FIRST_TOKEN_MODEL`, `FLOWCUT_FIRST_TOKEN_ENABLED`
- `FLOWCUT_DECOMPOSE_MODEL` (default: `gemini-3.1-flash-lite-preview`)

**Volcengine / Doubao (Mojing):**
- `VOLCENGINE_API_KEY`, `VOLCENGINE_API_BASE`, `VOLCENGINE_MODEL`

**MySQL (shared):**
- `MYSQL_HOST`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DB`, `MYSQL_PORT`

**Redis** (optional — falls back to in-memory queue if empty):
- `REDIS_URL`

**OSS 对象存储 (Flowcut):**
- `FLOWCUT_OSS_ENDPOINT`, `FLOWCUT_OSS_ACCESS_KEY_ID`, `FLOWCUT_OSS_ACCESS_KEY_SECRET`, `FLOWCUT_OSS_BUCKET`

**ASR 语音识别 (Flowcut — 字节跳动 BigModel):**
- `FLOWCUT_ASR_APP_KEY`, `FLOWCUT_ASR_ACCESS_KEY`

**Embedding & 向量搜索 (Flowcut):**
- `OLLAMA_BASE_URL` (default: `http://localhost:11434`)
- `OLLAMA_EMBEDDING_MODEL` (default: `bge-m3`)
- `QDRANT_URL` (default: `http://localhost:6333`)

## Architecture — `simpleclaw/` (Harness Core)

This is the shared orchestration layer. Business modules **must not** put harness logic here.

### Key Modules

- **`simpleclaw/core/loop.py`** — `ReactLoop`: the ReAct execution engine.
  - Stream LLM tokens → collect tool calls → execute tools → inject results → repeat until no coupled tools remain.
  - Tools are split into **coupled** (`needs_followup=True`, results fed back to LLM) and **decoupled** (`needs_followup=False`, fire-and-forget).
  - Supports `ContextBuilder` for dynamic context assembly and `ContextCompressor` for memory management.

- **`simpleclaw/llm/base.py`** — `LLMProvider` abstract interface.
  - `stream()` yields `TextChunk` / `ToolCallChunk`.
  - `stream_with_retry()` handles transient errors with exponential backoff.
  - `VolcengineLLM` (in `volcengine.py`) is the concrete provider for Doubao models.

- **`simpleclaw/tools/base.py`** — `Tool` abstract base class.
  - `execution_mode`: `"inline"` (sync) vs `"durable"` (async task queued).
  - `needs_followup`: whether the tool result must be fed back into the ReAct loop.
  - `ToolResult` carries `content` (string) and `ok` (bool).

- **`simpleclaw/tools/registry.py`** — `ToolRegistry`.
  - Register tools, get JSON schemas, execute by name, check `needs_followup`.

- **`simpleclaw/context/builder.py`** — `ContextBuilder`.
  - Assembles the final message list for the LLM.
  - Splits system prompt into **stable prefix** (cached via prefix cache) and **dynamic tail** (per-turn).
  - Handles attention packets and image placeholder replacement for historical images.

- **`simpleclaw/harness/lifecycle.py`** — `ToolLifecycle` + `BeforeToolHook`.
  - Tool gates: inspect invocations before execution and allow/deny them.
  - `ToolGateDecision` with `allowed`, `ok`, `action`, `reason`, `phase` fields.

- **`simpleclaw/runtime/`** — Task queue, scope locks, side effects, services.
  - `TaskQueue` (InMemory or Redis), `ScopeLockRegistry`, `RuntimeServices`.
  - **`simpleclaw/runtime/worker.py`** — `TaskWorker` (moved here from Mojing during Flowcut build). Both Mojing and Flowcut import `TaskWorker` from this shared location. `Mojing/runtime/worker.py` now re-exports it for backwards compatibility.

- **`simpleclaw/subagent/`** — Subagent abstraction (`SubagentBase`, `SubagentRunner`).

- **`simpleclaw/api/server.py`** — Lightweight dev server for harness testing.
  - `POST /chat` — SSE stream via `ReactLoop`.
  - `GET /admin` — Admin debug page.

## Architecture — Business Modules (`Mojing/` as Reference)

`Mojing/` is the reference architecture for all business modules. `Flowcut/` should follow this pattern.

### Directory Pattern
```
Mojing/
  agent/          # MainAgent, FirstTokenAgent, postprocess, cold_path
  api/            # FastAPI routes, server, DI container
  context/        # Business-specific dynamic context providers
  tools/          # Business-specific tools
  storage/        # Repositories (MySQL)
  runtime/        # Business-specific task executors & workers
  subagent/       # Subagent implementations
  workspace/      # System prompt files (Agent.md, SOUL.md, TOOL.md, compliance.md)
```

### Key Patterns

- **`Mojing/api/container.py`** — `AppContainer` + `build_container()`.
  - Centralized dependency injection: initializes all repos, LLM instances, agents, stores, workers.
  - Called by `server.py` on startup. Workers are started as asyncio tasks.

- **`Mojing/agent/main_agent.py`** — `MainAgent`.
  - Factory for `ContextBuilder` and `ToolRegistry` per tenant/session.
  - Holds `tool_factories`, `device_tool_factories`, `staged_tool_factories`.
  - SessionStore calls `main_agent.make_context_builder()` and `main_agent.make_tool_registry()` on each turn.

- **`Mojing/config.py`** — Application config.
  - `load_stable_sections()` reads `workspace/*.md` files (Agent.md → SOUL.md → TOOL.md → compliance.md → journey/{stage}.md).
  - `make_llm_config()` / `make_hook_llm_config()` / `make_first_token_llm_config()` for different LLM instances.

- **`Mojing/storage/`** — Repository pattern.
  - `Database` (aiomysql pool), `SessionRepository`, `DocumentRepository`, `ImageRepository`, `TopicRepository`, etc.

- **`Mojing/runtime/worker.py`** — `TaskWorker`.
  - Consumes tasks from `TaskQueue` streams, executes via named executors, updates task state.

## FlowCut MVP — Architecture (`Flowcut/`)

`Flowcut/` is a 抖音千川 content production tool. Core pipeline (拆镜、脚本、素材匹配、导出) fully implemented; FFmpeg compose and Qianchuan publishing remain stubbed.

### Directory Structure
```
Flowcut/
  agent/          # MainAgent, FirstTokenAgent, PostprocessHook, ColdPathHook
  api/            # FastAPI routes, server, AppContainer (build_container)
  context/        # TaskContextProvider (returns [] — stub)
  tools/          # 10 Tool classes: decompose_video, generate_scripts, search_materials,
                  #   upload_script, update_script, match_by_script, export_package,
                  #   compose_video, check_task_status, publish_to_qianchuan
  storage/        # MaterialRepo, CreativeRepo, ScriptRepo, QianchuanRepo,
                  #   ReferenceVideoRepo, SessionRepo, VectorStore + shared repos
  runtime/        # FlowcutTaskStream (8 streams — see below), executors, make_workers()
  services/       # gemini_video.py, scene_align.py, script_generator.py, embedding.py,
                  #   material_matcher.py, zip_parser.py, douyin_client.py
  workspace/      # Agent.md, SOUL.md, TOOL.md, compliance.md
  scripts/        # 工具脚本 (reset_db.py, spike_asr_response.py 等)
  config.py       # FLOWCUT_* env vars + OSS config
```

### Start Flowcut dev server
```bash
cd SimpleClaw
uv run python -m uvicorn Flowcut.api.server:app --reload --port 8001
```

### DB Tables (MySQL, created via ensure_schema on startup)
- `fc_reference_video` — 爆款视频 (status: `PROCESSING → READY / FAILED`；拆镜完成即终态；`product` nullable；`audio_oss_key` 存音轨；`script_id` 关联产出的 fc_script)
- `fc_script` — 脚本表 (source: `decomposed / uploaded`; status: `DRAFT / CONFIRMED`; `segments_json`: `[{idx, start_time, end_time, visual, copy}]`)
- `fc_material` — 素材主表 (status: `PROCESSING → READY / FAILED`; `vector_indexed` 标记 Qdrant 同步状态)
- `fc_creative` — 成片表 (status: `PENDING → COMPOSING → READY / FAILED`; label: `NORMAL / HOT / DEAD`)
- `fc_material_usage` — 素材↔成片多对多
- `fc_qianchuan_account` — 千川账号 + OAuth token 存储

**注意：** `fc_reference_video` 不再产生子片段 `fc_material` 行（2026-05-23 决策）。`fc_material` 只由 zip/单文件素材上传填充。

### Task Streams (8 条)
| Stream | 职责 | 状态 |
|--------|------|------|
| `MATERIAL_PROCESS` | 素材 ASR + 描述 + 向量索引 | 已实现 |
| `SCENE_DECOMPOSE` | 爆款视频拆镜 → fc_script + 音轨 | 已实现（含 ASR copy 字段）|
| `VIDEO_COMPOSE` | FFmpeg 拼片 | Stubbed |
| `QIANCHUAN_PUBLISH` | 千川素材上传 + 创建计划 | Stubbed |
| `QIANCHUAN_SYNC` | T+1 数据回流 | Stubbed |
| `VECTOR_REPAIR` | 补建 Qdrant 向量 | 已实现 |
| `EXPORT_PACKAGE` | 素材打包 zip 下载 | 已实现 |

### Key Flowcut design decisions
- **No journey stages** — MainAgent uses single `"default"` stage
- **No subagents** — long-running work goes through TaskQueue only
- **Tool factories** — All tools instantiated in `build_container()` via lambda factories
- **LLM** — Uses `GeminiLLM` (not VolcengineLLM as in Mojing)
- **OSS key format:**
  - Materials (zip/单文件): `materials/{tenant_key}/{product}/uploads/{ts}_{filename}`
  - Reference video: `uploads/{tenant_key}/{ts}_{filename}`
  - Audio track: `reference_videos/{tenant_key}/{ref_video_id}/audio.mp3`
  - Fallback product when omitted: `通用`
- **`product` field nullable** — 上传时可不选产品，拆镜/匹配时再指定
- **Upload size limit** — 500 MB cap (HTTP 413 on exceed)
- **fc_script lifecycle** — `DRAFT` (可编辑) → `CONFIRMED` (可召回) → `DRAFT` (重新编辑后)
- **Dual-vector recall** — `match_by_script` 分别对 `visual` 和 `copy` embed，查对应向量池
- **Export is async** — `export_package` 走 `EXPORT_PACKAGE` stream，前端轮询 `/tasks/{task_id}`

## simpleclaw-specific Design Notes

- **Prefix cache** — `ContextBuilder` supports Volcengine prefix cache via `_cache_stable_prefix` / `_cache_dynamic_tail` keys in system messages.
- **Attention packets** — Injected into the message stream at specific placements (`before_last_user`, `after_history`, `tail`) with deduplication logic (`until_changed`, `periodic`, `one_turn`, `always`).
- **Image handling** — Only the most recent user image is sent as multimodal input. Historical images are replaced with `[用户已上传图片]` placeholder.
- **durable tools** — Tools that trigger long-running work use `execution_mode="durable"` and are queued via `RuntimeServices.submit_task()`. The ReAct loop receives an immediate ack and continues.
