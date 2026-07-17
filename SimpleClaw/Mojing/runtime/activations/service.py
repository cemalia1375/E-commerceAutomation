from __future__ import annotations

from typing import Awaitable, Callable

from Mojing.runtime.activations.models import ActivationRequest


class RuntimeActivationService:
    def __init__(
        self,
        *,
        enqueue_fn: Callable[..., Awaitable[str | None]],
    ) -> None:
        self._enqueue_fn = enqueue_fn

    async def enqueue(self, request: ActivationRequest) -> str | None:
        return await self._enqueue_fn(
            session_key=request.session_key,
            tenant_key=request.tenant_key,
            activation_kind=request.activation_kind,
            task_id=request.task_id,
            summary=request.summary,
            reminder_text=request.reminder_text,
            source_type=request.source_type,
            source_id=request.source_id,
            source_session_key=request.source_session_key,
            business_ref_type=request.business_ref_type,
            business_ref_id=request.business_ref_id,
            priority=request.priority,
            delivery_policy=request.delivery_policy,
            preempt_policy=request.preempt_policy,
            expires_at_ms=request.expires_at_ms,
            dedupe_key=request.dedupe_key,
            persist_completion_event=request.persist_completion_event,
            payload_json=request.payload_json,
        )
