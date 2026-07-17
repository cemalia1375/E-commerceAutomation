from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from simpleclaw.context.builder import ContextBuilder
from simpleclaw.skills import SkillRegistry
from simpleclaw.tools.builtin.skill import ReadSkillAssetTool


SKILL_BODY = """---
name: demo
description: demo skill
when_to_use: when needed
materialization: scene
---

# Demo

workflows/main.yaml 是入口剧本。
"""

WORKFLOW_BODY = "step: 1\naction: noop\n"
ACTION_JS_BODY = "(args) => console.log(args);\n"


@pytest.mark.unit
class ReadSkillAssetToolTest(unittest.IsolatedAsyncioTestCase):
    def _build(self, tmp_root: Path) -> ReadSkillAssetTool:
        skill_dir = tmp_root / "demo"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(SKILL_BODY, encoding="utf-8")
        (skill_dir / "workflows").mkdir()
        (skill_dir / "workflows" / "main.yaml").write_text(WORKFLOW_BODY, encoding="utf-8")
        (skill_dir / "scripts").mkdir()
        (skill_dir / "scripts" / "action.js").write_text(ACTION_JS_BODY, encoding="utf-8")
        (tmp_root / "outside.txt").write_text("secret\n", encoding="utf-8")

        registry = SkillRegistry([tmp_root])
        builder = ContextBuilder(stable_sections=[], skill_registry=registry)
        tool = ReadSkillAssetTool()
        tool.set_context(context_builder=builder)
        return tool

    async def test_reads_sibling_yaml(self) -> None:
        with TemporaryDirectory() as tmp:
            tool = self._build(Path(tmp))
            result = await tool.execute(skill_name="demo", path="workflows/main.yaml")
            self.assertTrue(result.ok)
            self.assertIn("step: 1", result.content)
            self.assertIn("skill=demo", result.content)
            self.assertEqual(result.metadata["skill_name"], "demo")
            self.assertEqual(result.metadata["path"], "workflows/main.yaml")

    async def test_reads_nested_action_js(self) -> None:
        with TemporaryDirectory() as tmp:
            tool = self._build(Path(tmp))
            result = await tool.execute(skill_name="demo", path="scripts/action.js")
            self.assertTrue(result.ok)
            self.assertIn("console.log", result.content)

    async def test_rejects_path_escape(self) -> None:
        with TemporaryDirectory() as tmp:
            tool = self._build(Path(tmp))
            result = await tool.execute(skill_name="demo", path="../outside.txt")
            self.assertFalse(result.ok)
            self.assertIn("escape", result.content.lower())

    async def test_unknown_skill(self) -> None:
        with TemporaryDirectory() as tmp:
            tool = self._build(Path(tmp))
            result = await tool.execute(skill_name="nonexistent", path="x.yaml")
            self.assertFalse(result.ok)
            self.assertIn("Unknown skill", result.content)

    async def test_missing_file(self) -> None:
        with TemporaryDirectory() as tmp:
            tool = self._build(Path(tmp))
            result = await tool.execute(skill_name="demo", path="workflows/missing.yaml")
            self.assertFalse(result.ok)
            self.assertIn("not found", result.content.lower())

    async def test_directory_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            tool = self._build(Path(tmp))
            result = await tool.execute(skill_name="demo", path="workflows")
            self.assertFalse(result.ok)
            self.assertIn("directory", result.content.lower())

    async def test_empty_inputs(self) -> None:
        with TemporaryDirectory() as tmp:
            tool = self._build(Path(tmp))
            self.assertFalse((await tool.execute(skill_name="", path="x")).ok)
            self.assertFalse((await tool.execute(skill_name="demo", path="")).ok)

    async def test_missing_registry(self) -> None:
        builder = ContextBuilder(stable_sections=[], skill_registry=None)
        tool = ReadSkillAssetTool()
        tool.set_context(context_builder=builder)
        result = await tool.execute(skill_name="demo", path="x")
        self.assertFalse(result.ok)
        self.assertIn("skill registry", result.content)
