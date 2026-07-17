"""Helpers for waking the cross-episode highlight batch orchestrator."""
from __future__ import annotations

import asyncio
import json
import logging

from simpleclaw.runtime.task_protocol import TaskEnvelope

from Flowcut.runtime.streams import FlowcutTaskStream

logger = logging.getLogger(__name__)


async def wake_highlight_batch(
    *,
    runtime,
    batch_id: str,
    tenant_key: str,
    session_key: str = "highlight_plan",
) -> bool:
    """Queue a continuation after a child stage reaches a terminal state."""
    for attempt in range(3):
        try:
            await runtime.submit_task(
                TaskEnvelope(
                    task_type="highlight_batch",
                    payload={
                        "batch_id": batch_id,
                        "tenant_key": tenant_key,
                        "session_key": session_key,
                    },
                    stream=FlowcutTaskStream.HIGHLIGHT_BATCH,
                    tenant_key=tenant_key,
                    session_key=session_key,
                    scope_key=batch_id,
                ),
                tool_name="highlight_batch",
                summary=f"continue batch {batch_id}",
            )
            return True
        except Exception as exc:
            if attempt == 2:
                logger.error(
                    "highlight continuation enqueue failed batch=%s attempts=3: %s",
                    batch_id,
                    exc,
                )
                return False
            await asyncio.sleep(0.25 * (2 ** attempt))
    return False


async def recover_active_highlight_batches(*, runtime, highlight_batch_repo) -> int:
    """Wake non-terminal business batches after a service restart."""
    recovered = 0
    for batch in await highlight_batch_repo.list_all_active():
        state = batch.get("orchestrator_state_json") or {}
        if isinstance(state, str):
            try:
                state = json.loads(state)
            except json.JSONDecodeError:
                state = {}
        if not isinstance(state, dict):
            state = {}
        ok = await wake_highlight_batch(
            runtime=runtime,
            batch_id=str(batch["batch_id"]),
            tenant_key=str(batch.get("tenant_key") or "flowcut"),
            session_key=str(state.get("session_key") or "highlight_plan"),
        )
        recovered += int(ok)
    return recovered
