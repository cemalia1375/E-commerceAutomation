"""Tests for postprocess document wiring."""

from __future__ import annotations

import unittest

from Mojing.agent.postprocess import (
    _AppendSoulNoteTool,
    _RemoveSoulNoteTool,
    _UpdateUserSectionTool,
    _render_input,
)


class _DocRepo:
    def __init__(self) -> None:
        self.docs: dict[tuple[str, str], str] = {}

    async def get(self, tenant_key: str, doc_name: str) -> str | None:
        return self.docs.get((tenant_key, doc_name))

    async def set(self, tenant_key: str, doc_name: str, content: str) -> None:
        self.docs[(tenant_key, doc_name)] = content


class PostprocessDocumentsTest(unittest.IsolatedAsyncioTestCase):
    def test_render_input_includes_all_tenant_documents(self) -> None:
        rendered = _render_input(
            user_message="以后直接一点",
            assistant_reply="好，我之后直接说重点。",
            user_md="## style\n\n- 回复长度偏好：简短",
            soul_md="- 偏好直接说结论\n- 拒绝聊年龄",
        )

        self.assertIn("## 当前 USER.md", rendered)
        self.assertIn("## 当前 SOUL.md", rendered)
        self.assertIn("偏好直接说结论", rendered)
        self.assertIn("拒绝聊年龄", rendered)

    def test_render_input_keeps_first_token_and_formal_reply_separate(self) -> None:
        rendered = _render_input(
            user_message="帮我看看报告",
            assistant_reply="我先接住。\n\n正式分析内容。",
            first_token_reply="我先接住。",
            main_assistant_reply="正式分析内容。",
            user_md="",
            soul_md="",
        )

        self.assertIn("魔镜开场（first_token_llm）：我先接住。", rendered)
        self.assertIn("魔镜正式回复：正式分析内容。", rendered)

    async def test_update_user_section_patches_only_one_section(self) -> None:
        repo = _DocRepo()
        repo.docs[("tenant-1", "USER.md")] = """## identity

（无内容）

## style

（无内容）

## Learned Skin Profile

- 肤质：混合性皮肤
"""
        tool = _UpdateUserSectionTool("tenant-1", repo)  # type: ignore[arg-type]

        result = await tool.execute(section="style", content="- 回复长度偏好：简短")

        self.assertTrue(result.ok)
        updated = repo.docs[("tenant-1", "USER.md")]
        self.assertIn("## identity\n\n（无内容）", updated)
        self.assertIn("## style\n\n- 回复长度偏好：简短", updated)
        self.assertIn("## Learned Skin Profile\n\n- 肤质：混合性皮肤", updated)
        self.assertEqual(tool.changed_docs, ["USER.md"])

    async def test_update_user_section_rejects_full_document_content(self) -> None:
        repo = _DocRepo()
        tool = _UpdateUserSectionTool("tenant-1", repo)  # type: ignore[arg-type]

        result = await tool.execute(section="style", content="## style\n\n- 回复长度偏好：简短")

        self.assertFalse(result.ok)
        self.assertNotIn(("tenant-1", "USER.md"), repo.docs)

    async def test_update_user_section_allows_nested_subheadings(self) -> None:
        repo = _DocRepo()
        repo.docs[("tenant-1", "USER.md")] = "## skin\n\n（无内容）"
        tool = _UpdateUserSectionTool("tenant-1", repo)  # type: ignore[arg-type]

        result = await tool.execute(
            section="skin",
            content="### current_concerns\n\n- 下巴闭口｜来源：用户主诉关注",
        )

        self.assertTrue(result.ok)
        self.assertIn("### current_concerns", repo.docs[("tenant-1", "USER.md")])

    async def test_append_soul_note_dedupes(self) -> None:
        repo = _DocRepo()
        repo.docs[("tenant-1", "SOUL.md")] = "- 偏好直接说结论"
        tool = _AppendSoulNoteTool("tenant-1", repo)  # type: ignore[arg-type]

        result = await tool.execute(note="偏好直接说结论")

        self.assertTrue(result.ok)
        self.assertEqual(repo.docs[("tenant-1", "SOUL.md")], "- 偏好直接说结论")
        self.assertEqual(tool.changed_docs, [])

    async def test_append_and_remove_redline_in_soul(self) -> None:
        repo = _DocRepo()
        append_tool = _AppendSoulNoteTool("tenant-1", repo)  # type: ignore[arg-type]
        remove_tool = _RemoveSoulNoteTool("tenant-1", repo, append_tool.changed_docs)  # type: ignore[arg-type]

        append_result = await append_tool.execute(note="拒绝聊年龄")
        remove_result = await remove_tool.execute(note="拒绝聊年龄")

        self.assertTrue(append_result.ok)
        self.assertTrue(remove_result.ok)
        self.assertEqual(repo.docs[("tenant-1", "SOUL.md")], "")
        self.assertEqual(append_tool.changed_docs, ["SOUL.md"])


if __name__ == "__main__":
    unittest.main()
