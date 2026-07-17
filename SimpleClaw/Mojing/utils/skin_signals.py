"""Helpers for reading the canonical skin analysis signal payload."""

from __future__ import annotations

from typing import Any


def signal_label(signal: dict[str, Any]) -> str:
    """Return the user-facing signal label from the canonical `name` field."""

    return _text(signal.get("name"))


def signal_location_text(signal: dict[str, Any]) -> str:
    """Return a readable location string for profile summaries."""

    return "·".join(signal_regions(signal))


def signal_regions(signal: dict[str, Any]) -> list[str]:
    """Return raw region names as a list."""

    value = signal.get("regions")
    if not isinstance(value, list):
        return []
    return [_text(item) for item in value if _text(item)]


def signal_care_suggestions(signal: dict[str, Any]) -> list[str]:
    """Return care suggestions from the canonical `careSuggestions` field."""

    value = signal.get("careSuggestions")
    if not isinstance(value, list):
        return []
    return [_text(item) for item in value if _text(item)]


def signal_severity(signal: dict[str, Any]) -> str:
    """Normalize signal severity into the local light/heavy vocabulary."""

    raw = _text(signal.get("severity"))
    if raw == "重度" or raw == "中度":
        return "重度"
    if raw == "轻度":
        return "轻度"
    return ""


def severity_rank(severity: str) -> int:
    if severity == "重度":
        return 2
    if severity == "轻度":
        return 1
    return 0


def _text(value: Any) -> str:
    return str(value or "").strip()
