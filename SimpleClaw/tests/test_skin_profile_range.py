import unittest
from datetime import datetime

from Mojing.storage.skin_profile_repo import SkinProfileRepository


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.executed = []
        self.description = [
            ("profile_id",), ("tenant_key",), ("session_key",), ("message_id",),
            ("image_url",), ("analysis_id",), ("skin_attribute_json",), ("overall_state",),
            ("advantages_json",), ("signals_json",), ("sync_status",), ("sync_reason",),
            ("synced_to_user_doc_at",), ("sync_error",), ("created_at",), ("updated_at",),
        ]

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
    def __init__(self, rows):
        self.cursor = _FakeCursor(rows)

    def acquire(self):
        return _FakeAcquire(_FakeConn(self.cursor))


def _row(profile_id, created_at, signals):
    return (
        profile_id, "t1", "s1", None, "http://img", "an1",
        None, "stable", None, signals, "synced", None, None, None,
        created_at, created_at,
    )


class TestListProfilesInRange(unittest.IsolatedAsyncioTestCase):
    async def test_returns_multiple_rows_as_dicts(self) -> None:
        rows = [
            _row(2, datetime(2026, 6, 2, 9), [{"signalCode": "黑头"}]),
            _row(1, datetime(2026, 5, 20, 9), [{"signalCode": "黑头"}]),
        ]
        db = _FakeDB(rows)
        repo = SkinProfileRepository(db)
        result = await repo.list_profiles_in_range(
            "t1", datetime(2026, 5, 20), datetime(2026, 6, 3)
        )
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["profile_id"], 2)
        self.assertEqual(result[0]["signals_json"], [{"signalCode": "黑头"}])
        sql, params = db.cursor.executed[0]
        self.assertIn("created_at >= %s", sql)
        self.assertIn("created_at < %s", sql)
        self.assertEqual(params, ("t1", datetime(2026, 5, 20), datetime(2026, 6, 3)))

    async def test_empty_returns_empty_list(self) -> None:
        db = _FakeDB([])
        repo = SkinProfileRepository(db)
        result = await repo.list_profiles_in_range(
            "t1", datetime(2026, 5, 20), datetime(2026, 6, 3)
        )
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
