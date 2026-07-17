"""Skin diary specific postprocess hook.

This hook is intentionally narrower than the global PostprocessHook:
it only updates USER.md skin section, SOUL.md, and SKIN_DIARY_TODO.md.
"""

from __future__ import annotations

import re
from pathlib import Path

from loguru import logger

from simpleclaw.core.events import ErrorEvent
from simpleclaw.core.loop import ReactLoop
from simpleclaw.harness.hooks import PostrunHook, TurnContext
from simpleclaw.llm.base import LLMProvider
from simpleclaw.runtime.task_protocol import TaskExecutionResult
from simpleclaw.tools.base import Tool, ToolResult
from simpleclaw.tools.registry import ToolRegistry

from Mojing.agent.postprocess import append_markdown_item, remove_markdown_item, update_user_section
from Mojing.storage.document_repo import DocumentRepository

SKIN_DIARY_TODO_DOC = "SKIN_DIARY_TODO.md"

_PROMPT_PATH = Path(__file__).parent / "prompt" / "postprocess_skin_diary.md"
_SEP = "\n\n---\n\n"
_LEARNED_PROFILE_LABELS = (
    "肤龄阶段",
    "肤色调",
    "主要肤况",
    "问题分布",
    "皮肤总评",
    "皮肤优势",
    "护理关注点",
    "最近图片建档时间",
)
_DEFAULT_TODO = "## active\n\n（无内容）\n\n## done\n\n（无内容）\n\n## dismissed\n\n（无内容）"


def _load_prompt() -> str:
    if _PROMPT_PATH.exists():
        return _PROMPT_PATH.read_text(encoding="utf-8").strip()
    return "你是肌肤日记后台档案维护员。根据本轮对话更新皮肤档案或任务，完成后回 done."


def _render_input(
    *,
    user_message: str,
    assistant_reply: str,
    user_md: str,
    soul_md: str,
    skin_diary_todo_md: str,
    first_token_reply: str = "",
    main_assistant_reply: str = "",
) -> str:
    first_token_reply = str(first_token_reply or "").strip()
    main_assistant_reply = str(main_assistant_reply or "").strip()
    if first_token_reply:
        formal_reply = main_assistant_reply or str(assistant_reply or "").strip()
        turn_text = (
            "## 本轮肌肤日记对话\n\n"
            f"用户：{user_message}\n\n"
            f"肌肤日记开场（first_token_llm）：{first_token_reply}\n\n"
            f"肌肤日记正式回复：{formal_reply}"
        )
    else:
        turn_text = f"## 本轮肌肤日记对话\n\n用户：{user_message}\n\n肌肤日记：{assistant_reply}"

    parts = [turn_text]
    parts.append(f"## 当前 USER.md\n\n{user_md or '（暂无，首次写入）'}")
    parts.append(f"## 当前 SOUL.md（沟通偏好 / 红线）\n\n{soul_md or '（暂无，首次写入）'}")
    parts.append(f"## 当前 SKIN_DIARY_TODO.md\n\n{skin_diary_todo_md or '（暂无，首次写入）'}")
    return _SEP.join(parts)


class _TrackedDocumentTool(Tool):
    def __init__(
        self,
        tenant_key: str,
        document_repo: DocumentRepository,
        changed_docs: list[str] | None = None,
    ) -> None:
        self._tenant_key = tenant_key
        self._doc_repo = document_repo
        self.changed_docs = changed_docs if changed_docs is not None else []

    def _mark_changed(self, doc_name: str) -> None:
        if doc_name not in self.changed_docs:
            self.changed_docs.append(doc_name)


