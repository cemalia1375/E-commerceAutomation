"""
NetworkRecorder: 监听 Playwright 的 response 事件，按 URL 正则过滤并收集 JSON 响应。

设计：
  * 注册到一个 Page 上，进入"录制"状态后所有匹配 URL 的响应被异步入栈。
  * collect() 会阻塞直到收到至少一条匹配（或超时），适合"goto 页面 + 等 XHR"的模式。
  * 也可以连续多次 fetch（翻页/切日期）后 dump_all() 一把拿全。

典型用法：
    async with BrowserClient(cdp_url) as client:
        recorder = NetworkRecorder(client.page, pattern=r"qianchuan\\.jinritemai\\.com/.*/report")
        await recorder.start()
        await client.navigate(REPORT_URL)
        rows = await recorder.collect(min_count=1, timeout=30)
"""
from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, Any, Optional, Union

if TYPE_CHECKING:
    from playwright.async_api import Page, Response


class NetworkRecorder:
    """监听一个 Page 的 response 事件，按 URL pattern 收集 JSON。"""

    def __init__(self, page: Page, pattern: Union[str, re.Pattern]):
        self._page = page
        self._pattern = pattern if isinstance(pattern, re.Pattern) else re.compile(pattern)
        self._captures: list[dict[str, Any]] = []
        self._started = False
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._started:
            return
        self._page.on("response", self._on_response)
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return
        self._page.remove_listener("response", self._on_response)
        self._started = False

    def _on_response(self, response: Response) -> None:
        # 同步 callback；真正读 body 走 asyncio.create_task
        if not self._pattern.search(response.url):
            return
        asyncio.create_task(self._capture(response))

    async def _capture(self, response: Response) -> None:
        try:
            # 只接受 JSON
            ctype = (response.headers.get("content-type") or "").lower()
            if "application/json" not in ctype:
                return
            body = await response.json()
        except Exception:
            # 响应可能已被释放 / 不是合法 JSON → 跳过
            return
        async with self._lock:
            self._captures.append(
                {
                    "url": response.url,
                    "status": response.status,
                    "body": body,
                }
            )

    async def collect(
        self, *, min_count: int = 1, timeout: float = 30.0, poll_interval: float = 0.2
    ) -> list[dict[str, Any]]:
        """等待至少 min_count 条匹配响应，返回当前所有已捕获项。超时返回已有。"""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            async with self._lock:
                if len(self._captures) >= min_count:
                    return list(self._captures)
            await asyncio.sleep(poll_interval)
        async with self._lock:
            return list(self._captures)

    async def dump_all(self) -> list[dict[str, Any]]:
        """立即返回当前所有已捕获项，不等待。"""
        async with self._lock:
            return list(self._captures)

    async def clear(self) -> None:
        async with self._lock:
            self._captures.clear()
