"""进程内 scope_lock 注册表。

用于 durable task 的细粒度保序：不同 worker 可以共享同一份 registry，
从而按 scope_key 串行，而不是靠整个 stream 的单 worker 粗暴串行。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator


class ScopeLockRegistry:
    """按 scope_key 提供共享 asyncio.Lock。"""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._registry_lock = asyncio.Lock()

    async def get_lock(self, scope_key: str) -> asyncio.Lock:
        async with self._registry_lock:
            lock = self._locks.get(scope_key)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[scope_key] = lock
            return lock

    @asynccontextmanager
    async def hold(self, scope_key: str) -> AsyncIterator[None]:
        lock = await self.get_lock(scope_key)
        await lock.acquire()
        try:
            yield
        finally:
            lock.release()
