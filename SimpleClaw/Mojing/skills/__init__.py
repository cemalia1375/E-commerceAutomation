from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Iterable

from simpleclaw.skills import SkillRegistry
import yaml

_SKILLS_ROOT = Path(__file__).parent

_MAIN_AGENT_VISIBLE_SKILLS = frozenset({
    "deep_report.offer",
    "device_connection_help",
    "skin_diary.offer_refresh",
    "skincare_cabinet",
})
_SKIN_DIARY_VISIBLE_SKILLS = frozenset({
    "skin_diary.current_concern_review",
})
_DEEP_REPORT_VISIBLE_SKILLS = frozenset()


class FilteredSkillRegistry:
    """A filtered view over the shared Mojing skill registry."""

    def __init__(self, base: SkillRegistry, visible_names: Iterable[str]) -> None:
        self._base = base
        self._visible_names = frozenset(str(name or "").strip() for name in visible_names if str(name or "").strip())

    @property
    def roots(self) -> list[Path]:
        return self._base.roots

    @property
    def skill_names(self) -> list[str]:
        return sorted(name for name in self._base.skill_names if name in self._visible_names)

    def descriptors(self):
        return [desc for desc in self._base.descriptors() if desc.name in self._visible_names]

    def get(self, name: str):
        skill_name = str(name or "").strip()
        if skill_name not in self._visible_names:
            return None
        return self._base.get(skill_name)

    def load(self, name: str):
        skill_name = str(name or "").strip()
        if skill_name not in self._visible_names:
            return None
        return self._base.load(skill_name)

    def require(self, name: str):
        skill_name = str(name or "").strip()
        if skill_name not in self._visible_names:
            available = ", ".join(self.skill_names) or "(none)"
            raise KeyError(f"Unknown skill '{skill_name}'. Available: {available}")
        return self._base.require(skill_name)

    def render_index(self) -> str:
        descriptors = self.descriptors()
        if not descriptors:
            return ""
        payload = {
            "skills": [
                {
                    "name": desc.name,
                    "description": desc.description or "未提供描述。",
                    "when_to_use": desc.when_to_use or "当当前场景明确匹配该 skill 时使用。",
                    "materialization": desc.materialization,
                }
                for desc in descriptors
            ]
        }
        yaml_text = yaml.safe_dump(
            payload,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        ).strip()
        return (
            "## Skills Index\n"
            "以下是当前可按需读取的本地 SKILL.md 元数据。只有当当前场景明确需要时，才应读取对应 skill。\n"
            "```yaml\n"
            f"{yaml_text}\n"
            "```"
        ).strip()

    def render_skill_body(self, name: str) -> str:
        self.require(name)
        return self._base.render_skill_body(name)


@lru_cache(maxsize=1)
def get_skill_registry() -> SkillRegistry:
    """Return the shared Mojing skill registry.

    The registry is discovered once at process startup usage time and then
    reused by main agent and subagents so skill metadata/body stay consistent
    across lanes.
    """
    return SkillRegistry([_SKILLS_ROOT])


@lru_cache(maxsize=1)
def get_main_skill_registry() -> FilteredSkillRegistry:
    return FilteredSkillRegistry(get_skill_registry(), _MAIN_AGENT_VISIBLE_SKILLS)


@lru_cache(maxsize=1)
def get_skin_diary_skill_registry() -> FilteredSkillRegistry:
    return FilteredSkillRegistry(get_skill_registry(), _SKIN_DIARY_VISIBLE_SKILLS)


@lru_cache(maxsize=1)
def get_deep_report_skill_registry() -> FilteredSkillRegistry:
    return FilteredSkillRegistry(get_skill_registry(), _DEEP_REPORT_VISIBLE_SKILLS)


__all__ = [
    "FilteredSkillRegistry",
    "get_skill_registry",
    "get_main_skill_registry",
    "get_skin_diary_skill_registry",
    "get_deep_report_skill_registry",
]
