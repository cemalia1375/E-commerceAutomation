"""Tests for provider cache expiry comparisons."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from Mojing.storage import llm_cache_repo as repo_module
from Mojing.storage.llm_cache_repo import LLMCacheRepository, SessionCacheRecord


class _Cursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def execute(self, sql: str, params: tuple) -> None:
        self.executed.append((sql, params))

    async def fetchone(self):
        return None


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def cursor(self) -> _Cursor:
        return self._cursor


class _Database:
    def __init__(self) -> None:
        self.cursor_obj = _Cursor()

    def acquire(self) -> _Connection:
        return _Connection(self.cursor_obj)


class LLMCacheRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_prefix_cache_default_now_uses_unix_time(self) -> None:
        db = _Database()
        repo = LLMCacheRepository(db)  # type: ignore[arg-type]

        with patch.object(repo_module.time, "time", return_value=1777517009.7):
            result = await repo.get_prefix_cache(
                provider="volcengine",
                lane="main_agent",
                tenant_key="tenant-1",
                session_key="main:tenant-1",
                model="model",
                thinking_type="disabled",
                prompt_fingerprint="prompt",
                tools_fingerprint="tools",
            )

        self.assertIsNone(result)
        self.assertEqual(db.cursor_obj.executed[0][1][-1], 1777517009)

    async def test_session_memory_cache_expires_against_unix_time(self) -> None:
        db = _Database()
        repo = LLMCacheRepository(db)  # type: ignore[arg-type]
        key = repo_module._session_cache_key(
            provider="volcengine",
            lane="opener",
            tenant_key="tenant-1",
            session_key="main:tenant-1",
            model="model",
            thinking_type="disabled",
            cache_mode="session_chain",
            prompt_fingerprint="prompt",
            context_version=0,
        )
        repo._session_cache[key] = SessionCacheRecord(
            id=1,
            response_id="resp_expired",
            base_response_id=None,
            turn_count=1,
            expire_at=1777517000,
            main_consolidated_from=0,
            context_fingerprint=None,
            metadata={},
        )

        with patch.object(repo_module.time, "time", return_value=1777517009.7):
            result = await repo.get_session_cache(
                provider="volcengine",
                lane="opener",
                tenant_key="tenant-1",
                session_key="main:tenant-1",
                model="model",
                thinking_type="disabled",
                cache_mode="session_chain",
                prompt_fingerprint="prompt",
                context_version=0,
            )

        self.assertIsNone(result)
        self.assertNotIn(key, repo._session_cache)
        self.assertEqual(db.cursor_obj.executed[0][1][-1], 1777517009)


if __name__ == "__main__":
    unittest.main()
