#!/bin/bash
# 启动一个长驻 Chromium 实例，专用于千川后台自动化。
#
# 设计：
#   * 优先使用 Playwright 自带 chromium —— Mac/Linux 二进制完全一致，
#     行为指纹稳定（避免本地 Chrome 和服务器 Chrome 版本/codecs 差异引发的风控漂移）。
#   * attach-only：Flowcut 后端通过 CDP (port 9222) 连接，不启动新进程。
#   * user-data-dir 独立：~/.flowcut/chrome-qianchuan/，扫码登录态持久化于此。
#   * macOS 本地开发：无需 Xvfb/VNC。
#   * Linux 服务器（未来上云）：自动检测无 DISPLAY 时启 Xvfb。
#
# 前置：
#   uv pip install playwright
#   uv run playwright install chromium
#
# 用法：
#   bash SimpleClaw/Flowcut/scripts/start-chrome-qianchuan.sh
#
# Env 覆盖：
#   CDP_PORT       默认 9222
#   USER_DATA_DIR  默认 ~/.flowcut/chrome-qianchuan
#   CHROME_BIN     若指定则跳过自动探测

set -e

CDP_PORT="${CDP_PORT:-9222}"
USER_DATA_DIR="${USER_DATA_DIR:-$HOME/.flowcut/chrome-qianchuan}"
CHROME_BIN="${CHROME_BIN:-}"

mkdir -p "$USER_DATA_DIR"

# ---- 探测 Chromium 二进制（优先 Playwright） ---------------------------------
if [ -z "$CHROME_BIN" ]; then
    if [ "$(uname)" = "Darwin" ]; then
        PW_CACHE="$HOME/Library/Caches/ms-playwright"
    else
        PW_CACHE="$HOME/.cache/ms-playwright"
    fi

    # 找最新版本的 chromium-XXXXXX 目录
    PW_CHROMIUM_DIR=$(ls -d "$PW_CACHE"/chromium-* 2>/dev/null | sort -V | tail -1 || true)

    if [ -n "$PW_CHROMIUM_DIR" ]; then
        if [ "$(uname)" = "Darwin" ]; then
            CANDIDATE="$PW_CHROMIUM_DIR/chrome-mac/Chromium.app/Contents/MacOS/Chromium"
        else
            CANDIDATE="$PW_CHROMIUM_DIR/chrome-linux/chrome"
        fi
        if [ -x "$CANDIDATE" ]; then
            CHROME_BIN="$CANDIDATE"
            echo "✓ 使用 Playwright chromium: $CHROME_BIN"
        fi
    fi

    # Fallback：系统 Chrome（不推荐，仅在 playwright 未装时）
    if [ -z "$CHROME_BIN" ]; then
        echo "⚠ Playwright chromium 未找到（$PW_CACHE 下没有 chromium-*）"
        echo "  建议先跑: uv run playwright install chromium"
        echo "  回退到系统 Chrome..."
        if [ -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]; then
            CHROME_BIN="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        elif command -v google-chrome >/dev/null 2>&1; then
            CHROME_BIN="$(command -v google-chrome)"
        elif command -v chromium >/dev/null 2>&1; then
            CHROME_BIN="$(command -v chromium)"
        else
            echo "ERROR: 没找到任何 Chrome/Chromium。" >&2
            exit 1
        fi
    fi
fi

cleanup() {
    echo "Stopping..."
    kill $(jobs -p) 2>/dev/null || true
    wait 2>/dev/null || true
}
trap cleanup EXIT

# Linux 无显示时启 Xvfb
NEEDS_DISPLAY="no"
if [ "$(uname)" = "Linux" ] && [ -z "$DISPLAY" ]; then
    NEEDS_DISPLAY="yes"
fi

if [ "$NEEDS_DISPLAY" = "yes" ]; then
    Xvfb :99 -screen 0 1366x768x24 -ac +extension GLX +render -noreset &
    export DISPLAY=:99
    sleep 1
    command -v fluxbox >/dev/null 2>&1 && fluxbox &
    sleep 1
fi

EXTRA_FLAGS=""
if [ "$(uname)" = "Linux" ]; then
    EXTRA_FLAGS="--no-sandbox --disable-setuid-sandbox --disable-dev-shm-usage"
fi

echo "=========================================="
echo "  Chrome CDP:  http://127.0.0.1:${CDP_PORT}"
echo "  User data:   $USER_DATA_DIR"
echo "  目标站点:    https://qianchuan.jinritemai.com/"
echo "=========================================="
echo ""
echo "首次启动后请在 Chrome 中：访问千川后台 → 扫码登录。"
echo "登录态会落到 user-data-dir，后续重启脚本无需重登。"
echo ""

"$CHROME_BIN" \
    --remote-debugging-port=${CDP_PORT} \
    --user-data-dir="$USER_DATA_DIR" \
    --no-first-run \
    --start-maximized \
    --disable-blink-features=AutomationControlled \
    --disable-features=TranslateUI \
    --disable-infobars \
    --disable-default-apps \
    $EXTRA_FLAGS \
    'https://qianchuan.jinritemai.com/' &

wait
