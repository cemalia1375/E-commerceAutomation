"""DocumentRepository — 从 nb_tenant_documents 读取按租户存储的文档。

表结构关键字段：
  tenant_key, doc_type (e.g. "user"), doc_name (e.g. "USER.md"), content
  唯一键：(tenant_key, doc_type, doc_name)

doc_name → doc_type 映射：
  USER.md      → user
  SOUL.md      → soul
  其他          → doc
"""

from __future__ import annotations

import contextvars
import hashlib
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from Mojing.storage.database import Database

_DOC_TYPE_MAP = {
    "USER.md":       "user",
    "SOUL.md":       "soul",
    "SKIN_DIARY_TODO.md": "skin_diary_todo",
}


def _doc_type(doc_name: str) -> str:
    return _DOC_TYPE_MAP.get(doc_name, "doc")


@dataclass(slots=True)
class DocumentWriteContext:
    """Lightweight provenance for document side effects."""

    change_source: str | None = None
    source_task_id: str | None = None
    session_key: str | None = None
    trace_id: str | None = None
    message_seq_start: int | None = None
    message_seq_end: int | None = None
    change_summary: str | None = None
    operator_id: str | None = None


_DOCUMENT_WRITE_CONTEXT: contextvars.ContextVar[DocumentWriteContext | None] = (
    contextvars.ContextVar("mojing_document_write_context", default=None)
)


@contextmanager
def document_write_context(
    *,
    change_source: str | None = None,
    source_task_id: str | None = None,
    session_key: str | None = None,
    trace_id: str | None = None,
    message_seq_start: int | None = None,
    message_seq_end: int | None = None,
    change_summary: str | None = None,
    operator_id: str | None = None,
):
    """Attach provenance to all document writes in the current async context."""

    token = _DOCUMENT_WRITE_CONTEXT.set(DocumentWriteContext(
        change_source=change_source,
        source_task_id=source_task_id,
        session_key=session_key,
        trace_id=trace_id,
        message_seq_start=message_seq_start,
        message_seq_end=message_seq_end,
        change_summary=change_summary,
        operator_id=operator_id,
    ))
    try:
        yield
    finally:
        _DOCUMENT_WRITE_CONTEXT.reset(token)


