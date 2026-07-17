"""Compact per-turn facts for post-turn workers."""

from __future__ import annotations

from loguru import logger

from simpleclaw.core.messages import AssistantMessage, ToolResultMessage


async def collect_turn_facts(
    c,
    *,
    tenant_key: str,
    session_key: str,
    since_ms: int,
    messages: list,
    tool_result_events: list[dict] | None = None,
) -> dict[str, list[dict]]:
    """Collect message, tool invocation, and runtime task facts for one turn."""
    message_tool_calls, message_tool_results = extract_message_tool_facts(messages)
    return {
        "tool_calls": message_tool_calls,
        "tool_results": [*message_tool_results, *list(tool_result_events or [])],
        "tool_invocations": await collect_tool_invocation_facts(
            c,
            tenant_key=tenant_key,
            session_key=session_key,
            since_ms=since_ms,
        ),
        "runtime_tasks": await collect_runtime_task_facts(
            c,
            tenant_key=tenant_key,
            session_key=session_key,
            since_ms=since_ms,
        ),
    }


def extract_message_tool_facts(messages: list) -> tuple[list[dict], list[dict]]:
    tool_calls: list[dict] = []
    tool_results: list[dict] = []
    for index, msg in enumerate(messages):
        if isinstance(msg, AssistantMessage):
            for call in msg.tool_calls or []:
                tool_calls.append({
                    "message_index": index,
                    "tool_call_id": call.id,
                    "tool_name": call.name,
                    "arguments": dict(call.arguments or {}),
                    "source": "message_history",
                })
        elif isinstance(msg, ToolResultMessage):
            tool_results.append({
                "message_index": index,
                "tool_call_id": msg.call_id,
                "result": msg.content,
                "source": "message_history",
            })
    return tool_calls, tool_results


async def collect_tool_invocation_facts(
    c,
    *,
    tenant_key: str,
    session_key: str,
    since_ms: int,
) -> list[dict]:
    repo = getattr(c, "tool_invocation_repo", None)
    finder = getattr(repo, "list_recent_for_session", None)
    if finder is None:
        return []
    try:
        rows = await finder(
            tenant_key=tenant_key,
            session_key=session_key,
            since_ms=max(0, int(since_ms) - 1000),
            limit=20,
        )
    except Exception as exc:
        logger.warning(
            "collect tool invocation facts failed: tenant={} session={} err={}",
            tenant_key, session_key, exc,
        )
        return []
    return [compact_tool_invocation(row) for row in rows]


async def collect_runtime_task_facts(
    c,
    *,
    tenant_key: str,
    session_key: str,
    since_ms: int,
) -> list[dict]:
    repo = getattr(c, "runtime_task_repo", None)
    finder = getattr(repo, "list_recent_updates", None)
    if finder is None:
        return []
    try:
        rows = await finder(
            tenant_key=tenant_key,
            session_key=session_key,
            since_ms=max(0, int(since_ms) - 1000),
            limit=30,
        )
    except Exception as exc:
        logger.warning(
            "collect runtime task facts failed: tenant={} session={} err={}",
            tenant_key, session_key, exc,
        )
        return []
    result: list[dict] = []
    for row in rows:
        data = row.to_dict() if hasattr(row, "to_dict") else dict(row or {})
        result.append(compact_runtime_task(data))
    return result


def compact_tool_invocation(row: dict) -> dict:
    return {
        "invocation_id": str(row.get("invocation_id") or ""),
        "session_key": str(row.get("session_key") or ""),
        "tool_call_id": str(row.get("tool_call_id") or ""),
        "tool_name": str(row.get("tool_name") or ""),
        "execution_mode": str(row.get("execution_mode") or ""),
        "status": str(row.get("status") or ""),
        "input_json": row.get("input_json") if isinstance(row.get("input_json"), dict) else None,
        "output_summary": str(row.get("output_summary") or "")[:1000],
        "runtime_task_id": str(row.get("runtime_task_id") or ""),
        "business_ref_type": str(row.get("business_ref_type") or ""),
        "business_ref_id": str(row.get("business_ref_id") or ""),
        "last_error": str(row.get("last_error") or "")[:500],
    }


def compact_runtime_task(row: dict) -> dict:
    return {
        "task_id": str(row.get("task_id") or ""),
        "task_type": str(row.get("task_type") or ""),
        "session_key": str(row.get("session_key") or ""),
        "tool_name": str(row.get("tool_name") or ""),
        "status": str(row.get("status") or ""),
        "business_ref_type": str(row.get("business_ref_type") or ""),
        "business_ref_id": str(row.get("business_ref_id") or ""),
        "summary": str(row.get("summary") or "")[:500],
        "error": str(row.get("error") or row.get("last_error") or "")[:500],
        "input_json": row.get("input_json") if isinstance(row.get("input_json"), dict) else None,
        "output_json": row.get("output_json") if isinstance(row.get("output_json"), dict) else None,
    }
