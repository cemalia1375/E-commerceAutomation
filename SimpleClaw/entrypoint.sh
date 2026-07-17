#!/bin/bash
set -euo pipefail

# 若未配置外部 REDIS_URL，则在容器内后台启动 Redis
if [ -z "${REDIS_URL:-}" ]; then
    echo "[entrypoint] 未检测到 REDIS_URL，启动内置 Redis..."
    redis-server --daemonize yes --bind 127.0.0.1
fi

# 生产环境启动 Uvicorn（ workers 建议按 CPU 核数调整，示例用 1 方便日志查看 ）
exec uvicorn Mojing.api.server:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers "${UVICORN_WORKERS:-1}" \
    --log-level "${LOG_LEVEL:-info}"
