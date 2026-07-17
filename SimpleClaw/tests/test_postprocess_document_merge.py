"""Tests for postprocess document patch helpers."""

from __future__ import annotations

import unittest

from Mojing.agent.postprocess import append_markdown_item, remove_markdown_item, update_user_section


class PostprocessPatchHelpersTest(unittest.TestCase):
    def test_update_user_section_preserves_managed_sections(self) -> None:
        existing = """## identity

（无内容）

## style

（无内容）

## Learned Skin Profile

- 肤质：混合性皮肤
- 主要肤况：黑头
"""

        merged = update_user_section(existing, "identity", "- 称呼偏好：小风")

        self.assertIn("## Learned Skin Profile", merged)
        self.assertIn("- 肤质：混合性皮肤", merged)
        self.assertIn("## identity\n\n- 称呼偏好：小风", merged)
        self.assertIn("## style", merged)

    def test_update_user_section_inserts_missing_section_before_managed_sections(self) -> None:
        existing = """## Learned Skin Profile

- 肤质：混合性皮肤
"""

        merged = update_user_section(existing, "style", "- 回复长度偏好：简短")

        self.assertEqual(merged.count("## Learned Skin Profile"), 1)
        self.assertLess(merged.index("## style"), merged.index("## Learned Skin Profile"))
        self.assertIn("- 回复长度偏好：简短", merged)

    def test_append_markdown_item_dedupes_existing_item(self) -> None:
        merged = append_markdown_item("- 偏好直接说结论", "偏好直接说结论")

        self.assertEqual(merged, "- 偏好直接说结论")

    def test_remove_markdown_item_removes_exact_item(self) -> None:
        merged = remove_markdown_item(
            """- 偏好直接说结论

- 不喜欢被催""",
            "偏好直接说结论",
        )

        self.assertEqual(merged, "- 不喜欢被催")


if __name__ == "__main__":
    unittest.main()
