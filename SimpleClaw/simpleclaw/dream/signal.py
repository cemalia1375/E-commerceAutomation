"""Dream signal protocol.

DreamSignal records the business/runtime state change that may justify dream.
It is provenance, not execution: candidates, jobs, and artifacts still own
admission, execution, and outputs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, TYPE_CHECKING

from simpleclaw.dream.protocol import DreamTrigger, dream_scope_key, make_dream_id, now_ms

if TYPE_CHECKING:
    from simpleclaw.dream.protocol import DreamCandidate


DreamSignalPriority = Literal["low", "normal", "high"]


@dataclass(slots=True)
class DreamSignal:
    """A business/runtime state change that may justify a future dream job."""

    tenant_key: str
    signal_type: str
    reason: str
    session_key: str | None = None
    namespace: str = "default"
    subject_type: str | None = None
    subject_id: str | None = None
    source_type: str | None = None
    source_id: str | None = None
    input_cursor: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    read_assets: list[str] = field(default_factory=list)
    write_assets: list[str] = field(default_factory=list)
    forbidden_assets: list[str] = field(default_factory=list)
    priority: DreamSignalPriority = "low"
    signal_id: str = field(default_factory=lambda: make_dream_id("dreamsig"))
    created_at_ms: int = field(default_factory=now_ms)

    @property
    def scope_key(self) -> str:
        return dream_scope_key(
            tenant_key=self.tenant_key,
            session_key=self.session_key,
            namespace=self.namespace,
        )

    @property
    def dedupe_key(self) -> str:
        subject = self.subject_id or self.source_id or self.input_cursor or self.reason
        return f"{self.scope_key}:{self.signal_type}:{subject}"

    @property
    def merge_key(self) -> str:
        subject = self.subject_type or self.signal_type
        return f"{self.scope_key}:{subject}"

    def to_candidate(self, *, trigger: DreamTrigger = "system_monitor") -> "DreamCandidate":
        from simpleclaw.dream.protocol import DreamCandidate

        payload = dict(self.payload or {})
        payload.setdefault("signal", self.to_dict())
        payload.setdefault("read_assets", list(self.read_assets))
        payload.setdefault("write_assets", list(self.write_assets))
        payload.setdefault("forbidden_assets", list(self.forbidden_assets))
        return DreamCandidate(
            tenant_key=self.tenant_key,
            session_key=self.session_key,
            namespace=self.namespace,
            trigger=trigger,
            reason=self.reason,
            source_id=self.source_id or self.subject_id or self.signal_id,
            input_cursor=self.input_cursor,
            payload=payload,
            created_at_ms=self.created_at_ms,
            updated_at_ms=self.created_at_ms,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
