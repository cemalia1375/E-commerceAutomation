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
    Flowcut/            # New business: FlowCut MVP (currently empty)
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
- `GOOGLE_API_KEY`, `GOOGLE_MODEL` (default: `gemini-2.5-flash`)
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
  - Streams: `POSTPROCESS`, `TOPIC_TRACKING`, `IMAGE_ANALYSIS`, `SKIN_DIARY`, `MEMORY_EXTRACT`, `DEEP_RESEARCH`, `SUBAGENT_DISPATCH`, `BACKGROUND`.

### Workspace Prompt Files (`Mojing/workspace/`)
These are markdown files that form the system prompt, loaded in order:
1. `Agent.md` — Agent identity and behavior rules
2. `SOUL.md` — Personality and tone
3. `TOOL.md` — Tool usage guidelines
4. `compliance.md` — Shared compliance constraints
5. `journey/{stage}.md` — Stage-specific strategy (novice / explore / mature)

## FlowCut MVP — Architecture (`Flowcut/`)

`Flowcut/` is a 抖音千川 content production tool. Storage, infrastructure, gemini scene decomposition, and semantic material search are fully implemented; FFmpeg compose and Qianchuan publishing remain stubbed.

### Directory Structure
```
Flowcut/
  agent/          # MainAgent, FirstTokenAgent, PostprocessHook, ColdPathHook
  api/            # FastAPI routes, server, AppContainer (build_container)
  context/        # TaskContextProvider (returns [] — stub until task state is wired)
  tools/          # 6 Tool classes (decompose_video, generate_scripts, search_materials,
                  #   compose_video, check_task_status, publish_to_qianchuan)
  storage/        # MaterialRepo, CreativeRepo, ScriptRepo, QianchuanRepo, ReferenceVideoRepo, SessionRepo, VectorStore + shared repos
  runtime/        # FlowcutTaskStream (7 streams: material_process / scene_decompose / clip_create / video_compose / qianchuan_publish / qianchuan_sync / vector_repair), executors (partial), make_workers()
  services/       # gemini_video.py, scene_align.py, script_generator.py, embedding.py, material_matcher.py, zip_parser.py, douyin_client.py
  workspace/      # Agent.md, SOUL.md, TOOL.md, compliance.md, scripts/ (角色拆镜策略 JSON)
  config.py       # FLOWCUT_* env vars + OSS config
```

### Start Flowcut dev server
```bash
cd SimpleClaw
uv run python -m uvicorn Flowcut.api.server:app --reload --port 8001
```

### DB Tables (MySQL, created via ensure_schema on startup)
- `fc_reference_video` — 爆款视频 (status: PROCESSING → AWAITING_CLASSIFICATION → DECOMPOSED / FAILED；拆镜完成后等待用户分类确认再批量生成子片段；scene_data_json 存储拆镜结果)
- `fc_material` — 素材主表 (status: PROCESSING → READY / FAILED; vector_indexed 标记 Qdrant 同步状态)
- `fc_script` — 脚本表 (segments_json: JSON array)
- `fc_creative` — 成片表 (status: PENDING → COMPOSING → READY / FAILED; label: NORMAL / HOT / DEAD)
- `fc_material_usage` — 素材↔成片多对多
- `fc_qianchuan_account` — 千川账号 + OAuth token 存储

### Implementation Status (as of 2026-05-19)

