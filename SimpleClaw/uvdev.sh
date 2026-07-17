#!/usr/bin/env bash
# FlowCut 本地开发全栈启动脚本
#
# 用法:
#   ./uvdev.sh                  启动 FlowCut 全栈（默认）
#   ./uvdev.sh flowcut [port]   启动 FlowCut 全栈，可指定后端端口（默认 8001）
#   ./uvdev.sh mojing  [port]   仅启动 Mojing API Server（兼容原行为，默认 8000）
#
# FlowCut 模式会依次准备/检查：
#   1. Embedding provider（Ollama 时检查 localhost:11434；API provider 时跳过本地模型）
#   2. Qdrant（docker 容器，6333/6334 端口）
#   3. FlowCut 后端（uvicorn，默认 8001）
#   4. 前端（flowcut_frontend，npm run dev）

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
FRONTEND_DIR="$REPO_ROOT/flowcut_frontend"
LOG_DIR="$SCRIPT_DIR/logs"

MODE="${1:-flowcut}"
PORT="${2:-}"

mkdir -p "$LOG_DIR"

if [[ -f "$SCRIPT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/.env"
  set +a
fi

# 本地依赖（Qdrant / 后端 / 前端）必须直连，避免被 ALL_PROXY/HTTP_PROXY
# 劫持到代理里。httpx/qdrant-client 在 SOCKS 代理下还会要求 socksio。
export NO_PROXY="${NO_PROXY:-localhost,127.0.0.1,::1,0.0.0.0}"
export no_proxy="${no_proxy:-$NO_PROXY}"

# ---------- 通用工具 ----------

log()  { printf '\033[36m[dev]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[dev]\033[0m %s\n' "$*"; }
err()  { printf '\033[31m[dev]\033[0m %s\n' "$*" >&2; }

# 给后台进程日志加上彩色前缀
prefix_stream() {
  local tag="$1"
  local color="$2"
  awk -v tag="$tag" -v color="$color" '{
    printf "\033[%sm[%s]\033[0m %s\n", color, tag, $0
    fflush()
  }'
}

# ---------- 仅 Mojing 模式 ----------

if [[ "$MODE" == "mojing" ]]; then
  PORT="${PORT:-8000}"
  cd "$SCRIPT_DIR"
  log "启动 Mojing API Server  port=$PORT"
  log "Admin 面板: http://localhost:$PORT/admin/editor"
  exec uv run python -m uvicorn Mojing.api.server:app \
    --host 0.0.0.0 \
    --port "$PORT" \
    --reload \
    --reload-dir . \
    --log-level info
fi

# ---------- FlowCut 全栈模式 ----------

if [[ "$MODE" != "flowcut" ]]; then
  err "未知模式: $MODE （可选: flowcut | mojing）"
  exit 1
fi

BACKEND_PORT="${PORT:-8001}"

PIDS=()
QDRANT_STARTED_BY_US=0

cleanup() {
  local exit_code=$?
  echo
  log "收到退出信号，清理子进程..."
  for pid in "${PIDS[@]:-}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  # 等待子进程退出
  for pid in "${PIDS[@]:-}"; do
    wait "$pid" 2>/dev/null || true
  done
  if [[ "$QDRANT_STARTED_BY_US" == "1" ]]; then
    log "停止 Qdrant 容器..."
    docker stop flowcut-qdrant >/dev/null 2>&1 || true
  fi
  log "全部停止"
  exit "$exit_code"
}
trap cleanup INT TERM EXIT

wait_for_any_child() {
  while true; do
    for pid in "${PIDS[@]:-}"; do
      if ! kill -0 "$pid" 2>/dev/null; then
        wait "$pid" 2>/dev/null || true
        return
      fi
    done
    sleep 1
  done
}

# 1) Embedding provider 检查
embedding_provider_raw="${FLOWCUT_EMBEDDING_PROVIDER-}"
if [[ -z "$embedding_provider_raw" ]]; then
  embedding_provider_raw="${EMBEDDING_PROVIDER-ollama}"
fi
embedding_provider="$(printf '%s' "$embedding_provider_raw" | tr '[:upper:]' '[:lower:]')"
if [[ "$embedding_provider" == "ollama" ]]; then
  log "检查 Ollama (localhost:11434) ..."
  if ! curl -sf -m 2 http://localhost:11434/api/tags >/dev/null; then
    err "Ollama 未运行。请先启动 Ollama.app，或执行: brew services start ollama"
    exit 1
  fi

  if ! curl -sf http://localhost:11434/api/tags | grep -q '"name":"bge-m3'; then
    warn "未检测到 bge-m3 模型，开始拉取（首次需要数分钟，~1.2GB）..."
    ollama pull bge-m3
  fi
  log "Ollama OK (bge-m3 已就绪)"
else
  log "Embedding provider=${embedding_provider:-ollama}，跳过 Ollama 检查"
fi

# 2) Qdrant 检查/启动
log "检查 Qdrant (localhost:6333) ..."
if curl -sf -m 2 http://localhost:6333/readyz >/dev/null 2>&1; then
  log "Qdrant 已在运行，跳过启动"
else
  if ! command -v docker >/dev/null 2>&1; then
    err "未安装 docker，无法启动 Qdrant"
    exit 1
  fi
  docker_container_names="$(docker ps -a --format '{{.Names}}' 2>/dev/null)" || {
    err "Docker 不可访问。请确认 Docker Desktop 已启动，并且当前终端有权限访问 Docker。"
    exit 1
  }
  if printf '%s\n' "$docker_container_names" | grep -q '^flowcut-qdrant$'; then
    log "复用已存在的 Qdrant 容器 flowcut-qdrant"
    docker start flowcut-qdrant >/dev/null
  else
    log "创建并启动新的 Qdrant 容器 flowcut-qdrant"
    docker run -d \
      --name flowcut-qdrant \
      -p 6333:6333 -p 6334:6334 \
      -v "$SCRIPT_DIR/qdrant_storage:/qdrant/storage:z" \
      qdrant/qdrant >/dev/null
  fi
  QDRANT_STARTED_BY_US=1
  # 等就绪
  for i in {1..30}; do
    if curl -sf -m 1 http://localhost:6333/readyz >/dev/null 2>&1; then
      break
    fi
    sleep 1
    if [[ "$i" == "30" ]]; then
      err "Qdrant 启动超时"
      exit 1
    fi
  done
  log "Qdrant 已就绪"
fi

# 3) 启动 FlowCut 后端
cd "$SCRIPT_DIR"
if command -v uv >/dev/null 2>&1; then
  BACKEND_RUNNER=(uv run python)
elif [[ -x "$SCRIPT_DIR/.venv/bin/python" ]]; then
  BACKEND_RUNNER=("$SCRIPT_DIR/.venv/bin/python")
else
  BACKEND_RUNNER=(python3)
fi
if ! "${BACKEND_RUNNER[@]}" -c "import aiohttp, fastapi, openai, qdrant_client, uvicorn" >/dev/null 2>&1; then
  err "Python 依赖不完整。请先执行: ${BACKEND_RUNNER[*]} -m pip install -r \"$SCRIPT_DIR/requirements.txt\""
  exit 1
fi
log "启动 FlowCut 后端  port=$BACKEND_PORT  日志=$LOG_DIR/backend.log"
(
  "${BACKEND_RUNNER[@]}" -m uvicorn Flowcut.api.server:app \
    --host 0.0.0.0 \
    --port "$BACKEND_PORT" \
    --reload \
    --reload-dir . \
    --log-level info 2>&1 \
  | tee "$LOG_DIR/backend.log" \
  | prefix_stream "backend" "32"
) &
PIDS+=($!)

# 4) 启动前端
if [[ ! -d "$FRONTEND_DIR" ]]; then
  err "前端目录不存在: $FRONTEND_DIR"
  exit 1
fi
if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
  warn "前端 node_modules 不存在，先执行 npm install ..."
  (cd "$FRONTEND_DIR" && npm install)
fi

log "启动前端  目录=$FRONTEND_DIR  日志=$LOG_DIR/frontend.log"
(
  cd "$FRONTEND_DIR"
  npm run dev 2>&1 \
  | tee "$LOG_DIR/frontend.log" \
  | prefix_stream "frontend" "35"
) &
PIDS+=($!)

echo
log "全部启动完成。Ctrl+C 退出。"
log "  后端:   http://localhost:$BACKEND_PORT"
log "  前端:   见上方 Vite 输出（一般 http://localhost:5173）"
log "  Qdrant: http://localhost:6333/dashboard"
echo

# 任意一个子进程退出则触发 cleanup
wait_for_any_child