class DocumentRepository:
    """对 nb_tenant_documents 的轻量读写封装层。"""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, tenant_key: str, doc_name: str) -> str | None:
        """返回文档内容，若文档不存在则返回 None。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT content
                    FROM nb_tenant_documents
                    WHERE tenant_key = %s AND doc_name = %s AND is_active = 1
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (tenant_key, doc_name),
                )
                row = await cur.fetchone()
        return row[0] if row else None

    async def get_metadata(self, tenant_key: str, doc_name: str) -> dict[str, Any] | None:
        """Return lightweight document metadata without loading large content."""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT content_hash, updated_at, created_at
                    FROM nb_tenant_documents
                    WHERE tenant_key = %s AND doc_name = %s AND is_active = 1
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (tenant_key, doc_name),
                )
                row = await cur.fetchone()
                cols = [d[0] for d in cur.description] if cur.description else []
        if row is None:
            return None
        item = dict(zip(cols, row))
        for key in ("updated_at", "created_at"):
            value = item.get(key)
            if isinstance(value, datetime):
                item[key] = value.strftime("%Y-%m-%d %H:%M:%S")
        return item

    async def list_versions_by_source_tasks(
        self,
        *,
        tenant_key: str,
        source_task_ids: list[str] | tuple[str, ...],
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return document versions produced by known runtime task ids."""
        tenant_key = str(tenant_key or "").strip()
        task_ids = [str(task_id or "").strip() for task_id in source_task_ids if str(task_id or "").strip()]
        if not tenant_key or not task_ids:
            return []
        task_ids = task_ids[:50]
        limit = max(1, min(int(limit or 20), 100))
        placeholders = ",".join(["%s"] * len(task_ids))
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"""
                    SELECT version_id, doc_id, tenant_key, doc_type, doc_name, version_no,
                           content_hash, change_summary, change_source, source_task_id,
                           session_key, trace_id, message_seq_start, message_seq_end,
                           operator_id, created_at
                    FROM nb_tenant_document_versions
                    WHERE tenant_key=%s
                      AND source_task_id IN ({placeholders})
                    ORDER BY created_at DESC, version_id DESC
                    LIMIT %s
                    """,
                    (tenant_key, *task_ids, limit),
                )
                rows = await cur.fetchall()
                cols = [d[0] for d in cur.description] if cur.description else []
        return [_format_version_row(dict(zip(cols, row))) for row in rows]

    async def list_recent_versions_for_session(
        self,
        *,
        tenant_key: str,
        session_key: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Return recent document versions associated with one session."""
        tenant_key = str(tenant_key or "").strip()
        session_key = str(session_key or "").strip()
        if not tenant_key or not session_key:
            return []
        limit = max(1, min(int(limit or 10), 50))
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT version_id, doc_id, tenant_key, doc_type, doc_name, version_no,
                           content_hash, change_summary, change_source, source_task_id,
                           session_key, trace_id, message_seq_start, message_seq_end,
                           operator_id, created_at
                    FROM nb_tenant_document_versions
                    WHERE tenant_key=%s
                      AND session_key=%s
                    ORDER BY created_at DESC, version_id DESC
                    LIMIT %s
                    """,
                    (tenant_key, session_key, limit),
                )
                rows = await cur.fetchall()
                cols = [d[0] for d in cur.description] if cur.description else []
        return [_format_version_row(dict(zip(cols, row))) for row in rows]

    async def set(
        self,
        tenant_key: str,
        doc_name: str,
        content: str,
        *,
        change_source: str | None = None,
        source_task_id: str | None = None,
        session_key: str | None = None,
        trace_id: str | None = None,
        message_seq_start: int | None = None,
        message_seq_end: int | None = None,
        change_summary: str | None = None,
        operator_id: str | None = None,
    ) -> None:
        """插入或更新一条文档记录（由后处理写入器调用）。"""
        now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        doc_type = _doc_type(doc_name)
        content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()
        ctx = _DOCUMENT_WRITE_CONTEXT.get()
        meta = DocumentWriteContext(
            change_source=change_source or (ctx.change_source if ctx else None) or "document_repo",
            source_task_id=source_task_id or (ctx.source_task_id if ctx else None),
            session_key=session_key or (ctx.session_key if ctx else None),
            trace_id=trace_id or (ctx.trace_id if ctx else None),
            message_seq_start=message_seq_start if message_seq_start is not None else (ctx.message_seq_start if ctx else None),
            message_seq_end=message_seq_end if message_seq_end is not None else (ctx.message_seq_end if ctx else None),
            change_summary=change_summary or (ctx.change_summary if ctx else None),
            operator_id=operator_id or (ctx.operator_id if ctx else None) or "agent",
        )

        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    SELECT doc_id, content_hash, version_no
                    FROM nb_tenant_documents
                    WHERE tenant_key = %s AND doc_name = %s AND is_active = 1
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (tenant_key, doc_name),
                )
                row = await cur.fetchone()
                if row and str(row[1] or "") == content_hash:
                    return
                next_version = int(row[2] or 0) + 1 if row else 1
                await cur.execute(
                    """
                    INSERT INTO nb_tenant_documents
                        (tenant_key, doc_type, doc_name, content, content_hash,
                         format, version_no, is_active, created_by, updated_by,
                         created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, 'markdown', %s, 1, 'agent', 'agent', %s, %s)
                    ON DUPLICATE KEY UPDATE
                        content      = VALUES(content),
                        content_hash = VALUES(content_hash),
                        version_no   = VALUES(version_no),
                        updated_by   = VALUES(updated_by),
                        updated_at   = VALUES(updated_at)
                    """,
                    (tenant_key, doc_type, doc_name, content, content_hash, next_version, now, now),
                )
                await cur.execute(
                    """
                    SELECT doc_id, version_no
                    FROM nb_tenant_documents
                    WHERE tenant_key = %s AND doc_name = %s AND is_active = 1
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (tenant_key, doc_name),
                )
                doc_row = await cur.fetchone()
                if not doc_row:
                    return
                try:
                    await cur.execute(
                        """
                        INSERT INTO nb_tenant_document_versions
                            (doc_id, tenant_key, doc_type, doc_name, version_no,
                             content, content_hash, change_summary, change_source,
                             source_task_id, session_key, trace_id, message_seq_start,
                             message_seq_end, operator_id, created_at)
                        VALUES
                            (%s, %s, %s, %s, %s,
                             %s, %s, %s, %s,
                             %s, %s, %s, %s,
                             %s, %s, %s)
                        """,
                        (
                            int(doc_row[0]),
                            tenant_key,
                            doc_type,
                            doc_name,
                            int(doc_row[1] or next_version),
                            content,
                            content_hash,
                            _trim(meta.change_summary, 512),
                            _trim(meta.change_source, 64),
                            _trim(meta.source_task_id, 64),
                            _trim(meta.session_key, 256),
                            _trim(meta.trace_id, 64),
                            meta.message_seq_start,
                            meta.message_seq_end,
                            _trim(meta.operator_id, 255),
                            now,
                        ),
                    )
                except Exception as exc:
                    logger.warning(
                        "document version write failed tenant={} doc={} version={} err={}",
                        tenant_key,
                        doc_name,
                        int(doc_row[1] or next_version),
                        exc,
                    )


def _trim(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


def _format_version_row(row: dict[str, Any]) -> dict[str, Any]:
    for key in ("created_at",):
        value = row.get(key)
        if isinstance(value, datetime):
            row[key] = value.strftime("%Y-%m-%d %H:%M:%S")
    return row
