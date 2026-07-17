"""Tests for document version ledger writes."""

from __future__ import annotations

import unittest

from Mojing.storage.document_repo import DocumentRepository, document_write_context


class _Cursor:
    def __init__(self, db: "_Database") -> None:
        self._db = db
        self._result = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def execute(self, sql: str, params: tuple) -> None:
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT doc_id, content_hash, version_no"):
            tenant_key, doc_name = params
            row = self._db.docs.get((tenant_key, doc_name))
            self._result = None if row is None else (row["doc_id"], row["content_hash"], row["version_no"])
            return
        if normalized.startswith("INSERT INTO nb_tenant_documents"):
            tenant_key, doc_type, doc_name, content, content_hash, version_no, _created_at, updated_at = params
            key = (tenant_key, doc_name)
            existing = self._db.docs.get(key)
            doc_id = existing["doc_id"] if existing else self._db.next_doc_id
            if existing is None:
                self._db.next_doc_id += 1
            self._db.docs[key] = {
                "doc_id": doc_id,
                "tenant_key": tenant_key,
                "doc_type": doc_type,
                "doc_name": doc_name,
                "content": content,
                "content_hash": content_hash,
                "version_no": version_no,
                "updated_at": updated_at,
            }
            self._result = None
            return
        if normalized.startswith("SELECT doc_id, version_no"):
            tenant_key, doc_name = params
            row = self._db.docs.get((tenant_key, doc_name))
            self._result = None if row is None else (row["doc_id"], row["version_no"])
            return
        if normalized.startswith("INSERT INTO nb_tenant_document_versions"):
            self._db.versions.append(params)
            self._result = None
            return
        raise AssertionError(f"unexpected SQL: {normalized}")

    async def fetchone(self):
        return self._result


class _Connection:
    def __init__(self, db: "_Database") -> None:
        self._db = db

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def cursor(self) -> _Cursor:
        return _Cursor(self._db)


class _Database:
    def __init__(self) -> None:
        self.docs: dict[tuple[str, str], dict] = {}
        self.versions: list[tuple] = []
        self.next_doc_id = 1

    def acquire(self) -> _Connection:
        return _Connection(self)


class DocumentRepositoryVersionsTest(unittest.IsolatedAsyncioTestCase):
    async def test_set_writes_versions_only_when_content_changes(self) -> None:
        db = _Database()
        repo = DocumentRepository(db)  # type: ignore[arg-type]

        with document_write_context(
            change_source="postprocess",
            source_task_id="task-1",
            session_key="main:tenant-1",
            trace_id="trace-1",
            operator_id="mojing:post-turn",
        ):
            await repo.set("tenant-1", "USER.md", "v1")
            await repo.set("tenant-1", "USER.md", "v1")
            await repo.set("tenant-1", "USER.md", "v2")

        self.assertEqual(db.docs[("tenant-1", "USER.md")]["version_no"], 2)
        self.assertEqual(len(db.versions), 2)
        self.assertEqual(db.versions[0][4], 1)
        self.assertEqual(db.versions[1][4], 2)
        self.assertEqual(db.versions[1][8], "postprocess")
        self.assertEqual(db.versions[1][9], "task-1")
        self.assertEqual(db.versions[1][10], "main:tenant-1")
        self.assertEqual(db.versions[1][11], "trace-1")


if __name__ == "__main__":
    unittest.main()
