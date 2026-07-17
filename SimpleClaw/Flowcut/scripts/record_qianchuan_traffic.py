"""Phase 0：在长驻 Chromium 上录制所有千川后台 XHR 流量。

用法（终端 1，保持 Chromium 跑着）：
  bash Flowcut/scripts/start-chrome-qianchuan.sh

用法（终端 2，启动录制）：
  uv run python Flowcut/scripts/record_qianchuan_traffic.py

录制时你在 Chromium 窗口里手动操作：
  1. 顶部菜单 → 数据 / 报表 / 创意分析（哪个能看到"按视频/创意维度的消耗数据"）
  2. 选最近 7 天 / 切到不同日期
  3. 翻 2-3 页（让分页接口暴露）
  4. 切换"按计划 / 按创意 / 按账户"维度（如果有）
  5. 点击"导出"按钮（即使我们不用 CSV，也想看导出接口长啥样）

录够 5-10 分钟后回到终端按 Ctrl+C。

输出：
  dev/qc_traffic/qc_traffic_<timestamp>.jsonl
  每行一个 JSON：{"ts", "method", "url", "status", "req_body", "resp_body"}
  自动过滤：只记录 qianchuan.jinritemai.com / *.bytedance / oceanengine 域名下、
           且响应是 JSON 的接口（噪声资源不录）。
"""
from __future__ import annotations

import asyncio
import json
import re
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.async_api import (
    BrowserContext,
    Request,
    Response,
    async_playwright,
)

CDP_URL = "http://127.0.0.1:9222"

# 我们关心的域名 —— 千川后台前端会调这些
INTERESTING_DOMAINS = re.compile(
    r"(qianchuan\.jinritemai\.com"
    r"|ad\.oceanengine\.com"
    r"|api\.oceanengine\.com"
    r"|ad-data\.oceanengine\.com"
    r"|advertiser\.oceanengine\.com"
    r"|tnc-data\.snssdk\.com)",
    re.IGNORECASE,
)

# 明确排除：埋点、监控、静态资源 —— 噪声大且无用
EXCLUDED_PATH = re.compile(
    r"(\.(js|css|woff2?|png|jpg|jpeg|gif|svg|ico|mp4|webp)$"
    r"|/log/|/track|/monitor|/applog|/sentry|/__webpack)",
    re.IGNORECASE,
)


class Recorder:
    def __init__(self, out_file: Path):
        self.out_file = out_file
        self.fp = out_file.open("a", encoding="utf-8", buffering=1)
        self.count = 0
        self.skipped = 0
        # 暂存 request body：response 事件触发时再合并
        self._req_bodies: dict[str, str] = {}

    def _interesting(self, url: str) -> bool:
        if not INTERESTING_DOMAINS.search(url):
            return False
        if EXCLUDED_PATH.search(url):
            return False
        return True

    async def on_request(self, request: Request) -> None:
        if not self._interesting(request.url):
            return
        body = request.post_data
        if body:
            self._req_bodies[request.url + "::" + request.method] = body

    async def on_response(self, response: Response) -> None:
        url = response.url
        if not self._interesting(url):
            self.skipped += 1
            return

        ctype = (response.headers.get("content-type") or "").lower()
        is_json = "application/json" in ctype or "text/json" in ctype

        # 非 JSON 也记录元数据，方便后续看到底有哪些类型
        resp_body = None
        if is_json:
            try:
                resp_body = await response.json()
            except Exception:
                try:
                    resp_body = await response.text()
                except Exception:
                    resp_body = None

        req = response.request
        req_body_str = self._req_bodies.pop(url + "::" + req.method, None)
        req_body = None
        if req_body_str:
            try:
                req_body = json.loads(req_body_str)
            except Exception:
                req_body = req_body_str

        record = {
            "ts": time.time(),
            "method": req.method,
            "url": url,
            "status": response.status,
            "content_type": ctype,
            "req_query": dict(req.url.split("?", 1)[1:1] and []),  # 占位
            "req_body": req_body,
            "resp_body": resp_body,
        }
        self.fp.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self.count += 1
        if self.count % 10 == 0:
            print(f"  …已记录 {self.count} 条接口")

    def close(self) -> None:
        self.fp.close()


def attach_to_all_pages(ctx: BrowserContext, recorder: Recorder) -> None:
    """已有 page 和未来新开 page 都监听。"""
    for page in ctx.pages:
        page.on("request", recorder.on_request)
        page.on("response", recorder.on_response)

    def on_new_page(page):
        page.on("request", recorder.on_request)
        page.on("response", recorder.on_response)

    ctx.on("page", on_new_page)


async def main() -> None:
    repo_root = Path(__file__).resolve().parents[3]  # SimpleClaw/
    out_dir = repo_root / "dev" / "qc_traffic"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = out_dir / f"qc_traffic_{ts}.jsonl"

    print(f"录制输出 → {out_file}")
    print(f"连接 CDP → {CDP_URL}")

    recorder = Recorder(out_file)

    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
        attach_to_all_pages(ctx, recorder)

        print()
        print("=" * 60)
        print("✓ 已开始录制。请到 Chromium 窗口里手动操作：")
        print("  1. 顶部菜单 → 数据 / 报表 / 创意分析")
        print("  2. 选时间范围（最近 7 天、最近 30 天）")
        print("  3. 翻 2-3 页报表")
        print("  4. 切换不同维度（按视频 / 按计划 / 按账户）")
        print("  5. 点'导出'按钮看看导出接口")
        print()
        print("完成后回到此终端按 Ctrl+C 停止录制。")
        print("=" * 60)
        print()

        # 等 Ctrl+C
        stop_event = asyncio.Event()

        def handle_sigint(*_):
            stop_event.set()

        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, handle_sigint)

        try:
            await stop_event.wait()
        except KeyboardInterrupt:
            pass

        print()
        print(f"录制结束。总共记录 {recorder.count} 条接口，跳过 {recorder.skipped} 条无关请求。")
        recorder.close()
        print(f"文件: {out_file}")
        print()
        print("下一步：把这个文件路径发给我，我做接口分析。")

        await browser.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
