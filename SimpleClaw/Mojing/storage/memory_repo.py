"""MySQLMemory — 以 nb_memory_entries 为后端的 Memory 实现。

读路径：retrieve() → 按 last_referenced_at DESC 排序的 SELECT（LRU）
写路径：store()    → INSERT ... ON DUPLICATE KEY UPDATE
"""

from __future__ import annotations

import json
from datetime import datetime

from simpleclaw.memory.base import Memory, MemoryItem
from Mojing.storage.database import Database


class MySQLMemory(Memory):
    """从 nb_memory_entries 读取的按租户隔离的 Memory 实现。"""

    def __init__(self, db: Database, tenant_key: str, source: str = "main") -> None:
        self._db = db
        self._tenant_key = tenant_key
        self._source = source

    # ------------------------------------------------------------------
    # 写入（冷路径）
    # ------------------------------------------------------------------

    async def store(
        self,
        key: str,
        content: str,
        *,
        description: str = "",
        metadata: dict | None = None,
        memory_type: str = "chitchat",
    ) -> None:
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        token_count = len(content.split())  # 粗略估算
        memory_type = str(memory_type or "chitchat").strip() or "chitchat"

        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO nb_memory_entries
                        (tenant_key, source, topic, description, content, memory_type, token_count, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        description  = VALUES(description),
                        content      = VALUES(content),
                        memory_type  = VALUES(memory_type),
                        token_count  = VALUES(token_count),
                        updated_at   = VALUES(updated_at)
                    """,
                    (
                        self._tenant_key, self._source, key,
                        description, content, memory_type, token_count, now, now,
                    ),
                )

    # ------------------------------------------------------------------
    # 读取（热路径）
    # ------------------------------------------------------------------

    async def retrieve(self, query: str = "", top_k: int = 20) -> list[MemoryItem]:
        """按 LRU 顺序返回记忆条目（最近引用的排在最前）。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT topic, description, content
                    FROM nb_memory_entries
                    WHERE tenant_key = %s AND source = %s
                    ORDER BY
                        (memory_type = 'skin') DESC,
                        COALESCE(last_referenced_at, created_at) DESC,
                        created_at DESC
                    LIMIT %s
                    """,
                    (self._tenant_key, self._source, top_k),
                )
                rows = await cur.fetchall()

        if not rows:
            return []

        items = [
            MemoryItem(
                key=row[0],
                content=row[2],
                description=row[1] or "",
            )
            for row in rows
        ]

        # 更新已检索条目的 last_referenced_at（LRU 刷新）
        keys = [row[0] for row in rows]
        await self._touch(keys)

        return items

    async def delete(self, key: str) -> None:
        """删除指定 topic 的记忆条目。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    DELETE FROM nb_memory_entries
                    WHERE tenant_key = %s AND source = %s AND topic = %s
                    """,
                    (self._tenant_key, self._source, key),
                )

    async def _touch(self, keys: list[str]) -> None:
        if not keys:
            return
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        placeholders = ",".join(["%s"] * len(keys))
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    UPDATE nb_memory_entries
                    SET last_referenced_at = %s
                    WHERE tenant_key = %s AND source = %s AND topic IN ({placeholders})
                    """,
                    [now, self._tenant_key, self._source, *keys],
                )
