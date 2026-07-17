"""Tests for skin diary specific postprocess tools."""

from __future__ import annotations

import unittest

from Mojing.subagent.skin_diary_postprocess import (
    SKIN_DIARY_TODO_DOC,
    _AppendSoulNoteTool,
    _RemoveSoulNoteTool,
    _UpdateSkinDiaryTodoTool,
    _UpdateUserSkinSectionTool,
    _render_input,
)


class _DocRepo:
    def __init__(self) -> None:
        self.docs: dict[tuple[str, str], str] = {}

    async def get(self, tenant_key: str, doc_name: str) -> str | None:
        return self.docs.get((tenant_key, doc_name))

    async def set(self, tenant_key: str, doc_name: str, content: str) -> None:
        self.docs[(tenant_key, doc_name)] = content


class SkinDiaryPostprocessToolsTest(unittest.IsolatedAsyncioTestCase):
    def test_render_input_includes_skin_diary_todo(self) -> None:
        rendered = _render_input(
            user_message="今晚提醒我观察下巴闭口",
            assistant_reply="好，我会把晚间观察作为任务承接。",
            user_md="## skin\n\n（无内容）",
            soul_md="- 不想被催拍照",
            skin_diary_todo_md="## active\n\n- [ ] due: tonight｜观察下巴闭口",
        )

        self.assertIn("## 当前 SKIN_DIARY_TODO.md", rendered)
        self.assertIn("观察下巴闭口", rendered)
        self.assertIn("不想被催拍照", rendered)

    async def test_update_user_skin_section_only_patches_skin(self) -> None:
        repo = _DocRepo()
        repo.docs[("tenant-1", "USER.md")] = """## identity

（无内容）

## skin

（无内容）

## Learned Skin Profile

- 肤质：混合性皮肤
"""
        tool = _UpdateUserSkinSectionTool("tenant-1", repo)  # type: ignore[arg-type]

        result = await tool.execute(
            content="### current_concerns\n\n- 下巴闭口｜来源：用户主诉关注｜不作为图片检测结论"
        )

        self.assertTrue(result.ok)
        updated = repo.docs[("tenant-1", "USER.md")]
        self.assertIn("## identity\n\n（无内容）", updated)
        self.assertIn("### current_concerns", updated)
        self.assertIn("## Learned Skin Profile\n\n- 肤质：混合性皮肤", updated)
        self.assertEqual(tool.changed_docs, ["USER.md"])

    async def test_update_user_skin_section_rejects_managed_profile_content(self) -> None:
        repo = _DocRepo()
        tool = _UpdateUserSkinSectionTool("tenant-1", repo)  # type: ignore[arg-type]

        result = await tool.execute(
            content="- 肤龄阶段：轻熟肌\n- 问题分布：鼻翼\n- 皮肤总评：稳定"
        )

        self.assertFalse(result.ok)
        self.assertNotIn(("tenant-1", "USER.md"), repo.docs)

    async def test_update_skin_diary_todo_writes_dedicated_document(self) -> None:
        repo = _DocRepo()
        tool = _UpdateSkinDiaryTodoTool("tenant-1", repo)  # type: ignore[arg-type]

        result = await tool.execute(
            content="## active\n\n- [ ] due: 2026-05-01 evening｜观察下巴闭口｜source: user_confirmed"
        )

        self.assertTrue(result.ok)
        self.assertIn("观察下巴闭口", repo.docs[("tenant-1", SKIN_DIARY_TODO_DOC)])
        self.assertEqual(tool.changed_docs, [SKIN_DIARY_TODO_DOC])

    async def test_append_and_remove_redline_in_soul(self) -> None:
        repo = _DocRepo()
        append_tool = _AppendSoulNoteTool("tenant-1", repo)  # type: ignore[arg-type]
        remove_tool = _RemoveSoulNoteTool("tenant-1", repo, append_tool.changed_docs)  # type: ignore[arg-type]

        append_result = await append_tool.execute(note="不想被催拍照")
        remove_result = await remove_tool.execute(note="不想被催拍照")

        self.assertTrue(append_result.ok)
        self.assertTrue(remove_result.ok)
        self.assertEqual(repo.docs[("tenant-1", "SOUL.md")], "")
        self.assertEqual(append_tool.changed_docs, ["SOUL.md"])


if __name__ == "__main__":
    unittest.main()
