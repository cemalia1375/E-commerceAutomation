"""Mutation tools for Mojing DreamSubagent.

UpsertMemoryEntryTool is always registered and authorizes memory_type='skin'
writes to source='main' even when full mutation is off; WriteDocumentTool and
non-skin writes remain test/explicit-env only.
"""

from __future__ import annotations

from typing import Any

from simpleclaw.tools.base import Tool, ToolResult
from Mojing.dream.tools.common import json_result
from Mojing.storage.memory_repo import MySQLMemory


class UpsertMemoryEntryTool(Tool):
    name = "upsert_memory_entry"
    description = (
        "Create or replace one long-term memory entry after reviewing concrete dream evidence. "
        "Use only when asset_policy.write_assets includes memory_entries."
    )
    parameters = {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Memory source, default main."},
            "topic": {"type": "string", "description": "Stable memory topic to create or replace."},
            "description": {"type": "string", "description": "Short retrieval description."},
            "content": {"type": "string", "description": "Full memory content to persist."},
            "reason": {"type": "string", "description": "Why this direct dream write is justified."},
            "memory_type": {"type": "string", "description": "Memory type: skin or chitchat. Default chitchat."},
        },
        "required": ["topic", "description", "content", "reason"],
    }
    needs_followup = True
    execution_mode = "inline"
    read_only = False
    tool_category = "sync_write"
    risk_level = "medium"
    destructive = False
    concurrency_safe = False
    requires_approval = False
    exposure_scope = "agent"

    def __init__(
        self,
        *,
        db: Any,
        tenant_key: str,
        allowed: bool,
        job_id: str,
        default_source: str = "main",
        skin_apply_allowed: bool = False,
    ) -> None:
        self._db = db
        self._tenant_key = tenant_key
        self._allowed = allowed
        self._job_id = job_id
        self._default_source = str(default_source or "main")
        self._skin_apply_allowed = skin_apply_allowed

    async def execute(
        self,
        *,
        topic: str,
        description: str,
        content: str,
        reason: str,
        source: str = "main",
        memory_type: str = "chitchat",
    ) -> ToolResult:
        mt = str(memory_type or "chitchat").strip() or "chitchat"
        source_text = str(source or self._default_source or "main").strip() or "main"
        # skin 免授权 apply 仅限 source='main'（主 agent 默认注入只读 source='main'），
        # 不放行 subagent ledger 派生的非 main source，避免写进主 agent 看不到的分区。
        skin_ok = mt == "skin" and self._skin_apply_allowed and source_text == "main"
        permitted = self._allowed or skin_ok
        if not permitted:
            return json_result({"ok": False, "error": "dream memory mutation is disabled"}, ok=False)
        topic_text = str(topic or "").strip()
        content_text = str(content or "").strip()
        description_text = str(description or "").strip()
        reason_text = str(reason or "").strip()
        if not topic_text or not content_text or not description_text or not reason_text:
            return json_result({"ok": False, "error": "topic, description, content, and reason are required"}, ok=False)
        memory = MySQLMemory(db=self._db, tenant_key=self._tenant_key, source=source_text)
        await memory.store(
            topic_text,
            content_text,
            description=description_text,
            metadata={"change_source": "dream_subagent", "dream_job_id": self._job_id, "reason": reason_text},
            memory_type=mt,
        )
        return json_result({
            "ok": True,
            "action": "memory_entry_upserted",
            "source": source_text,
            "topic": topic_text,
            "memory_type": mt,
            "dream_job_id": self._job_id,
            "reason": reason_text,
        })


class WriteDocumentTool(Tool):
    name = "write_document"
    description = (
        "Replace USER.md, SOUL.md, or SKIN_DIARY_TODO.md after reviewing concrete dream evidence. "
        "Use only when asset_policy.write_assets includes tenant_documents."
    )
    parameters = {
        "type": "object",
        "properties": {
            "doc_name": {"type": "string", "enum": ["USER.md", "SOUL.md", "SKIN_DIARY_TODO.md"]},
            "content": {"type": "string", "description": "Complete replacement markdown content."},
            "change_summary": {"type": "string", "description": "Short audit summary for the document version."},
            "reason": {"type": "string", "description": "Why this direct dream write is justified."},
        },
        "required": ["doc_name", "content", "change_summary", "reason"],
    }
    needs_followup = True
    execution_mode = "inline"
    read_only = False
    tool_category = "sync_write"
    risk_level = "medium"
    destructive = False
    concurrency_safe = False
    requires_approval = False
    exposure_scope = "agent"

    def __init__(
        self,
        *,
        document_repo: Any,
        tenant_key: str,
        allowed: bool,
        job_id: str,
        session_key: str | None,
        trace_id: str | None,
        message_seq_start: int | None = None,
        message_seq_end: int | None = None,
    ) -> None:
        self._document_repo = document_repo
        self._tenant_key = tenant_key
        self._allowed = allowed
        self._job_id = job_id
        self._session_key = session_key
        self._trace_id = trace_id
        self._message_seq_start = message_seq_start
        self._message_seq_end = message_seq_end

    async def execute(
        self,
        *,
        doc_name: str,
        content: str,
        change_summary: str,
        reason: str,
    ) -> ToolResult:
        if not self._allowed:
            return json_result({"ok": False, "error": "dream document mutation is disabled"}, ok=False)
        doc = str(doc_name or "").strip()
        if doc not in {"USER.md", "SOUL.md", "SKIN_DIARY_TODO.md"}:
            return json_result({"ok": False, "error": "unsupported doc_name", "doc_name": doc}, ok=False)
        content_text = str(content or "").strip()
        summary_text = str(change_summary or "").strip()
        reason_text = str(reason or "").strip()
        if not content_text or not summary_text or not reason_text:
            return json_result({"ok": False, "error": "content, change_summary, and reason are required"}, ok=False)
        await self._document_repo.set(
            self._tenant_key,
            doc,
            content_text,
            change_source="dream_subagent",
            source_task_id=self._job_id,
            session_key=self._session_key,
            trace_id=self._trace_id,
            message_seq_start=self._message_seq_start,
            message_seq_end=self._message_seq_end,
            change_summary=summary_text,
            operator_id="dream_subagent",
        )
        return json_result({
            "ok": True,
            "action": "document_written",
            "doc_name": doc,
            "dream_job_id": self._job_id,
            "change_summary": summary_text,
            "reason": reason_text,
        })
