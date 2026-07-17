"""/admin/lab Memory 监控面板的查询函数。

移植自 script/runner/runner.py 的 _memory_entries / _memory_ledgers /
_dream_artifacts（script/ 是一次性工具区，不直接 import）。entries 额外
带 created_at/updated_at，供前端轮询时 diff 出"变化记录"时间线。
"""

from __future__ import annotations

import json
from typing import Any


def _format_dt(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


async def memory_entries(db: Any, tenant_key: str) -> list[dict[str, Any]]:
    async with db.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT topic, description, content, source, memory_type,
                       created_at, updated_at
                  FROM nb_memory_entries
                 WHERE tenant_key = %s
                 ORDER BY source, topic
                """,
                (tenant_key,),
            )
            rows = await cur.fetchall()
    out: list[dict[str, Any]] = []
    for topic, description, content, source, memory_type, created_at, updated_at in rows:
        out.append({
            "topic": str(topic or ""),
            "description": str(description or ""),
            "content": str(content or ""),
            "source": str(source or ""),
            "memory_type": str(memory_type or "chitchat"),
            "is_skin": str(memory_type or "").strip() == "skin",
            "created_at": _format_dt(created_at),
            "updated_at": _format_dt(updated_at),
        })
    return out


async def memory_ledgers(db: Any, tenant_key: str) -> list[dict[str, Any]]:
    async with db.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT ledger_id, status, dream_status, metadata_json, created_at
                  FROM nb_memory_ledgers
                 WHERE tenant_key = %s
                 ORDER BY created_at ASC
                """,
                (tenant_key,),
            )
            rows = await cur.fetchall()
    out: list[dict[str, Any]] = []
    for ledger_id, status, dream_status, metadata_json, created_at in rows:
        try:
            metadata = json.loads(metadata_json) if metadata_json else {}
        except (TypeError, ValueError):
            metadata = {}
        out.append({
            "ledger_id": str(ledger_id or ""),
            "status": str(status or ""),
            "dream_status": str(dream_status or ""),
            "guardrail": metadata.get("guardrail") if isinstance(metadata, dict) else None,
            "created_at": _format_dt(created_at),
        })
    return out


async def dream_artifacts(db: Any, tenant_key: str) -> list[dict[str, Any]]:
    async with db.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                SELECT artifact_key, status, applied_at, content, created_at
                  FROM nb_subagent_artifacts
                 WHERE tenant_key = %s
                   AND artifact_key LIKE 'memory-ledger:%%'
                 ORDER BY created_at ASC
                """,
                (tenant_key,),
            )
            rows = await cur.fetchall()
    return [
        {
            "artifact_key": str(artifact_key or ""),
            "status": str(status or ""),
            "applied": applied_at is not None,
            "content": str(content or ""),
            "created_at": _format_dt(created_at),
        }
        for artifact_key, status, applied_at, content, created_at in rows
    ]


async def memory_snapshot(container: Any, tenant_key: str) -> dict[str, Any]:
    """组合快照：记忆条目 + ledgers + dream artifacts + USER.md。"""
    user_doc = await container.doc_repo.get(tenant_key, "USER.md")
    return {
        "entries": await memory_entries(container.db, tenant_key),
        "ledgers": await memory_ledgers(container.db, tenant_key),
        "artifacts": await dream_artifacts(container.db, tenant_key),
        "user_doc": user_doc or "",
    }