class _UpdateUserSkinSectionTool(_TrackedDocumentTool):
    name = "update_user_skin_section"
    description = (
        "Patch only USER.md's skin section. Pass the body of ## skin only. "
        "Do not include the ## skin heading or Learned Skin Profile fields."
    )
    parameters = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "New body for USER.md ## skin. Third-level headings like ### current_concerns are allowed.",
            },
        },
        "required": ["content"],
    }

    async def execute(self, *, content: str) -> ToolResult:
        body = str(content or "").strip()
        if _contains_top_level_heading(body):
            return ToolResult(content="Error: content must not include #/## headings.", ok=False)
        if _contains_managed_profile_content(body):
            return ToolResult(content="Error: skin section must not include Learned Skin Profile fields.", ok=False)

        existing = await self._doc_repo.get(self._tenant_key, "USER.md")
        updated = update_user_section(existing or "", "skin", body)
        if (existing or "") == updated:
            return ToolResult(content="noop: USER.md unchanged")
        await self._doc_repo.set(self._tenant_key, "USER.md", updated)
        self._mark_changed("USER.md")
        return ToolResult(content="ok: USER.md skin section updated")


class _UpdateSkinDiaryTodoTool(_TrackedDocumentTool):
    name = "update_skin_diary_todo"
    description = (
        "Replace SKIN_DIARY_TODO.md with the current skin diary task list. "
        "Keep still-active existing tasks and use sections ## active / ## done / ## dismissed."
    )
    parameters = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "Full markdown content for SKIN_DIARY_TODO.md.",
            },
        },
        "required": ["content"],
    }

    async def execute(self, *, content: str) -> ToolResult:
        updated = str(content or "").strip() or _DEFAULT_TODO
        if _contains_h1_heading(updated):
            return ToolResult(content="Error: SKIN_DIARY_TODO.md must not include a top-level # heading.", ok=False)
        existing = await self._doc_repo.get(self._tenant_key, SKIN_DIARY_TODO_DOC)
        if (existing or "").strip() == updated:
            return ToolResult(content="noop: SKIN_DIARY_TODO.md unchanged")
        await self._doc_repo.set(self._tenant_key, SKIN_DIARY_TODO_DOC, updated)
        self._mark_changed(SKIN_DIARY_TODO_DOC)
        return ToolResult(content="ok: SKIN_DIARY_TODO.md updated")


class _AppendSoulNoteTool(_TrackedDocumentTool):
    name = "append_soul_note"
    description = "Append one stable communication preference or red-line/refusal to SOUL.md. Pass a single note, without '- ' prefix."
    parameters = {
        "type": "object",
        "properties": {
            "note": {
                "type": "string",
                "description": "One stable communication preference or red-line/refusal explicitly stated by the user.",
            },
        },
        "required": ["note"],
    }

    async def execute(self, *, note: str) -> ToolResult:
        item = _normalize_markdown_item(note)
        if not item:
            return ToolResult(content="Error: note must not be empty.", ok=False)
        existing = await self._doc_repo.get(self._tenant_key, "SOUL.md")
        updated = append_markdown_item(existing or "", item)
        if (existing or "") == updated:
            return ToolResult(content="noop: SOUL.md unchanged")
        await self._doc_repo.set(self._tenant_key, "SOUL.md", updated)
        self._mark_changed("SOUL.md")
        return ToolResult(content="ok: SOUL.md note appended")


class _RemoveSoulNoteTool(_TrackedDocumentTool):
    name = "remove_soul_note"
    description = "Remove one obsolete communication preference or red-line/refusal from SOUL.md."
    parameters = {
        "type": "object",
        "properties": {
            "note": {
                "type": "string",
                "description": "Existing communication preference or red-line item to remove when the user explicitly re-opens that topic.",
            },
        },
        "required": ["note"],
    }

    async def execute(self, *, note: str) -> ToolResult:
        item = _normalize_markdown_item(note)
        if not item:
            return ToolResult(content="Error: note must not be empty.", ok=False)
        existing = await self._doc_repo.get(self._tenant_key, "SOUL.md")
        updated = remove_markdown_item(existing or "", item)
        if (existing or "") == updated:
            return ToolResult(content="noop: SOUL.md unchanged")
        await self._doc_repo.set(self._tenant_key, "SOUL.md", updated)
        self._mark_changed("SOUL.md")
        return ToolResult(content="ok: SOUL.md note removed")


