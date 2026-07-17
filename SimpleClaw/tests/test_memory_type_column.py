import unittest

from Mojing.storage.memory_repo import MySQLMemory


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.executed = []  # [(sql, params)]
        self.description = [("topic",), ("description",), ("content",)]

    async def execute(self, sql, params=None):
        self.executed.append((sql, params))

    async def fetchall(self):
        return self._rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeAcquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakeDB:
    def __init__(self, rows=None):
        self.cursor = _FakeCursor(rows or [])

    def acquire(self):
        return _FakeAcquire(_FakeConn(self.cursor))


class TestMemoryTypeColumn(unittest.IsolatedAsyncioTestCase):
    async def test_store_writes_memory_type(self) -> None:
        db = _FakeDB()
        mem = MySQLMemory(db, tenant_key="t1", source="main")
        await mem.store("皮肤问题", "正文", description="描述", memory_type="skin")
        sql, params = db.cursor.executed[-1]
        self.assertIn("memory_type", sql)
        # params 顺序：(tenant_key, source, topic, description, content, memory_type, ...)
        self.assertEqual(params[5], "skin")

    async def test_store_defaults_memory_type_chitchat(self) -> None:
        db = _FakeDB()
        mem = MySQLMemory(db, tenant_key="t1", source="main")
        await mem.store("工作受委屈", "正文", description="描述")
        sql, params = db.cursor.executed[-1]
        self.assertIn("chitchat", params)

    async def test_retrieve_pins_skin_first(self) -> None:
        db = _FakeDB(rows=[("皮肤问题", "skin 描述", "skin 正文")])
        mem = MySQLMemory(db, tenant_key="t1", source="main")
        await mem.retrieve(top_k=20)
        select_sql = db.cursor.executed[0][0]
        # skin 条目必须在 ORDER BY 里被置顶（不被 LRU 挤出 top_k）
        self.assertIn("memory_type = 'skin'", select_sql)
        self.assertLess(
            select_sql.index("memory_type = 'skin'"),
            select_sql.index("last_referenced_at"),
        )


if __name__ == "__main__":
    unittest.main()
