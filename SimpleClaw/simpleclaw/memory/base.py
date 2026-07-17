"""Memory 抽象接口。

框架定义契约；具体实现位于应用层
（例如 Mojing/storage/memory_repo.py 对应 MySQL，或其他项目中的向量数据库）。

读路径（热路径）：
    每轮对话时，ContextBuilder 内部会调用 retrieve()，将相关的
    记忆条目注入系统提示的动态尾部。

写路径（冷路径）：
    store() 在一轮对话完成后异步调用 —— 由 PostrunHook 触发，
    不阻塞主响应流。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class MemoryItem:
    """retrieve() 返回的单条记忆条目。"""

    key: str              # 主题 / 唯一标识符
    content: str          # 完整内容文本
    description: str = "" # 用于索引区域的单行摘要
    metadata: dict = field(default_factory=dict)


class Memory(ABC):
    """所有记忆后端必须遵守的契约。"""

    @abstractmethod
    async def store(
        self,
        key: str,
        content: str,
        *,
        description: str = "",
        metadata: dict | None = None,
    ) -> None:
        """写入或更新一条记忆条目。

        若相同 key 的条目已存在，则覆盖。
        """
        ...

    @abstractmethod
    async def retrieve(self, query: str = "", top_k: int = 20) -> list[MemoryItem]:
        """返回与给定查询最相关的记忆条目列表。

        若 query 为空，则按最近访问顺序（LRU）返回最新的条目。
        """
        ...

    async def as_section(self, query: str = "", top_k: int = 20) -> str:
        """将记忆条目渲染为 Markdown 区块，以便注入系统提示。

        若无任何条目则返回空字符串。
        默认格式：
            # Memory

            - topic_a — 单行描述
            - topic_b — 单行描述
        """
        items = await self.retrieve(query=query, top_k=top_k)
        if not items:
            return ""
        lines = [
            f"- {item.key} — {item.description or item.content[:80]}"
            for item in items
        ]
        return "# Memory\n\n" + "\n".join(lines)
