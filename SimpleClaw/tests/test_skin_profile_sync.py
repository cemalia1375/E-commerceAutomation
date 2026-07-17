"""Tests for syncing image-derived skin profile into USER.md."""

from __future__ import annotations

import unittest

from Mojing.agent.skin_profile_sync import (
    extract_existing_block,
    extract_skin_summary,
    merge_skin_section,
    render_skin_block,
)


class SkinProfileSyncSectionTest(unittest.TestCase):
    def test_empty_learned_skin_profile_heading_is_replaced_not_duplicated(self) -> None:
        existing = """## skin

（无内容）

## Learned Skin Profile"""

        updated = merge_skin_section(existing, "- 肤质：混合性皮肤")

        self.assertEqual(updated.count("## Learned Skin Profile"), 1)
        self.assertIn("## Learned Skin Profile\n\n- 肤质：混合性皮肤", updated)

    def test_extract_existing_block_accepts_empty_heading(self) -> None:
        existing = """## skin

（无内容）

## Learned Skin Profile"""

        self.assertEqual(extract_existing_block(existing), "")

    def test_extract_skin_summary_includes_signal_severity(self) -> None:
        row = {
            "skin_attribute_json": {
                "stage": {"name": "年轻肌"},
                "toneType": {"name": "暖调二白"},
                "oilType": {"name": "混合性皮肤"},
            },
            "signals_json": [
                {
                    "code": "papules",
                    "name": "丘疹",
                    "severity": "轻度",
                    "regions": ["额头"],
                    "careSuggestions": ["局部抗炎抑菌"],
                },
                {
                    "code": "pih_marks",
                    "name": "PIH黑痘印",
                    "severity": "重度",
                    "regions": ["额头", "左面颊", "右面颊"],
                    "careSuggestions": ["防晒+淡印"],
                },
            ],
            "advantages_json": ["胶原充足"],
            "overall_state": "年轻底子好，重点控油淡印",
            "created_at": "2026-05-22 12:00:00",
        }

        summary = extract_skin_summary(row, parse_json=lambda raw: raw)
        block = render_skin_block(summary)

        self.assertIn("- 主要肤况：丘疹（轻度）、PIH黑痘印（重度）", block)
        self.assertIn("- 问题分布：额头：丘疹（轻度）；额头·左面颊·右面颊：PIH黑痘印（重度）", block)

    def test_extract_skin_summary_uses_highest_severity_for_duplicate_signal(self) -> None:
        row = {
            "skin_attribute_json": {},
            "signals_json": [
                {"code": "blackheads", "name": "黑头", "severity": "轻度", "regions": ["鼻子"]},
                {"code": "blackheads", "name": "黑头", "severity": "重度", "regions": ["额头"]},
            ],
            "advantages_json": [],
            "overall_state": "",
            "created_at": None,
        }

        summary = extract_skin_summary(row, parse_json=lambda raw: raw)

        self.assertEqual(summary["skin_concern"], "黑头（重度）")
        self.assertEqual(summary["skin_concern_distribution"], "鼻子：黑头（轻度）；额头：黑头（重度）")

    def test_extract_skin_summary_accepts_current_signal_payload_shape(self) -> None:
        row = {
            "skin_attribute_json": {},
            "signals_json": [
                {
                    "code": "closed_comedones",
                    "name": "闭口",
                    "regions": ["额头"],
                    "severity": "轻度",
                    "careSuggestions": ["温和代谢角质", "减少厚重叠加"],
                },
                {
                    "code": "visible_pores",
                    "name": "毛孔粗大",
                    "regions": ["鼻子"],
                    "severity": "重度",
                    "careSuggestions": ["控油", "收缩毛孔"],
                },
            ],
            "advantages_json": [],
            "overall_state": "",
            "created_at": None,
        }

        summary = extract_skin_summary(row, parse_json=lambda raw: raw)

        self.assertEqual(summary["skin_concern"], "闭口（轻度）、毛孔粗大（重度）")
        self.assertEqual(summary["skin_concern_distribution"], "额头：闭口（轻度）；鼻子：毛孔粗大（重度）")
        self.assertEqual(summary["skin_care_focus"], "温和代谢角质、减少厚重叠加、控油、收缩毛孔")


if __name__ == "__main__":
    unittest.main()
