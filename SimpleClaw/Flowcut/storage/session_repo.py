"""会话仓库 — 读写 nb_sessions 与 nb_session_messages 表。

复用 nanobot 已有的表结构，不做任何 Schema 变更。

last_consolidated
-----------------
nb_sessions.last_consolidated 是定义 LLM 工作窗口的指针：
仅 seq >= last_consolidated 的消息才会送入模型。
指针之前的消息已被提取到 nb_memory_entries，不再属于活跃上下文。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from Flowcut.storage.database import Database
from simpleclaw.context.compressor import estimate_content_tokens


class SessionRepository:
    """从 MySQL 读写会话消息历史。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # 创建 & 列表
    # ------------------------------------------------------------------

    async def create_session(
        self,
        tenant_key: str,
        session_key: str,
        title: str | None = None,
    ) -> dict[str, Any]:
        """创建一条 nb_sessions 记录并返回。"""
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO nb_sessions
                        (tenant_key, session_key, session_type, title,
                         is_primary, last_consolidated, created_at, updated_at)
                    VALUES (%s, %s, 'main', %s, 0, 0, %s, %s)
                    """,
                    (tenant_key, session_key, title, now, now),
                )
        return {
            "tenant_key": tenant_key,
            "session_key": session_key,
            "title": title,
            "session_type": "main",
            "last_consolidated": 0,
            "created_at": now,
            "updated_at": now,
        }

    async def list_by_tenant(
        self,
        tenant_key: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """列出某租户下所有会话（按更新时间倒序），带消息数。

        增加 message_count 字段（nb_session_messages 的行数），前端无需
        逐会话请求即可显示各会话的消息数量。
        """
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT
                        s.tenant_key, s.session_key, s.session_type, s.title,
                        s.last_consolidated, s.created_at, s.updated_at,
                        COALESCE(m.cnt, 0) AS message_count
                    FROM nb_sessions s
                    LEFT JOIN (
                        SELECT tenant_key, session_key, COUNT(*) AS cnt
                        FROM nb_session_messages
                        GROUP BY tenant_key, session_key
                    ) m ON m.tenant_key = s.tenant_key AND m.session_key = s.session_key
                    WHERE s.tenant_key = %s
                    ORDER BY s.updated_at DESC
                    LIMIT %s OFFSET %s
                    """,
                    (tenant_key, limit, offset),
                )
                rows = await cur.fetchall()
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in rows]

    # ------------------------------------------------------------------
    # 加载
    # ------------------------------------------------------------------

    async def load_messages(
        self,
        tenant_key: str,
        session_key: str,
        offset: int = 0,
        limit: int | None = None,
    ) -> tuple[list[dict], int]:
        """返回该会话的 (messages, last_consolidated)。

        messages          — 按 seq ASC 排序的消息（OpenAI 字典格式）
        last_consolidated — 来自 nb_sessions 的工作窗口起始指针
        offset / limit    — 分页支持；limit=None 时返回全部。
        """
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                # 从会话记录中读取 consolidated 指针
                await cur.execute(
                    """
                    SELECT last_consolidated
                    FROM nb_sessions
                    WHERE tenant_key = %s AND session_key = %s
                    LIMIT 1
                    """,
                    (tenant_key, session_key),
                )
                row = await cur.fetchone()
                last_consolidated = row[0] if row else 0

                # 读取消息历史（支持分页）
                if limit is not None:
                    await cur.execute(
                        """
                        SELECT message_json
                        FROM nb_session_messages
                        WHERE tenant_key = %s AND session_key = %s
                        ORDER BY seq ASC
                        LIMIT %s OFFSET %s
                        """,
                        (tenant_key, session_key, limit, offset),
                    )
                else:
                    await cur.execute(
                        """
                        SELECT message_json
                        FROM nb_session_messages
                        WHERE tenant_key = %s AND session_key = %s
                        ORDER BY seq ASC
                        """,
                        (tenant_key, session_key),
                    )
                rows = await cur.fetchall()

        messages: list[dict] = []
        for (raw,) in rows:
            if not raw:
                continue
            try:
                msg = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(msg, dict):
                    messages.append(msg)
            except Exception:
                pass

        return messages, last_consolidated

    # ------------------------------------------------------------------
    # 保存（追加本轮新消息）
    # ------------------------------------------------------------------

    async def append_messages(
        self,
        tenant_key: str,
        session_key: str,
        new_messages: list[dict],
        start_seq: int,
        last_consolidated: int = 0,
    ) -> None:
        """仅追加本轮新产生的消息。

        对于长会话比全量保存更高效。
        last_consolidated 同步写入 nb_sessions，确保指针持久化。
        """
        if not new_messages:
            return

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                # Upsert 会话记录，保留 last_consolidated
                await cur.execute(
                    """
                    INSERT INTO nb_sessions
                        (tenant_key, session_key, session_type, is_primary,
                         last_consolidated, created_at, updated_at)
                    VALUES (%s, %s, 'main', 0, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        last_consolidated = VALUES(last_consolidated),
                        updated_at        = VALUES(updated_at)
                    """,
                    (tenant_key, session_key, last_consolidated, now, now),
                )

                for i, msg in enumerate(new_messages):
                    seq = start_seq + i
                    role = msg.get("role", "")
                    tool_call_id = msg.get("tool_call_id")
                    tool_name = None
                    tool_calls = msg.get("tool_calls") or []
                    if tool_calls:
                        fn = (tool_calls[0].get("function") or {})
                        tool_name = fn.get("name")

                    msg_json = json.dumps(msg, ensure_ascii=False)
                    tokens_estimate = _estimate_message_tokens(msg)
                    await cur.execute(
                        """
                        INSERT IGNORE INTO nb_session_messages
                            (tenant_key, session_key, seq, message_json, content_json,
                             role, tool_name, tool_call_id, tokens_estimate, created_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (tenant_key, session_key, seq, msg_json, msg_json,
                         role, tool_name, tool_call_id, tokens_estimate, now),
                    )

    # ------------------------------------------------------------------
    # 更新 consolidated 指针（冷路径压缩后调用）
    # ------------------------------------------------------------------

    async def update_consolidated(
        self,
        tenant_key: str,
        session_key: str,
        last_consolidated: int,
    ) -> None:
        """冷路径压缩后推进工作窗口指针。

        当记忆被提取、旧消息被淘汰时，仅需执行此写操作——不从数据库删除任何消息。
        """
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE nb_sessions
                    SET last_consolidated = %s, updated_at = %s
                    WHERE tenant_key = %s AND session_key = %s
                    """,
                    (last_consolidated, now, tenant_key, session_key),
                )


    # ------------------------------------------------------------------
    # 更新元数据
    # ------------------------------------------------------------------

    async def update_title(
        self,
        tenant_key: str,
        session_key: str,
        title: str,
    ) -> None:
        """更新会话标题（首条用户消息后调用）。"""
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE nb_sessions
                    SET title = %s, updated_at = %s
                    WHERE tenant_key = %s AND session_key = %s AND title IS NULL
                    """,
                    (title, now, tenant_key, session_key),
                )

    # ------------------------------------------------------------------
    # 删除
    # ------------------------------------------------------------------

    async def delete_session(
        self,
        tenant_key: str,
        session_key: str,
    ) -> bool:
        """删除会话及其所有消息。

        Returns: True 若确实删除了行，False 若会话不存在。
        """
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM nb_session_messages WHERE tenant_key = %s AND session_key = %s",
                    (tenant_key, session_key),
                )
                await cur.execute(
                    "DELETE FROM nb_sessions WHERE tenant_key = %s AND session_key = %s",
                    (tenant_key, session_key),
                )
        return True


def _estimate_message_tokens(msg: dict[str, Any]) -> int:
    total = estimate_content_tokens(msg.get("content") or "")
    for tc in msg.get("tool_calls") or []:
        fn = tc.get("function") or {}
        total += estimate_content_tokens(fn.get("arguments") or "")
    return max(total, 1)
