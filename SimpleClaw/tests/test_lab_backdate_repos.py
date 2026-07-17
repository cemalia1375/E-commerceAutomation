"""/admin/lab 回填用的 created_at 改写方法测试（无真实 MySQL）。"""
import unittest
from datetime import datetime
from typing import Any

from Mojing.storage.image_repo import ImageRepository
from Mojing.storage.skin_profile_repo import SkinProfileRepository


class _RecordingCursor:
    def __init__(self, log: list[tuple[str, Any]]) -> None:
        self._log = log

    async def execute(self, sql: str, params: Any = None) -> None:
        self._log.append((sql, params))

    async def __aenter__(self) -> "_RecordingCursor":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None


class _RecordingConn:
    def __init__(self, log: list[tuple[str, Any]]) -> None:
        self._log = log

    def cursor(self) -> _RecordingCursor:
        return _RecordingCursor(self._log)


class _RecordingDb:
    def __init__(self) -> None:
        self.executed: list[tuple[str, Any]] = []

    def acquire(self) -> "_RecordingDb._Ctx":
        return self._Ctx(self.executed)

    class _Ctx:
        def __init__(self, log: list[tuple[str, Any]]) -> None:
            self._log = log

        async def __aenter__(self) -> _RecordingConn:
            return _RecordingConn(self._log)

        async def __aexit__(self, *exc: Any) -> None:
            return None


class _ProbeSkinProfileRepository(SkinProfileRepository):
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def _execute(self, sql: str, params: tuple[Any, ...]) -> None:
        self.calls.append((sql, params))


class BackdateJobTest(unittest.IsolatedAsyncioTestCase):
    async def test_backdate_job_rewrites_all_four_time_columns(self) -> None:
        db = _RecordingDb()
        repo = ImageRepository(db)  # type: ignore[arg-type]
        hist = datetime(2026, 6, 1, 4, 0, 0)

        await repo.backdate_job("job-1", created_at=hist)

        self.assertEqual(len(db.executed), 1)
        sql, params = db.executed[0]
        self.assertIn("UPDATE nb_image_analysis_jobs", sql)
        for col in ("created_at=%s", "updated_at=%s", "started_at=%s", "completed_at=%s"):
            self.assertIn(col, sql)
        ts = "2026-06-01 04:00:00"
        self.assertEqual(params, (ts, ts, ts, ts, "job-1"))


class BackdateProfileTest(unittest.IsolatedAsyncioTestCase):
    async def test_backdate_profile_rewrites_created_and_updated_only(self) -> None:
        repo = _ProbeSkinProfileRepository()
        hist = datetime(2026, 6, 1, 4, 0, 0)

        await repo.backdate_profile(42, created_at=hist)

        self.assertEqual(len(repo.calls), 1)
        sql, params = repo.calls[0]
        self.assertIn("UPDATE nb_tenant_skin_profiles", sql)
        self.assertIn("created_at = %s", sql)
        self.assertIn("updated_at = %s", sql)
        # sync 时间戳保留真实值作审计，不应被改写
        self.assertNotIn("synced_to_user_doc_at", sql)
        ts = "2026-06-01 04:00:00"
        self.assertEqual(params, (ts, ts, 42))
