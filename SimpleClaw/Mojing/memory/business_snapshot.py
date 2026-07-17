"""Business fact snapshot builder for memory ledger records."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class MojingMemoryBusinessSnapshotBuilder:
    """Collect lightweight Mojing business facts around a compressed chunk.

    The builder only records evidence. It does not decide whether memory is
    correct, stale, or missing; that belongs to dream / later governance passes.
    """

    runtime_task_repo: Any | None = None
    document_repo: Any | None = None

    async def build(
        self,
        *,
        tenant_key: str,
        session_key: str,
        source: str,
        source_chunk: list[dict[str, Any]],
        base_snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        base = dict(base_snapshot or {})
        refs = extract_business_refs(source_chunk)
        task_ids = sorted(refs["task_ids"])
        runtime_tasks = await self._runtime_tasks(task_ids)
        document_versions = await self._document_versions(
            tenant_key=tenant_key,
            session_key=session_key,
            task_ids=task_ids,
        )
        return {
            **base,
            "business_refs": {
                "task_ids": task_ids,
                "runtime_task_ids": sorted(refs["runtime_task_ids"]),
                "trace_ids": sorted(refs["trace_ids"]),
                "business_refs": [
                    {"type": ref_type, "id": ref_id}
                    for ref_type, ref_id in sorted(refs["business_refs"])
                ],
                "action_keys": sorted(refs["action_keys"]),
            },
            "runtime_tasks": runtime_tasks,
            "document_versions": document_versions,
        }

    async def _runtime_tasks(self, task_ids: list[str]) -> list[dict[str, Any]]:
        if self.runtime_task_repo is None or not task_ids:
            return []
        rows: list[dict[str, Any]] = []
        for task_id in task_ids[:20]:
            try:
                row = await self.runtime_task_repo.get(task_id)
            except Exception:
                continue
            item = _task_to_dict(row)
            if item:
                rows.append(_compact_runtime_task(item))
        return rows

    async def _document_versions(
        self,
        *,
        tenant_key: str,
        session_key: str,
        task_ids: list[str],
    ) -> list[dict[str, Any]]:
        if self.document_repo is None or not tenant_key:
            return []
        rows: list[dict[str, Any]] = []
        by_source = getattr(self.document_repo, "list_versions_by_source_tasks", None)
        if callable(by_source) and task_ids:
            try:
                found = await by_source(tenant_key=tenant_key, source_task_ids=task_ids, limit=20)
            except Exception:
                found = []
            rows.extend(_compact_document_version(row, relation="source_task_id") for row in found)

        by_session = getattr(self.document_repo, "list_recent_versions_for_session", None)
        if callable(by_session) and session_key:
            try:
                found = await by_session(tenant_key=tenant_key, session_key=session_key, limit=10)
            except Exception:
                found = []
            existing = {
                str(row.get("version_id") or "")
                for row in rows
                if str(row.get("version_id") or "")
            }
            for row in found:
                version_id = str(row.get("version_id") or "")
                if version_id and version_id in existing:
                    continue
                rows.append(_compact_document_version(row, relation="session_recent"))
        return rows


def extract_business_refs(messages: list[dict[str, Any]]) -> dict[str, set]:
    refs = {
        "task_ids": set(),
        "runtime_task_ids": set(),
        "trace_ids": set(),
        "business_refs": set(),
        "action_keys": set(),
    }
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        _collect_refs(msg, refs)
    return refs


def _collect_refs(value: Any, refs: dict[str, set]) -> None:
    if isinstance(value, str):
        parsed = _parse_json(value)
        if parsed is not None:
            _collect_refs(parsed, refs)
        return
    if isinstance(value, list):
        for item in value:
            _collect_refs(item, refs)
        return
    if not isinstance(value, dict):
        return

    task_id = _clean(value.get("task_id"))
    runtime_task_id = _clean(value.get("runtime_task_id"))
    if task_id:
        refs["task_ids"].add(task_id)
    if runtime_task_id:
        refs["runtime_task_ids"].add(runtime_task_id)
        refs["task_ids"].add(runtime_task_id)
    trace_id = _clean(value.get("trace_id"))
    if trace_id:
        refs["trace_ids"].add(trace_id)
    action_key = _clean(value.get("action_key"))
    if action_key:
        refs["action_keys"].add(action_key)

    ref_type = _clean(value.get("business_ref_type"))
    ref_id = _clean(value.get("business_ref_id"))
    if ref_type or ref_id:
        refs["business_refs"].add((ref_type, ref_id))

    for item in value.values():
        _collect_refs(item, refs)


def _compact_runtime_task(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": _clean(task.get("task_id")),
        "task_type": _clean(task.get("task_type")),
        "session_key": _clean(task.get("session_key")),
        "trace_id": _clean(task.get("trace_id")),
        "tool_name": _clean(task.get("tool_name")),
        "status": _clean(task.get("status")),
        "business_ref_type": _clean(task.get("business_ref_type")),
        "business_ref_id": _clean(task.get("business_ref_id")),
        "summary": _clean(task.get("summary"))[:500],
        "error": _clean(task.get("error") or task.get("last_error"))[:500],
        "input_json": task.get("input_json") if isinstance(task.get("input_json"), dict) else task.get("payload"),
        "output_json": task.get("output_json") if isinstance(task.get("output_json"), dict) else task.get("output"),
    }


def _compact_document_version(row: dict[str, Any], *, relation: str) -> dict[str, Any]:
    return {
        "relation": relation,
        "version_id": _clean(row.get("version_id")),
        "doc_name": _clean(row.get("doc_name")),
        "doc_type": _clean(row.get("doc_type")),
        "version_no": row.get("version_no"),
        "content_hash": _clean(row.get("content_hash")),
        "change_source": _clean(row.get("change_source")),
        "source_task_id": _clean(row.get("source_task_id")),
        "session_key": _clean(row.get("session_key")),
        "trace_id": _clean(row.get("trace_id")),
        "message_seq_start": row.get("message_seq_start"),
        "message_seq_end": row.get("message_seq_end"),
        "change_summary": _clean(row.get("change_summary"))[:500],
        "created_at": _clean(row.get("created_at")),
    }


def _task_to_dict(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    if hasattr(row, "to_dict"):
        data = row.to_dict()
        return data if isinstance(data, dict) else None
    return None


def _parse_json(value: str) -> Any | None:
    text = str(value or "").strip()
    if not text or text[0] not in "[{":
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def _clean(value: Any) -> str:
    return str(value or "").strip()
