"""SessionMemory — 简单的进程内 Memory 实现。

使用普通 Python dict 存储条目。无持久化 —— 进程退出后数据丢失。

适用场景：
  - 单元测试（无需数据库）
  - 本地开发 / 在无基础设施的情况下对框架进行冒烟测试
  - 与持久化实现做基准对比
"""

from __future__ import annotations

from simpleclaw.memory.base import Memory, MemoryItem


class SessionMemory(Memory):
    """以 dict 为后端的纯内存 Memory 实现。在单进程异步场景下是线程安全的。"""

    def __init__(self) -> None:
        self._store: dict[str, MemoryItem] = {}

    async def store(
        self,
        key: str,
        content: str,
        *,
        description: str = "",
        metadata: dict | None = None,
    ) -> None:
        self._store[key] = MemoryItem(
            key=key,
            content=content,
            description=description,
            metadata=metadata or {},
        )

    async def retrieve(self, query: str = "", top_k: int = 20) -> list[MemoryItem]:
        """返回最多 top_k 条条目。

        若 query 非空，则对 key/description/content 做简单子串匹配过滤。
        否则返回所有条目（插入顺序，最新的在末尾 → 取尾部切片）。
        """
        items = list(self._store.values())

        if query:
            q = query.lower()
            items = [
                i for i in items
                if q in i.key.lower()
                or q in i.description.lower()
                or q in i.content.lower()
            ]

        return items[-top_k:]
