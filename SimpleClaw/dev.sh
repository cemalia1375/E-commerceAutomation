#!/usr/bin/env bash
# 开发模式启动 Mojing API Server
# 用法: ./dev.sh [port]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${1:-8000}"

cd "$SCRIPT_DIR"

echo "[dev] 启动 Mojing API Server  port=$PORT"
echo "[dev] Admin 面板: http://localhost:$PORT/admin/editor"

/Users/raofenghao/anaconda3/envs/mojingclaw/bin/python -m uvicorn Mojing.api.server:app \
  --host 0.0.0.0 \
  --port "$PORT" \
  --reload \
  --reload-dir . \
  --log-level info
