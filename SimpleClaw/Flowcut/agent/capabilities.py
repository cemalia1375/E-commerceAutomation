"""Per-turn capability profile for FlowCut agents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AgentCapabilities:
    """Business capability switches for a single main-agent turn."""

    device_enabled: bool = False


def capabilities_from_device_context(
    *,
    device_id: Any = None,
    device_code: Any = None,
) -> AgentCapabilities:
    """Enable hardware control only when the current request carries device identity."""
    return AgentCapabilities(
        device_enabled=_has_value(device_id) or _has_value(device_code),
    )


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    return bool(str(value).strip())
