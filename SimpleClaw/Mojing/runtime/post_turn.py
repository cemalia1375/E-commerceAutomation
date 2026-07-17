"""Post-turn runtime task enqueue helpers."""

from __future__ import annotations

import asyncio

from loguru import logger

from simpleclaw.runtime.task_protocol import TaskEnvelope
from Mojing.runtime.streams import MojingTaskStream


async def enqueue_post_turn_tasks(
    c,
    *,
    tenant_key: str,
    session_key: str,
    user_message: str,
    assistant_reply: str,
    first_token_reply: str = "",
    main_assistant_reply: str = "",
    media: list[str] | None = None,
    tool_calls: list[dict] | None = None,
    tool_results: list[dict] | None = None,
    tool_invocations: list[dict] | None = None,
    runtime_tasks: list[dict] | None = None,
) -> None:
    """Queue postprocess and obligation extraction for a completed main turn."""
    payload = {
        "tenant_key": tenant_key,
        "session_key": session_key,
        "user_message": user_message,
        "assistant_reply": assistant_reply,
        "first_token_reply": first_token_reply,
        "main_assistant_reply": main_assistant_reply or assistant_reply,
        "media": list(media or []),
        "tool_calls": list(tool_calls or []),
        "tool_results": list(tool_results or []),
        "tool_invocations": list(tool_invocations or []),
        "runtime_tasks": list(runtime_tasks or []),
    }
    tasks = [
        TaskEnvelope(
            task_type="postprocess",
            payload=payload,
            stream=MojingTaskStream.POSTPROCESS,
            tenant_key=tenant_key,
            session_key=session_key,
            scope_key=f"postprocess:{tenant_key}:USER.md",
            service_role="mojing:post-turn",
        ),
        TaskEnvelope(
            task_type="obligation_extract",
            payload=payload,
            stream=MojingTaskStream.OBLIGATION_EXTRACT,
            tenant_key=tenant_key,
            session_key=session_key,
            scope_key=f"obligation_extract:{tenant_key}",
            service_role="mojing:post-turn",
        ),
    ]

    results = await asyncio.gather(
        *[c.runtime.submit_task(t) for t in tasks],
        return_exceptions=True,
    )
    for task, result in zip(tasks, results):
        if isinstance(result, Exception):
            logger.warning(
                "post-turn enqueue failed: type={} tenant={} session={} err={}",
                task.task_type, tenant_key, session_key, result,
            )
        else:
            logger.info(
                "post-turn queued: type={} tenant={} session={} queue_id={}",
                task.task_type, tenant_key, session_key, result,
            )
