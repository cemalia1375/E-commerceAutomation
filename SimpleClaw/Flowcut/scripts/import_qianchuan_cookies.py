"""一次性脚本：把 Cookie-Editor 导出的 JSON cookie 导入到长驻 Chromium。

前置：
  bash Flowcut/scripts/start-chrome-qianchuan.sh   # Chromium 跑在 CDP 9222
  COOKIE_FILE=/path/to/qianchuan.jinritemai.com_json_xxx.json

用法：
  uv run python Flowcut/scripts/import_qianchuan_cookies.py \\
      /Users/shengxingou-1/Downloads/qianchuan.jinritemai.com_json_1779959299195.json

成功后：
  - 当前 Chromium 注入 cookie
  - 自动 navigate 到千川首页验证登录态
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

CDP_URL = "http://127.0.0.1:9222"
QIANCHUAN_HOME = "https://qianchuan.jinritemai.com/"


# Cookie-Editor 的 sameSite 值 → Playwright 期望值
SAMESITE_MAP = {
    "no_restriction": "None",
    "unspecified": "Lax",
    "lax": "Lax",
    "strict": "Strict",
    "none": "None",
}


def convert(raw: dict) -> dict:
    """把 Cookie-Editor 格式转成 Playwright 格式。"""
    out = {
        "name": raw["name"],
        "value": raw["value"],
        "domain": raw["domain"],
        "path": raw.get("path", "/"),
        "secure": bool(raw.get("secure", False)),
        "httpOnly": bool(raw.get("httpOnly", False)),
        "sameSite": SAMESITE_MAP.get(
            (raw.get("sameSite") or "lax").lower(), "Lax"
        ),
    }
    # 过期时间：Playwright 用 expires（秒，float），-1 表示 session cookie
    exp = raw.get("expirationDate")
    if exp is not None and not raw.get("session", False):
        out["expires"] = float(exp)
    return out


async def main(cookie_file: Path) -> None:
    raw_cookies = json.loads(cookie_file.read_text(encoding="utf-8"))
    if not isinstance(raw_cookies, list):
        raise SystemExit("cookie 文件必须是 JSON 数组（Cookie-Editor 默认格式）")

    converted = [convert(c) for c in raw_cookies]
    print(f"读取 {len(converted)} 条 cookie，准备注入到 {CDP_URL}")

    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(CDP_URL)
        ctx = browser.contexts[0] if browser.contexts else await browser.new_context()

        # 先清掉同域名旧 cookie（避免脏数据）
        existing = await ctx.cookies()
        targets = {".jinritemai.com", "qianchuan.jinritemai.com", "jinritemai.com"}
        before = sum(1 for c in existing if c.get("domain") in targets)
        if before:
            await ctx.clear_cookies()
            print(f"清除了 {before} 条同域名旧 cookie")

        await ctx.add_cookies(converted)
        print(f"✓ 已注入 {len(converted)} 条 cookie")

        # 用现有 page，没有则开一个，跳到千川首页验证
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.bring_to_front()
        print(f"导航到 {QIANCHUAN_HOME} 验证登录态...")
        await page.goto(QIANCHUAN_HOME, wait_until="domcontentloaded")
        await page.wait_for_load_state("networkidle", timeout=15000)

        final_url = page.url
        title = await page.title()
        print(f"  最终 URL:  {final_url}")
        print(f"  页面标题:  {title}")

        if "login" in final_url.lower() or "passport" in final_url.lower():
            print("⚠ 看起来被重定向到登录页，cookie 可能不完整或已过期")
        else:
            print("✓ 看起来登录态生效（未跳登录页）")

        await browser.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(
            "用法: uv run python Flowcut/scripts/import_qianchuan_cookies.py <cookie.json>"
        )
    asyncio.run(main(Path(sys.argv[1])))
