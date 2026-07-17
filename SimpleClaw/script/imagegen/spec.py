"""Load the admin lab image generation defaults."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

SPEC_PATH = Path(__file__).with_name("days.yaml")
OUT_DIR = Path(__file__).with_name("out")


@dataclass(frozen=True)
class DaySpec:
    day: int
    skin_state: str


@dataclass(frozen=True)
class ImagegenSpec:
    image_model: str
    size: str
    persona: str
    days: list[DaySpec]


def load_spec(path: Path = SPEC_PATH) -> ImagegenSpec:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    days_raw = raw.get("days") or []
    days = [
        DaySpec(day=int(item["day"]), skin_state=str(item.get("skin_state") or ""))
        for item in days_raw
    ]
    if not days:
        raise ValueError(f"{path} must define at least one image generation day")
    return ImagegenSpec(
        image_model=str(raw.get("image_model") or "doubao-seedream-3-0-t2i-250415"),
        size=str(raw.get("size") or "1536x2048"),
        persona=str(raw.get("persona") or ""),
        days=days,
    )
