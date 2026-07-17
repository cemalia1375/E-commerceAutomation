"""Shared readiness contracts and helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


ACTIVE_STATUSES = {"queued", "running", "wait_external"}
ACTIVE_STATUS_ALIASES = ACTIVE_STATUSES | {
    "wait_external_owned",
    "triggered",
    "external",
    "waiting_external",
}


@dataclass(slots=True)
class CapabilityDecision:
    allowed: bool
    capability: str
    reason: str = ""
    phase: str = ""
    message_focus: str = ""
    facts: dict[str, Any] = field(default_factory=dict)


def status_of(task: dict[str, Any] | None) -> str:
    return normalize_status((task or {}).get("status"))


def normalize_status(value: Any) -> str:
    status = str(value or "").strip().lower()
    if status in ACTIVE_STATUS_ALIASES - ACTIVE_STATUSES:
        return "wait_external"
    if status == "noop":
        return "succeeded"
    return status


def stringify_time(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    text = str(value or "").strip()
    return text or None


def is_after(left: Any, right: Any) -> bool:
    left_dt = parse_time(left)
    right_dt = parse_time(right)
    if left_dt is None or right_dt is None:
        return False
    return left_dt > right_dt


def parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        dt = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                dt = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            try:
                dt = datetime.fromisoformat(text)
            except ValueError:
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
