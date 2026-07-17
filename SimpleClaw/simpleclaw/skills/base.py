"""Skill 基础数据结构与 SKILL.md 解析。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml


SkillMaterialization = Literal["observation", "scene"]


@dataclass(slots=True)
class SkillDescriptor:
    """已发现 skill 的轻量元数据。"""

    name: str
    description: str
    when_to_use: str
    path: Path
    materialization: SkillMaterialization = "observation"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SkillDocument:
    """已解析的 SKILL.md。"""

    descriptor: SkillDescriptor
    body: str


def parse_skill_markdown(path: str | Path) -> SkillDocument:
    """Parse one SKILL.md file with optional YAML frontmatter."""

    target = Path(path)
    raw = target.read_text(encoding="utf-8").strip()
    frontmatter, body = _split_frontmatter(raw)
    data = _normalize_frontmatter(frontmatter)

    name = str(
        data.get("name")
        or data.get("skill")
        or target.parent.name
    ).strip()
    description = str(data.get("description") or "").strip()
    when_to_use = str(data.get("when_to_use") or data.get("when") or "").strip()
    materialization = _normalize_materialization(
        data.get("materialization") or data.get("kind") or data.get("mode")
    )

    descriptor = SkillDescriptor(
        name=name,
        description=description,
        when_to_use=when_to_use,
        path=target,
        materialization=materialization,
        metadata=data,
    )
    return SkillDocument(descriptor=descriptor, body=body.strip())


def _split_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    if not raw.startswith("---\n"):
        return {}, raw

    end = raw.find("\n---\n", 4)
    if end < 0:
        return {}, raw

    fm_text = raw[4:end].strip()
    body = raw[end + 5:]
    if not fm_text:
        return {}, body
    try:
        parsed = yaml.safe_load(fm_text)
    except Exception:
        return {}, raw
    if not isinstance(parsed, dict):
        return {}, body
    return parsed, body


def _normalize_frontmatter(data: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in (data or {}).items():
        if not isinstance(key, str):
            continue
        result[key.strip()] = value
    return result


def _normalize_materialization(raw: Any) -> SkillMaterialization:
    value = str(raw or "").strip().lower()
    if value in {"scene", "scene_overlay", "overlay", "context", "contextual"}:
        return "scene"
    return "observation"
