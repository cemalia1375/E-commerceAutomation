"""UserRepository / LoginSessionRepository — fc_user & fc_login_session 读写封装。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from Flowcut.storage.database import Database


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class UserRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get_by_username(self, username: str) -> dict[str, Any] | None:
        """按用户名查用户。返回 dict | None。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT * FROM fc_user WHERE username=%s",
                    (username,),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    async def get_by_id(self, user_id: int) -> dict[str, Any] | None:
        """按 id 查用户。返回 dict | None。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT * FROM fc_user WHERE id=%s",
                    (user_id,),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    async def create(
        self,
        *,
        username: str,
        password_hash: str,
        tenant_key: str,
        display_name: str | None = None,
    ) -> int:
        """创建用户，返回新用户 id。"""
        now = _now()
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO fc_user
                        (username, password_hash, tenant_key, display_name,
                         disabled, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, 0, %s, %s)
                    """,
                    (username, password_hash, tenant_key, display_name, now, now),
                )
                return int(cur.lastrowid)


class LoginSessionRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(
        self,
        *,
        session_id_hash: str,
        user_id: int,
        tenant_key: str,
        expires_at: datetime,
    ) -> None:
        """写入一条登录会话。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO fc_login_session
                        (session_id_hash, user_id, tenant_key, expires_at, created_at)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        session_id_hash,
                        user_id,
                        tenant_key,
                        expires_at.strftime("%Y-%m-%d %H:%M:%S"),
                        _now(),
                    ),
                )

    async def get_valid(self, session_id_hash: str) -> dict[str, Any] | None:
        """查询未过期的登录会话。过期或不存在返回 None。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT * FROM fc_login_session
                    WHERE session_id_hash=%s AND expires_at > UTC_TIMESTAMP()
                    """,
                    (session_id_hash,),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    async def delete(self, session_id_hash: str) -> None:
        """删除指定登录会话（登出）。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM fc_login_session WHERE session_id_hash=%s",
                    (session_id_hash,),
                )

    async def delete_expired(self) -> int:
        """清理所有已过期会话，返回删除行数。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM fc_login_session WHERE expires_at <= UTC_TIMESTAMP()"
                )
                return int(cur.rowcount)
