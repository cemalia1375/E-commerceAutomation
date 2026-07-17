"""SkillRegistry — 管理本地 SKILL.md 的发现、索引和正文加载。"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

import yaml

from simpleclaw.skills.base import SkillDescriptor, SkillDocument, parse_skill_markdown


class SkillRegistry:
    """本地技能注册表。

    设计目标：
    - 元数据可轻量暴露给模型
    - 正文按需读取
    - scene skill 与 observation skill 共享发现机制
    """

    def __init__(self, roots: Iterable[str | Path] | None = None) -> None:
        self._roots = [Path(root) for root in (roots or [])]
        self._documents: dict[str, SkillDocument] = {}
        if self._roots:
            self.discover()

    @property
    def roots(self) -> list[Path]:
        return list(self._roots)

    @property
    def skill_names(self) -> list[str]:
        return sorted(self._documents)

    def descriptors(self) -> list[SkillDescriptor]:
        return [self._documents[name].descriptor for name in self.skill_names]

    def discover(self) -> None:
        for root in self._roots:
            if not root.exists():
                continue
            for path in root.rglob("SKILL.md"):
                self.register_document(parse_skill_markdown(path))

    def register_document(self, document: SkillDocument) -> None:
        name = document.descriptor.name.strip()
        if not name:
            raise ValueError(f"Invalid skill name from {document.descriptor.path}")
        self._documents[name] = document

    def get(self, name: str) -> SkillDescriptor | None:
        document = self._documents.get(str(name or "").strip())
        return document.descriptor if document is not None else None

    def load(self, name: str) -> SkillDocument | None:
        return self._documents.get(str(name or "").strip())

    def require(self, name: str) -> SkillDocument:
        document = self.load(name)
        if document is None:
            available = ", ".join(self.skill_names) or "(none)"
            raise KeyError(f"Unknown skill '{name}'. Available: {available}")
        return document

    def skill_dir(self, name: str) -> Path:
        """Return the directory containing the SKILL.md for the named skill."""
        return self.require(name).descriptor.path.parent

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
        document = self.require(name)
        body = document.body.strip()
        if not body:
            return ""
        return (
            f"## 当前已激活 Skill：{document.descriptor.name}\n"
            f"路径：{document.descriptor.path}\n\n"
            f"{body}"
        ).strip()
