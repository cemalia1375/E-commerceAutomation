"""Tests for scenario runner memory watch hooks (no MySQL required)."""

from __future__ import annotations

import unittest

from Mojing.storage.memory_repo import MySQLMemory
from script.runner.memory_watch import MemoryWatcher, install_memory_watch


class _FakeLogger:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write(self, message: str) -> None:
        self.lines.append(message)


class _Cursor:
    def __init__(self, fetchone_results: list) -> None:
        self._fetchone_results = fetchone_results
        self.executed: list[tuple[str, tuple | None]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def execute(self, sql: str, params=None) -> None:
        self.executed.append((sql, params))

    async def fetchone(self):
        if self._fetchone_results:
            return self._fetchone_results.pop(0)
        return None

    async def fetchall(self):
        return []


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
    def __init__(self, fetchone_results: list | None = None) -> None:
        self.cursor_obj = _Cursor(fetchone_results or [])

    def acquire(self) -> _Connection:
        return _Connection(self.cursor_obj)


class MemoryWatchTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.logger = _FakeLogger()

    async def test_store_new_topic_logs_create(self) -> None:
        db = _Database(fetchone_results=[None])  # 存在性检查：不存在
        watcher = install_memory_watch(self.logger, tenant_key="tenant-1")
        try:
            watcher.set_phase("2")
            memory = MySQLMemory(db, tenant_key="tenant-1", source="main")  # type: ignore[arg-type]
            await memory.store("skin_type", "油性皮肤", description="用户为油性皮肤")
        finally:
            watcher.uninstall()

        self.assertEqual(len(watcher.records), 1)
        record = watcher.records[0]
        self.assertEqual(record.action, "create")
        self.assertEqual(record.phase, "2")
        self.assertEqual(record.source, "main")
        self.assertEqual(record.topic, "skin_type")
        self.assertIn(
            "MEMORY turn=2 action=create source=main topic=skin_type desc=用户为油性皮肤",
            self.logger.lines,
        )
        # 原始 store 仍被执行（存在性 SELECT + INSERT 两条）
        self.assertEqual(len(db.cursor_obj.executed), 2)
        self.assertIn("INSERT INTO nb_memory_entries", db.cursor_obj.executed[1][0])

    async def test_store_forwards_memory_type_kwarg(self) -> None:
        # 回归：memory_extract 的 create/update 路径会带 memory_type= 调 store；
        # wrapped_store 必须透传，否则 TypeError 被上游吞掉、记忆写不进库。
        db = _Database(fetchone_results=[None])
        watcher = install_memory_watch(self.logger, tenant_key="tenant-1")
        try:
            memory = MySQLMemory(db, tenant_key="tenant-1", source="main")  # type: ignore[arg-type]
            await memory.store("skin_type", "鼻翼黑头先加重后改善", description="趋势", memory_type="skin")
        finally:
            watcher.uninstall()

        self.assertEqual(watcher.records[0].action, "create")
        insert_sql, insert_params = db.cursor_obj.executed[1]
        self.assertIn("INSERT INTO nb_memory_entries", insert_sql)
        # 真实 store 的 INSERT 参数顺序：...content, memory_type, token_count...
        self.assertIn("skin", insert_params)

    async def test_store_existing_topic_logs_update(self) -> None:
        db = _Database(fetchone_results=[(1,)])  # 存在性检查：已存在
        watcher = install_memory_watch(self.logger, tenant_key="tenant-1")
        try:
            memory = MySQLMemory(db, tenant_key="tenant-1", source="main")  # type: ignore[arg-type]
            await memory.store("skin_type", "混合皮", description="更新后的描述")
        finally:
            watcher.uninstall()

        self.assertEqual(watcher.records[0].action, "update")

    async def test_delete_logs_delete(self) -> None:
        db = _Database()
        watcher = install_memory_watch(self.logger, tenant_key="tenant-1")
        try:
            watcher.set_phase("3")
            memory = MySQLMemory(db, tenant_key="tenant-1", source="main")  # type: ignore[arg-type]
            await memory.delete("skin_type")
        finally:
            watcher.uninstall()

        self.assertEqual(len(watcher.records), 1)
        self.assertEqual(watcher.records[0].action, "delete")
        self.assertIn("MEMORY turn=3 action=delete source=main topic=skin_type", self.logger.lines)

    async def test_tenant_mismatch_not_recorded(self) -> None:
        db = _Database(fetchone_results=[None])
        watcher = install_memory_watch(self.logger, tenant_key="tenant-1")
        try:
            memory = MySQLMemory(db, tenant_key="other-tenant", source="main")  # type: ignore[arg-type]
            await memory.store("skin_type", "油性皮肤", description="desc")
        finally:
            watcher.uninstall()

        self.assertEqual(watcher.records, [])
        self.assertEqual(self.logger.lines, [])
        # 原始写入仍被透传（只有 INSERT，没有存在性 SELECT）
        self.assertEqual(len(db.cursor_obj.executed), 1)

    async def test_uninstall_restores_originals(self) -> None:
        original_store = MySQLMemory.store
        original_delete = MySQLMemory.delete

        watcher = install_memory_watch(self.logger, tenant_key="tenant-1")
        self.assertIsNot(MySQLMemory.store, original_store)
        watcher.uninstall()

        self.assertIs(MySQLMemory.store, original_store)
        self.assertIs(MySQLMemory.delete, original_delete)

    async def test_summary_line(self) -> None:
        db = _Database(fetchone_results=[None, (1,)])
        watcher = install_memory_watch(self.logger, tenant_key="tenant-1")
        try:
            memory = MySQLMemory(db, tenant_key="tenant-1", source="main")  # type: ignore[arg-type]
            watcher.set_phase("1")
            await memory.store("topic_a", "content", description="d1")
            watcher.set_phase("3")
            await memory.store("topic_b", "content", description="d2")
        finally:
            watcher.uninstall()

        self.assertEqual(
            watcher.summary_line(),
            "MEMORY SUMMARY total=2 turns=[1, 3]",
        )

    async def test_summary_line_empty(self) -> None:
        watcher = MemoryWatcher(self.logger, tenant_key="tenant-1")
        self.assertEqual(
            watcher.summary_line(),
            "MEMORY SUMMARY total=0 turns=[]",
        )


if __name__ == "__main__":
    unittest.main()