class SkinDiaryPostprocessHook(PostrunHook):
    """Post-turn document maintenance for the skin diary sub-agent."""

    def __init__(self, llm: LLMProvider, document_repo: DocumentRepository) -> None:
        self._llm = llm
        self._doc_repo = document_repo
        self._system_prompt = _load_prompt()

    async def on_turn_end(self, ctx: TurnContext) -> TaskExecutionResult:
        try:
            return await self._run(ctx)
        except Exception as exc:
            logger.warning(
                "skin diary postprocess failed tenant={} session={}：{}",
                ctx.tenant_key, ctx.session_key, exc,
            )
            return TaskExecutionResult.failed(
                f"skin diary postprocess failed: {exc}",
                summary="skin diary postprocess execution failed",
            )

    async def _run(self, ctx: TurnContext) -> TaskExecutionResult:
        user_md = await self._doc_repo.get(ctx.tenant_key, "USER.md")
        soul_md = await self._doc_repo.get(ctx.tenant_key, "SOUL.md")
        todo_md = await self._doc_repo.get(ctx.tenant_key, SKIN_DIARY_TODO_DOC)

        input_text = _render_input(
            user_message=ctx.user_message,
            assistant_reply=ctx.assistant_reply,
            first_token_reply=ctx.first_token_reply,
            main_assistant_reply=ctx.main_assistant_reply,
            user_md=user_md or "",
            soul_md=soul_md or "",
            skin_diary_todo_md=todo_md or "",
        )

        registry = ToolRegistry()
        changed_docs: list[str] = []
        for tool in (
            _UpdateUserSkinSectionTool(ctx.tenant_key, self._doc_repo, changed_docs),
            _UpdateSkinDiaryTodoTool(ctx.tenant_key, self._doc_repo, changed_docs),
            _AppendSoulNoteTool(ctx.tenant_key, self._doc_repo, changed_docs),
            _RemoveSoulNoteTool(ctx.tenant_key, self._doc_repo, changed_docs),
        ):
            registry.register(tool)

        loop = ReactLoop(
            llm=self._llm,
            tool_registry=registry,
            system_prompt=self._system_prompt,
            max_iterations=3,
        )

        error_message: str | None = None
        async for event in loop.run(input_text):
            if isinstance(event, ErrorEvent):
                error_message = event.message

        if error_message:
            return TaskExecutionResult.failed(
                error_message,
                summary="skin diary postprocess react loop returned error",
            )

        logger.debug(
            "skin diary postprocess completed tenant={} session={}",
            ctx.tenant_key, ctx.session_key,
        )
        if changed_docs:
            return TaskExecutionResult.succeeded(
                summary=f"updated {', '.join(changed_docs)}",
                details={"changed_docs": list(changed_docs)},
            )
        return TaskExecutionResult.noop(
            summary="skin diary postprocess completed with no document changes",
            details={"changed_docs": []},
        )


def _contains_top_level_heading(content: str) -> bool:
    return re.search(r"(?m)^#{1,2}\s+", content) is not None


def _contains_h1_heading(content: str) -> bool:
    return re.search(r"(?m)^#\s+", content) is not None


def _contains_managed_profile_content(content: str) -> bool:
    if "Learned Skin Profile" in content:
        return True
    label_count = 0
    has_anchor = False
    for line in content.splitlines():
        label = _learned_profile_label(line)
        if label is None:
            continue
        label_count += 1
        if label in {"肤龄阶段", "问题分布", "皮肤总评", "最近图片建档时间"}:
            has_anchor = True
    return has_anchor and label_count >= 3


def _learned_profile_label(line: str) -> str | None:
    stripped = line.strip()
    if stripped.startswith("- "):
        stripped = stripped[2:].strip()
    for label in _LEARNED_PROFILE_LABELS:
        if stripped.startswith(f"{label}：") or stripped.startswith(f"{label}:"):
            return label
    return None


def _normalize_markdown_item(item: str) -> str:
    line = " ".join(str(item or "").strip().splitlines()).strip()
    if line.startswith("- "):
        line = line[2:].strip()
    return line
