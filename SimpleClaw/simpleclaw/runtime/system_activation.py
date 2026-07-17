"""System activation protocol.

SystemActivationRequest is the framework-level request object for non-user
events that may open a session turn. Runtime tasks, cron reminders, hooks,
external callbacks, and future dreaming jobs should converge on this protocol
instead of each caller directly running a ReactLoop turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from simpleclaw.runtime.session_ingress_protocol import (
    SessionIngressDeliveryPolicy,
    SessionIngressPreemptPolicy,
    SessionIngressPriority,
)


SystemActivationSourceType = Literal[
    "runtime_task",
    "cron",
    "dream",
    "hook",
    "external_callback",
    "manual",
    "system_monitor",
]


@dataclass(slots=True)
class SystemActivationRequest:
    """A normalized request to proactively activate a session."""

    session_key: str
    tenant_key: str
    activation_kind: str
    summary: str
    reminder_text: str
    task_id: str = ""
    source_type: SystemActivationSourceType = "runtime_task"
    source_id: str | None = None
    source_session_key: str | None = None
    business_ref_type: str | None = None
    business_ref_id: str | None = None
    priority: SessionIngressPriority = "low"
    delivery_policy: SessionIngressDeliveryPolicy = "best_effort"
    preempt_policy: SessionIngressPreemptPolicy = "drop_if_session_busy_or_user_arrives"
    expires_at_ms: int | None = None
    dedupe_key: str | None = None
    persist_completion_event: bool = True
    payload_json: dict[str, Any] = field(default_factory=dict)

    @property
    def effective_source_id(self) -> str:
        return str(self.source_id or self.task_id or "").strip()

    def to_payload(self) -> dict[str, Any]:
        payload = dict(self.payload_json or {})
        payload.update({
            "activation_kind": self.activation_kind,
            "task_id": self.task_id,
            "summary": self.summary,
            "reminder_text": self.reminder_text,
            "source_type": self.source_type,
            "source_id": self.effective_source_id,
            "source_session_key": self.source_session_key,
            "target_session_key": self.session_key,
            "business_ref_type": self.business_ref_type,
            "business_ref_id": self.business_ref_id,
            "dedupe_key": self.dedupe_key,
        })
        return payload
