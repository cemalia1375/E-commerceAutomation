"""
BrowserClient: 通过 CDP attach 到长驻 Chrome 实例。

设计原则：
  * attach-only：不启动新 Chrome；scripts/start-chrome-qianchuan.sh 负责维持常驻进程，
    cookies/登录态跨任务保留。
  * 一个 BrowserClient = 一个 CDP endpoint。多账号场景用多个 CDP 端口（多 Chrome 进程）。

依赖：playwright（已加入 requirements.txt），首次安装后需执行 `playwright install chromium`。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Page


@dataclass
class SnapshotNode:
    ref: int
    role: str
    name: str
    children: list["SnapshotNode"] = field(default_factory=list)

    def render(self, indent: int = 0) -> str:
        line = f"{'  ' * indent}[{self.ref}] {self.role}"
        if self.name:
            line += f' "{self.name[:80]}"'
        out = [line]
        for c in self.children:
            out.append(c.render(indent + 1))
        return "\n".join(out)


class BrowserClient:
    """Attach over CDP, expose 常用动作 + 暴露底层 Page 给定制场景。"""

    def __init__(self, cdp_url: str = "http://127.0.0.1:9222"):
        self.cdp_url = cdp_url
        self._pw = None
        self._browser: Optional[Browser] = None
        self._ctx: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._ref_map: dict[int, Any] = {}

    async def __aenter__(self):
        from playwright.async_api import async_playwright  # 千川功能专用，懒加载避免 PyInstaller 打包 Chromium
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.connect_over_cdp(self.cdp_url)
        self._ctx = (
            self._browser.contexts[0]
            if self._browser.contexts
            else await self._browser.new_context()
        )
        self._page = self._ctx.pages[0] if self._ctx.pages else await self._ctx.new_page()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        # Detach only — 让 Chrome 继续跑。
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    # ---- 暴露底层句柄给业务层（scraper 用） -------------------------------

    @property
    def page(self) -> Page:
        return self._page

    @property
    def context(self) -> BrowserContext:
        return self._ctx

    # ---- tab / navigation ------------------------------------------------

    async def tabs(self) -> list[dict[str, str]]:
        return [
            {"index": i, "url": p.url, "title": await p.title()}
            for i, p in enumerate(self._ctx.pages)
        ]

    async def focus(self, index: int) -> None:
        self._page = self._ctx.pages[index]
        await self._page.bring_to_front()

    async def open(self, url: str) -> int:
        self._page = await self._ctx.new_page()
        await self._page.goto(url, wait_until="domcontentloaded")
        return self._ctx.pages.index(self._page)

    async def navigate(self, url: str) -> None:
        await self._page.goto(url, wait_until="domcontentloaded")

    # ---- snapshot（LLM 驱动场景才用，scraper 用不上）---------------------

    async def snapshot(self, limit: int = 200) -> str:
        self._ref_map.clear()
        tree = await self._page.accessibility.snapshot(interesting_only=True)
        counter = [0]
        root_nodes: list[SnapshotNode] = []

        async def walk(node: dict, out: list[SnapshotNode]):
            if counter[0] >= limit:
                return
            counter[0] += 1
            ref = counter[0]
            sn = SnapshotNode(
                ref=ref, role=node.get("role", ""), name=node.get("name", "") or ""
            )
            self._ref_map[ref] = node
            out.append(sn)
            for child in node.get("children", []) or []:
                await walk(child, sn.children)

        if tree:
            await walk(tree, root_nodes)
        return "\n".join(n.render() for n in root_nodes)

    async def _locator_for_ref(self, ref: int):
        node = self._ref_map.get(ref)
        if not node:
            raise ValueError(f"unknown ref {ref} (snapshot may be stale)")
        role = node.get("role")
        name = node.get("name")
        if role and name:
            return self._page.get_by_role(role, name=name, exact=True).first
        if role:
            return self._page.get_by_role(role).first
        raise ValueError(f"ref {ref} 缺 role/name，请重新 snapshot")

    # ---- 通用动作 --------------------------------------------------------

    async def click(self, ref: int, double: bool = False) -> None:
        loc = await self._locator_for_ref(ref)
        await (loc.dblclick() if double else loc.click())

    async def type(self, ref: int, text: str, submit: bool = False) -> None:
        loc = await self._locator_for_ref(ref)
        await loc.fill(text)
        if submit:
            await loc.press("Enter")

    async def press(self, key: str) -> None:
        await self._page.keyboard.press(key)

    async def screenshot(
        self, full_page: bool = False, path: Optional[str] = None
    ) -> bytes:
        return await self._page.screenshot(full_page=full_page, path=path)

    async def wait(
        self,
        *,
        selector: Optional[str] = None,
        text: Optional[str] = None,
        url: Optional[str] = None,
        timeout: int = 30000,
    ) -> None:
        if selector:
            await self._page.wait_for_selector(selector, timeout=timeout)
        elif text:
            await self._page.get_by_text(text).first.wait_for(timeout=timeout)
        elif url:
            await self._page.wait_for_url(url, timeout=timeout)
        else:
            await self._page.wait_for_load_state("networkidle", timeout=timeout)

    async def cookies(self) -> list[dict]:
        return await self._ctx.cookies()
