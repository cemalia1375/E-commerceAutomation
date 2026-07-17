"""Per-turn capability profile for Mojing agents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AgentCapabilities:
    """Business capability switches for a single main-agent turn."""

    device_enabled: bool = False
    capture_photo_enabled: bool = True
    prompt_surface: str = "app"


def capabilities_from_device_context(
    *,
    device_id: Any = None,
    device_code: Any = None,
    prompt_surface: str = "app",
    capture_photo_enabled: bool = True,
) -> AgentCapabilities:
    """Build per-turn capability profile from request entry context."""
    surface = str(prompt_surface or "app").strip().lower()
    if surface not in {"app", "device"}:
        surface = "app"
    return AgentCapabilities(
        device_enabled=_has_value(device_id) or _has_value(device_code),
        capture_photo_enabled=bool(capture_photo_enabled),
        prompt_surface=surface,
    )


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    return bool(str(value).strip())
