from __future__ import annotations

import unittest

from Mojing.skills import (
    get_deep_report_skill_registry,
    get_main_skill_registry,
    get_skin_diary_skill_registry,
)


class SkillRegistryViewsTest(unittest.TestCase):
    def test_main_registry_hides_skin_diary_objection_skill(self) -> None:
        registry = get_main_skill_registry()

        self.assertIn("deep_report.offer", registry.skill_names)
        self.assertIn("skin_diary.offer_refresh", registry.skill_names)
        self.assertNotIn("skin_diary.first_entry", registry.skill_names)
        self.assertNotIn("skin_diary_objection_recheck", registry.skill_names)
        self.assertNotIn("skin_diary_objection_recheck", registry.render_index())
        with self.assertRaises(KeyError):
            registry.require("skin_diary_objection_recheck")
        with self.assertRaises(KeyError):
            registry.require("skin_diary.first_entry")

    def test_main_registry_renders_skincare_cabinet_metadata(self) -> None:
        registry = get_main_skill_registry()
        index = registry.render_index()

        self.assertIn("name: skincare_cabinet", index)
        self.assertIn("description: 处理护肤品识别、护肤柜查询和录入相关场景。", index)
        self.assertIn(
            "when_to_use: 当当前轮涉及护肤品或化妆品图片、产品查询、和用户自己产品相关的成分功效问题，或护肤柜录入流程时使用。",
            index,
        )
        self.assertIn("materialization: scene", index)

    def test_main_registry_renders_deep_report_offer_metadata(self) -> None:
        registry = get_main_skill_registry()
        index = registry.render_index()

        self.assertIn("name: deep_report.offer", index)
        self.assertIn("description: 主 Agent 在探索期对是否进入深度分析报告场景的邀请、确认与触发协议。", index)
        self.assertIn("materialization: scene", index)

    def test_skin_diary_registry_only_exposes_objection_skill(self) -> None:
        registry = get_skin_diary_skill_registry()

        self.assertEqual(registry.skill_names, ["skin_diary_objection_recheck"])
        self.assertIn("skin_diary_objection_recheck", registry.render_index())
        self.assertIsNotNone(registry.require("skin_diary_objection_recheck"))
        with self.assertRaises(KeyError):
            registry.require("skin_diary.offer_refresh")

    def test_deep_report_registry_is_empty(self) -> None:
        registry = get_deep_report_skill_registry()

        self.assertEqual(registry.skill_names, [])
        self.assertEqual(registry.render_index(), "")
