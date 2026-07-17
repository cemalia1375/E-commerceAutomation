"""ScriptRepository v2 单元测试（fake DB）。"""
from __future__ import annotations

import json
import unittest

from Flowcut.storage.script_repo import ScriptRepository, StatusConflictError


class _Cursor:
    def __init__(self, owner: "_Database") -> None:
        self._owner = owner
        self.lastrowid: int = 0
        self._next_fetchone: tuple | None = None
        self._next_fetchall: list[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def execute(self, sql: str, params: tuple | list = ()) -> None:
        sql_norm = " ".join(sql.split()).upper()
        self._owner.executed.append((sql, tuple(params or ())))
        if sql_norm.startswith("INSERT INTO FC_SCRIPT"):
            self._owner._auto_id += 1
            self.lastrowid = self._owner._auto_id
            tenant_key, source, ref_id, product, segments_json, now1, now2 = params
            self._owner.rows[self.lastrowid] = {
                "id": self.lastrowid,
                "tenant_key": tenant_key,
                "source": source,
                "reference_video_id": ref_id,
                "product": product,
                "segments_json": segments_json,
                "status": "DRAFT",
                "created_at": now1,
                "updated_at": now2,
            }
        elif sql_norm.startswith("UPDATE FC_SCRIPT SET SEGMENTS_JSON"):
            segments_json, now, sid = params
            if sid in self._owner.rows:
                self._owner.rows[sid]["segments_json"] = segments_json
                self._owner.rows[sid]["updated_at"] = now
        elif sql_norm.startswith("UPDATE FC_SCRIPT SET STATUS"):
            status, now, sid = params
            if sid in self._owner.rows:
                self._owner.rows[sid]["status"] = status
                self._owner.rows[sid]["updated_at"] = now
        elif sql_norm.startswith("SELECT"):
            if "WHERE ID = %S" in sql_norm:
                (sid,) = params
                row = self._owner.rows.get(sid)
                self._next_fetchone = _row_tuple(row) if row else None
            else:
                # list_by_tenant
                rows = [r for r in self._owner.rows.values()
                        if r["tenant_key"] == params[0]]
                # 处理可选 status/source 过滤
                tail = params[1:]
                if " AND STATUS = %S" in sql_norm:
                    rows = [r for r in rows if r["status"] == tail[0]]
                    tail = tail[1:]
                if " AND SOURCE = %S" in sql_norm:
                    rows = [r for r in rows if r["source"] == tail[0]]
                rows.sort(key=lambda r: r["id"], reverse=True)
                self._next_fetchall = [_row_tuple(r) for r in rows]

    async def fetchone(self):
        return self._next_fetchone

    async def fetchall(self):
        return self._next_fetchall


def _row_tuple(r: dict) -> tuple:
    return (
        r["id"], r["tenant_key"], r["source"], r["reference_video_id"],
        r.get("product"), r["segments_json"], r["status"],
        r["created_at"], r["updated_at"],
    )


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def cursor(self) -> _Cursor:
        return self._cursor

    async def commit(self) -> None:
        return None


class _Database:
    def __init__(self) -> None:
        self.rows: dict[int, dict] = {}
        self.executed: list[tuple] = []
        self._auto_id = 0

    def acquire(self) -> _Connection:
        return _Connection(_Cursor(self))


class ScriptRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_create_uploaded_script(self) -> None:
        repo = ScriptRepository(_Database())  # type: ignore[arg-type]
        record = await repo.create(
            tenant_key="t1",
            source="uploaded",
            segments=[
                {"idx": 0, "start_time": 0.0, "end_time": 3.0,
                 "visual": "v", "copy": "c"}
            ],
        )
        self.assertGreater(record["id"], 0)
        self.assertEqual(record["source"], "uploaded")
        self.assertEqual(record["status"], "DRAFT")
        self.assertIsNone(record["reference_video_id"])
        self.assertEqual(record["segments"][0]["visual"], "v")

    async def test_create_decomposed_script(self) -> None:
        repo = ScriptRepository(_Database())  # type: ignore[arg-type]
        record = await repo.create(
            tenant_key="t1",
            source="decomposed",
            reference_video_id=42,
            segments=[],
        )
        self.assertEqual(record["reference_video_id"], 42)

    async def test_update_segments_only_draft(self) -> None:
        repo = ScriptRepository(_Database())  # type: ignore[arg-type]
        record = await repo.create(tenant_key="t1", source="uploaded", segments=[])
        sid = record["id"]
        await repo.update_segments(sid, [{"idx": 0, "visual": "v2", "copy": ""}])
        await repo.update_status(sid, "CONFIRMED")
        with self.assertRaises(StatusConflictError):
            await repo.update_segments(sid, [])

    async def test_list_by_tenant_filter(self) -> None:
        repo = ScriptRepository(_Database())  # type: ignore[arg-type]
        await repo.create(tenant_key="t1", source="uploaded", segments=[])
        await repo.create(tenant_key="t1", source="decomposed",
                          reference_video_id=1, segments=[])
        uploaded = await repo.list_by_tenant("t1", source="uploaded")
        self.assertTrue(all(r["source"] == "uploaded" for r in uploaded))


if __name__ == "__main__":
    unittest.main()
