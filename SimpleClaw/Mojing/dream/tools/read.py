"""Read-only tools for Mojing DreamSubagent."""

from __future__ import annotations

from typing import Any

from simpleclaw.tools.base import Tool, ToolResult
from Mojing.dream.tools.common import json_result, ledger_to_dict
from Mojing.storage.memory_repo import MySQLMemory


class ReadMemoryLedgerTool(Tool):
    name = "read_memory_ledger"
    description = "Read the memory ledger that triggered this dream job, including before/actions/after and business snapshot."
    parameters = {
        "type": "object",
        "properties": {
            "ledger_id": {"type": "string", "description": "Memory ledger id, e.g. memledger_xxx. Defaults to the current dream job source ledger."},
        },
        "required": [],
    }
    needs_followup = True
    execution_mode = "inline"
    read_only = True
    tool_category = "sync_read"
    risk_level = "low"
    exposure_scope = "agent"

    def __init__(self, memory_ledger_repo: Any, *, default_ledger_id: str | None = None) -> None:
        self._memory_ledger_repo = memory_ledger_repo
        self._default_ledger_id = str(default_ledger_id or "").strip()

    async def execute(self, *, ledger_id: str = "") -> ToolResult:
        resolved_ledger_id = str(ledger_id or self._default_ledger_id).strip()
        if not resolved_ledger_id:
            return json_result({"ok": False, "error": "missing ledger_id"}, ok=False)
        ledger = await self._memory_ledger_repo.get_ledger(resolved_ledger_id)
        if ledger is None:
            return json_result({"ok": False, "error": "memory ledger not found", "ledger_id": resolved_ledger_id}, ok=False)
        return json_result({"ok": True, "ledger": ledger_to_dict(ledger)})


class ReadSessionMessagesTool(Tool):
    name = "read_session_messages"
    description = "Read a bounded range of persisted session messages for evidence review."
    parameters = {
        "type": "object",
        "properties": {
            "session_key": {"type": "string"},
            "start": {"type": "integer", "description": "Inclusive message sequence start."},
            "end": {"type": "integer", "description": "Inclusive message sequence end."},
        },
        "required": ["session_key"],
    }
    needs_followup = True
    execution_mode = "inline"
    read_only = True
    tool_category = "sync_read"
    risk_level = "low"
    exposure_scope = "agent"

    def __init__(self, *, session_repo: Any, tenant_key: str) -> None:
        self._session_repo = session_repo
        self._tenant_key = tenant_key

    async def execute(self, *, session_key: str, start: int | None = None, end: int | None = None) -> ToolResult:
        messages, last_consolidated = await self._session_repo.load_messages(self._tenant_key, session_key)
        start_idx = max(0, int(start or 0))
        end_idx = len(messages) - 1 if end is None else min(len(messages) - 1, max(start_idx, int(end)))
        selected = [] if end_idx < start_idx else messages[start_idx:end_idx + 1]
        return json_result({
            "ok": True,
            "session_key": session_key,
            "last_consolidated": last_consolidated,
            "start": start_idx,
            "end": end_idx,
            "messages": selected[:80],
            "truncated": len(selected) > 80,
        })


class ReadMemoryEntriesTool(Tool):
    name = "read_memory_entries"
    description = "Read current long-term memory entries for the user. Default source is main."
    parameters = {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Memory source, default main."},
            "top_k": {"type": "integer", "description": "Maximum entries to read."},
        },
        "required": [],
    }
    needs_followup = True
    execution_mode = "inline"
    read_only = True
    tool_category = "sync_read"
    risk_level = "low"
    exposure_scope = "agent"

    def __init__(self, *, db: Any, tenant_key: str) -> None:
        self._db = db
        self._tenant_key = tenant_key

    async def execute(self, *, source: str = "main", top_k: int = 20) -> ToolResult:
        memory = MySQLMemory(db=self._db, tenant_key=self._tenant_key, source=str(source or "main"))
        items = await memory.retrieve(top_k=max(1, min(int(top_k or 20), 50)))
        return json_result({
            "ok": True,
            "source": source or "main",
            "items": [
                {"topic": item.key, "description": item.description, "content": item.content}
                for item in items
            ],
        })


class ReadDocumentTool(Tool):
    name = "read_document"
    description = "Read current USER.md, SOUL.md, or other tenant document."
    parameters = {
        "type": "object",
        "properties": {
            "doc_name": {"type": "string", "enum": ["USER.md", "SOUL.md", "SKIN_DIARY_TODO.md"]},
        },
        "required": ["doc_name"],
    }
    needs_followup = True
    execution_mode = "inline"
    read_only = True
    tool_category = "sync_read"
    risk_level = "low"
    exposure_scope = "agent"

    def __init__(self, *, document_repo: Any, tenant_key: str) -> None:
        self._document_repo = document_repo
        self._tenant_key = tenant_key

    async def execute(self, *, doc_name: str) -> ToolResult:
        content = await self._document_repo.get(self._tenant_key, doc_name)
        metadata = await self._document_repo.get_metadata(self._tenant_key, doc_name)
        return json_result({
            "ok": True,
            "doc_name": doc_name,
            "metadata": metadata or {},
            "content": content or "",
        })


class ReadDocumentVersionsTool(Tool):
    name = "read_document_versions"
    description = "Read recent document versions associated with a session."
    parameters = {
        "type": "object",
        "properties": {
            "session_key": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["session_key"],
    }
    needs_followup = True
    execution_mode = "inline"
    read_only = True
    tool_category = "sync_read"
    risk_level = "low"
    exposure_scope = "agent"

    def __init__(self, *, document_repo: Any, tenant_key: str) -> None:
        self._document_repo = document_repo
        self._tenant_key = tenant_key

    async def execute(self, *, session_key: str, limit: int = 10) -> ToolResult:
        versions = await self._document_repo.list_recent_versions_for_session(
            tenant_key=self._tenant_key,
            session_key=session_key,
            limit=limit,
        )
        return json_result({"ok": True, "session_key": session_key, "versions": versions})


class ReadRuntimeTasksTool(Tool):
    name = "read_runtime_tasks"
    description = "Read recent runtime tasks for the tenant to understand business facts."
    parameters = {
        "type": "object",
        "properties": {
            "limit": {"type": "integer"},
        },
        "required": [],
    }
    needs_followup = True
    execution_mode = "inline"
    read_only = True
    tool_category = "sync_read"
    risk_level = "low"
    exposure_scope = "agent"

    def __init__(self, *, runtime_task_repo: Any, tenant_key: str) -> None:
        self._runtime_task_repo = runtime_task_repo
        self._tenant_key = tenant_key

    async def execute(self, *, limit: int = 20) -> ToolResult:
        tasks = await self._runtime_task_repo.list_recent(
            tenant_key=self._tenant_key,
            limit=limit,
        )
        compact = [
            {
                "task_id": task.get("task_id"),
                "task_type": task.get("task_type"),
                "status": task.get("status"),
                "session_key": task.get("session_key"),
                "summary": task.get("summary"),
                "business_ref_type": task.get("business_ref_type"),
                "business_ref_id": task.get("business_ref_id"),
                "created_at": task.get("created_at"),
                "updated_at": task.get("updated_at"),
                "completed_at": task.get("completed_at"),
            }
            for task in tasks
        ]
        return json_result({"ok": True, "tasks": compact})
