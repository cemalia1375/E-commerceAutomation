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
Copy `.env.example` to `.env` and fill in:
- `VOLCENGINE_API_KEY`, `VOLCENGINE_API_BASE`, `VOLCENGINE_MODEL`
- `MYSQL_HOST`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DB`
- `REDIS_URL` (optional — falls back to in-memory queue if empty)

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

`Flowcut/` is a 抖音千川 content production tool. The storage + infrastructure layer is fully implemented; business logic for Gemini decomposition, FFmpeg compose, and Qianchuan publishing remains stubbed with `raise NotImplementedError`.

### Directory Structure
```
Flowcut/
  agent/          # MainAgent, FirstTokenAgent, PostprocessHook, ColdPathHook
  api/            # FastAPI routes, server, AppContainer (build_container)
  context/        # TaskContextProvider (returns [] — stub until task state is wired)
  tools/          # 6 Tool classes (decompose_video, generate_scripts, search_materials,
                  #   compose_video, check_task_status, publish_to_qianchuan)
  storage/        # MaterialRepo, CreativeRepo, ScriptRepo, QianchuanRepo + shared repos
  runtime/        # FlowcutTaskStream (5 streams), executors (partial), make_workers()
  services/       # douyin_client.py (Qianchuan HTTP client stub)
  workspace/      # Agent.md, SOUL.md, TOOL.md, compliance.md
  config.py       # FLOWCUT_* env vars + OSS config
```

### Start Flowcut dev server
```bash
cd SimpleClaw
uv run python -m uvicorn Flowcut.api.server:app --reload --port 8001
```

### DB Tables (MySQL, created via ensure_schema on startup)
- `fc_material` — 素材主表 (status: PROCESSING → READY / FAILED)
- `fc_script` — 脚本表 (segments_json: JSON array)
- `fc_creative` — 成片表 (status: PENDING → COMPOSING → READY / FAILED; label: NORMAL / HOT / DEAD)
- `fc_material_usage` — 素材↔成片多对多
- `fc_qianchuan_account` — 千川账号 + OAuth token 存储

### Implementation Status (as of 2026-05-15)

**Fully implemented:**
- `storage/database.py` — `Database` (aiomysql pool) + `ensure_schema()` (creates all nb_* and fc_* tables with inline migrations)
- `storage/material_repo.py` — Full CRUD: `create`, `get`, `list_by_tenant`, `update_status`, `update`, `delete`, `increment_usage`
- `storage/creative_repo.py` — Full CRUD: `create`, `get`, `list_by_tenant`, `update_status`, `update_label`, `update_qianchuan_ids`
- `storage/oss_client.py` — `OSSClient` wrapping Volcengine TOS SDK: presigned PUT/GET URLs, upload, delete, public URL
- `storage/session_store.py` — Full `SessionStore`: TTL eviction, cold-start from DB, hot-swap profile, `maybe_compress`, `save_turn`
- `api/container.py` — Full `AppContainer` dataclass + `build_container()` wiring all dependencies and starting workers
- `runtime/executors.py` → `make_material_process_executor()` — complete ASR pipeline:
  - Downloads video from OSS presigned URL via aiohttp
  - Extracts 16kHz mono WAV audio via FFmpeg (subprocess)
  - Extracts cover frame at 0.5s via FFmpeg, uploads cover to OSS
  - Transcribes audio via ByteDance Streaming ASR WebSocket (`wss://openspeech.bytedance.com/api/v3/sauc/bigmodel`)
  - Writes `transcript` + `thumbnail_url` + status=READY/FAILED back to `fc_material`
  - Audio/image materials: immediately marked READY (no ASR)

**Stubbed (`raise NotImplementedError`):**
- `tools/decompose_video.py` → `prepare_task()` — needs Gemini scene decomposition TaskEnvelope
- `tools/generate_scripts.py` → `execute()` — needs LLM call to generate differentiated scripts
- `tools/search_materials.py` → `execute()` — needs DB search returning 3-tier candidates
- `tools/compose_video.py` → `prepare_task()` — needs FFmpeg compose TaskEnvelope
- `tools/check_task_status.py` → `execute()` — needs task_repo query
- `tools/publish_to_qianchuan.py` → `prepare_task()` — needs Qianchuan TaskEnvelope
- `runtime/executors.py` → `make_scene_decompose_executor()` — Gemini visual decomposition
- `runtime/executors.py` → `make_video_compose_executor()` — FFmpeg concat + eval agent loop
- `runtime/executors.py` → `make_qianchuan_publish_executor()` — upload material + create campaign
- `runtime/executors.py` → `make_qianchuan_sync_executor()` — T+1 data sync
- `context/providers.py` → `TaskContextProvider` — returns `[]`; should inject active task state

### Key Flowcut design decisions
- **No journey stages** — MainAgent uses single `"default"` stage (no novice/explore/mature)
- **No subagents** — FlowCut has no SubagentStore; long-running work goes through TaskQueue only
- **Tool factories** — All 6 tools instantiated in `build_container()` via lambda factories
- **LLM** — Uses `GeminiLLM` (not VolcengineLLM as in Mojing)
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