**Fully implemented:**
- `storage/database.py` — `Database` (aiomysql pool) + `ensure_schema()` (creates all nb_* and fc_* tables with inline migrations)
- `storage/material_repo.py` — Full CRUD: `create`, `get`, `list_by_tenant`, `update_status`, `update`, `delete`, `increment_usage`
- `storage/creative_repo.py` — Full CRUD: `create`, `get`, `list_by_tenant`, `update_status`, `update_label`, `update_qianchuan_ids`
- `storage/oss_client.py` — `OSSClient` wrapping Volcengine TOS SDK: presigned PUT/GET URLs, upload, delete, public URL
- `storage/session_store.py` — Full `SessionStore`: TTL eviction, cold-start from DB, hot-swap profile, `maybe_compress`, `save_turn`
- `api/container.py` — Full `AppContainer` dataclass + `build_container()` wiring all dependencies and starting workers
- `runtime/executors.py` → `make_material_process_executor()` — video: FFmpeg → 16kHz WAV → ByteDance ASR WebSocket; audio/image: immediately READY; description via Gemini analyze_video()
- `runtime/executors.py` → `make_scene_decompose_executor()` — Gemini visual segmentation + PySceneDetect physical cuts, aligned和写入 `scene_data`，完成后将 ref_video 状态置为 `AWAITING_CLASSIFICATION`（不直接落子片段）
- `runtime/executors.py` → `make_clip_create_executor()` — 用户分类确认后批量切片入 `fc_material`（FFmpeg 切片 + OSS 上传 + 向量索引）
- `runtime/executors.py` → `make_vector_repair_executor()` — 扫描 `vector_indexed=0` 的素材，补建 Qdrant 向量
- `storage/task_repo.py` — `RuntimeTaskRepository`：后台任务进度持久化与查询
- `services/material_matcher.py` / `services/zip_parser.py` — 素材匹配与 zip 目录结构解析
- `api/routes/reference_videos.py` — 爆款视频上传/拆镜触发/分类确认（`POST /{ref_video_id}/classify`）等
- `api/routes/sessions.py`、`api/routes/creatives.py`、`api/routes/qianchuan.py` — 会话、成片、千川账号路由
- `tools/check_task_status.py` → `execute()` — 通过 `RuntimeTaskRepository` 查询任务状态
- `services/gemini_video.py` — `analyze_video()`: Gemini Files API + gemini-3.1-flash-lite-preview → semantic segment list
- `services/scene_align.py` — `detect_scene_cuts()` + `align_timestamps()`
- `services/embedding.py` — `OllamaEmbeddingService`: wraps local Ollama bge-m3 for 1024-dim Chinese embeddings
- `storage/vector_store.py` — `VectorStore`: Qdrant wrapper with dual named vectors (desc_vec + transcript_vec) and max-fusion search
- `storage/reference_video_repo.py` — `ReferenceVideoRepo`: CRUD for `fc_reference_video` table
- `storage/session_repo.py` — `SessionRepo`: session persistence in DB
- `tools/decompose_video.py` → `prepare_task()` — validates READY status, submits scene_decompose TaskEnvelope
- `tools/generate_scripts.py` → `execute()` — parallel 4-role script generation via `script_generator.generate_for_role()`
- `tools/search_materials.py` → `execute()` — dual-vector two-phase semantic material search (product-specific → generic fallback)
- `api/routes/materials.py` → `GET /materials/tree` — 产品→场景角色两级树，含数量聚合；包含 PROCESSING + READY 素材，排除 FAILED（替换原 `/tree-summary`）
- `api/routes/materials.py` → `POST /materials/upload-zip` + `/upload-zip/confirm` — zip 批量上传，按 `{product}/{scene_role}/` 目录结构自动归类；预览与确认两步式流程
- `api/routes/materials.py` → `_make_upload_oss_key()` + `_sanitize_path_component()` — OSS key 按产品分层 + 路径遍历防御
- `flowcut_frontend/src/stores/productTreeStore.ts` — 产品树前端状态
- `flowcut_frontend/src/components/material/MaterialSidebar.tsx` — 左侧 Ant Design Tree 产品导航
- `flowcut_frontend/src/components/material/UploadModal.tsx` + `ZipPreview.tsx` — 单文件 + zip 批量上传弹窗

**Stubbed (`raise NotImplementedError`) or placeholder:**
- `tools/compose_video.py` → `prepare_task()` — needs FFmpeg compose TaskEnvelope
- `tools/publish_to_qianchuan.py` → `prepare_task()` — needs Qianchuan TaskEnvelope
- `runtime/executors.py` → `make_video_compose_executor()` — FFmpeg concat + eval agent loop
- `runtime/executors.py` → `make_qianchuan_publish_executor()` — upload material + create campaign
- `runtime/executors.py` → `make_qianchuan_sync_executor()` — T+1 data sync
- `context/providers.py` → `TaskContextProvider` — returns `[]`; should inject active task state

### Key Flowcut design decisions
- **No journey stages** — MainAgent uses single `"default"` stage (no novice/explore/mature)
- **No subagents** — FlowCut has no SubagentStore; long-running work goes through TaskQueue only
- **Tool factories** — All 6 tools instantiated in `build_container()` via lambda factories
- **LLM** — Uses `GeminiLLM` (not VolcengineLLM as in Mojing)
- **OSS key format** — Upload routes write product-partitioned keys:
  - Single file / zip import: `materials/{tenant_key}/{product}/uploads/{ts}_{filename}`
  - Clips from decompose: `materials/{tenant_key}/{product}/clips/{ts}_{idx}.mp4`
  - Fallback product when omitted: `通用`
- **`POST /materials/upload` requires `product` Form field** — must be provided by the frontend; falls back to `通用` if empty string
- **Upload size limit** — `POST /materials/upload` and `POST /materials/import-douyin` both enforce 500 MB cap (HTTP 413 on exceed); zip uploads already had the same limit via `_MAX_ZIP_SIZE`
- See `Flowcut/DESIGN.md` for full API list and stream design

## Important Design Principles

- **Immutable data** — Never mutate existing objects; always return new copies.
- **Small files** — 200-400 lines typical, 800 max. Extract utilities from large modules.
- **Error handling** — Handle errors explicitly at every level. Never silently swallow exceptions.
- **Input validation** — Validate all user input before processing. Fail fast with clear messages.
- **No hardcoded secrets** — Use `.env` and `os.environ`. `.env` is gitignored.
- **Prefix cache** — `ContextBuilder` supports Volcengine prefix cache via `_cache_stable_prefix` / `_cache_dynamic_tail` keys in system messages.
- **Attention packets** — Injected into the message stream at specific placements (`before_last_user`, `after_history`, `tail`) with deduplication logic (`until_changed`, `periodic`, `one_turn`, `always`).
- **Image handling** — Only the most recent user image is sent as multimodal input. Historical images are replaced with `[用户已上传图片]` placeholder.
- **durable tools** — Tools that trigger long-running work (e.g., image analysis, deep research) use `execution_mode="durable"` and are queued via `RuntimeServices.submit_task()`. The ReAct loop receives an immediate ack and continues.
