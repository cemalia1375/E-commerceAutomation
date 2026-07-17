#!/usr/bin/env bash
# 通用宿主机启动脚本（替代 dev.sh 的硬编码路径）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${1:-8000}"

cd "$SCRIPT_DIR"

# 自动查找可用的 Python 解释器
PYTHON="${PYTHON:-$(command -v python3 || command -v python || true)}"
if [ -z "$PYTHON" ]; then
    echo "错误：系统未找到 python3/python，请先安装 Python 3.11+" >&2
    exit 1
fi

echo "[start] 使用 Python: $PYTHON"
echo "[start] 启动 Mojing API Server  port=$PORT"
echo "[start] Admin 面板: http://localhost:$PORT/admin/editor"

exec "$PYTHON" -m uvicorn Mojing.api.server:app \
    --host 0.0.0.0 \
    --port "$PORT" \
    --log-level info
