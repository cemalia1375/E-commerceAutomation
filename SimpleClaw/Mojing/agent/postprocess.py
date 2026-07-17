"""PostprocessHook — 每轮主对话结束后在后台运行的档案归档 Agent。

每轮用户对话结束后，该 Hook 将：
  1. 从数据库获取该租户当前的 USER.md / SOUL.md
  2. 构建一个包含本轮对话摘要及当前文档状态的提示词
  3. 运行一个小型 ReactLoop（最多 3 次迭代），搭载局部文档 patch 工具
  4. LLM 判断本轮是否产生了值得归档的稳定信息，最多调用一次 patch 工具，
     然后回复 "done."

该 Hook 采用触发即忘模式——所有异常均被捕获并记录日志。
"""

from __future__ import annotations

import json
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

from Mojing.storage.document_repo import DocumentRepository

_POSTPROCESS_PROMPT_PATH = Path(__file__).parent.parent / "workspace" / "postprocess.md"

_USER_MD_TEMPLATE_PATH  = Path(__file__).parent.parent / "workspace" / "USER.md.skeleton"
_USER_SECTION_KEYS = ("identity", "style", "skin", "lifestyle", "goals")
_MANAGED_USER_SECTIONS = ("## Learned Skin Profile",)
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


class _UpdateUserSectionTool(_TrackedDocumentTool):
    name = "update_user_section"
    description = (
        "Patch one editable USER.md section only. The section must be one of "
        "identity, style, skin, lifestyle, goals. Pass only the section body, "
        "without top-level markdown section headings. The content must be the current "
        "complete body for that section: keep still-valid facts and remove obsolete "
        "or superseded facts. System-managed sections such as ## Learned Skin Profile "
        "are never editable by this tool."
    )
    parameters = {
        "type": "object",
        "properties": {
            "section": {
                "type": "string",
                "enum": list(_USER_SECTION_KEYS),
                "description": "Editable USER.md section to patch.",
            },
            "content": {
                "type": "string",
                "description": (
                    "Current complete body for this section only. Do not include #/## "
                    "section headings, system-managed profile fields, or copied Learned "
                    "Skin Profile severity/distribution facts."
                ),
            },
        },
        "required": ["section", "content"],
    }

    async def execute(self, *, section: str, content: str) -> ToolResult:
        if section not in _USER_SECTION_KEYS:
            return ToolResult(content=f"Error: section must be one of {list(_USER_SECTION_KEYS)}", ok=False)
        if _contains_markdown_heading(content):
            return ToolResult(content="Error: content must be a section body without #/## headings.", ok=False)
        if _contains_managed_profile_content(content):
            return ToolResult(
                content="Error: USER.md section content must not include Learned Skin Profile fields.",
                ok=False,
            )

        existing = await self._doc_repo.get(self._tenant_key, "USER.md")
        base = existing or _load_user_md_template()
        updated = update_user_section(base, section, content)
        if (existing or "") == updated:
            return ToolResult(content="noop: USER.md unchanged")
        await self._doc_repo.set(self._tenant_key, "USER.md", updated)
        self._mark_changed("USER.md")
        return ToolResult(content=f"ok: USER.md section {section} updated")


class _AppendSoulNoteTool(_TrackedDocumentTool):
    name = "append_soul_note"
    description = (
        "Append one stable communication preference or red-line/refusal to SOUL.md. "
        "Pass a single note, without '- ' prefix."
    )
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
                "description": "Existing communication preference to remove.",
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


_SEP = "\n\n---\n\n"


def _load_postprocess_prompt() -> str:
    if _POSTPROCESS_PROMPT_PATH.exists():
        return _POSTPROCESS_PROMPT_PATH.read_text(encoding="utf-8").strip()
    return "你是后台档案维护员。根据本轮对话决定是否更新用户档案，完成后回 done."


def _load_user_md_template() -> str:
    if _USER_MD_TEMPLATE_PATH.exists():
        return _USER_MD_TEMPLATE_PATH.read_text(encoding="utf-8").strip()
    return ""


def update_user_section(existing: str, section: str, body: str) -> str:
    """只替换 USER.md 中一个可编辑 section 的 body。"""
    if section not in _USER_SECTION_KEYS:
        raise ValueError(f"unknown USER.md section: {section}")
    content = existing.strip() or _default_user_md()
    replacement = f"## {section}\n\n{_normalize_section_body(body)}"
    return _replace_or_append_section(content, f"## {section}", replacement)


def _section_match(content: str, heading: str) -> re.Match[str] | None:
    heading_esc = re.escape(heading)
    pattern = rf"(?m)^{heading_esc}[^\n]*(?:\n(?!##\s+).*)*"
    return re.search(pattern, content)


def _replace_section(content: str, heading: str, replacement: str) -> str:
    heading_esc = re.escape(heading)
    pattern = rf"(?m)^{heading_esc}[^\n]*(?:\n(?!##\s+).*)*"
    return re.sub(pattern, replacement.strip(), content.strip(), count=1).strip()


def _replace_or_append_section(content: str, heading: str, replacement: str) -> str:
    if _section_match(content, heading):
        return _replace_section(content, heading, replacement)

    insertion_at = _first_managed_section_start(content)
    if insertion_at is None:
        return f"{content.strip()}\n\n{replacement.strip()}".strip()

    before = content[:insertion_at].rstrip()
    after = content[insertion_at:].lstrip()
    return f"{before}\n\n{replacement.strip()}\n\n{after}".strip()


def _first_managed_section_start(content: str) -> int | None:
    starts = []
    for heading in _MANAGED_USER_SECTIONS:
        match = _section_match(content, heading)
        if match:
            starts.append(match.start())
    return min(starts) if starts else None


def _default_user_md() -> str:
    sections = [f"## {section}\n\n（无内容）" for section in _USER_SECTION_KEYS]
    sections.extend(_MANAGED_USER_SECTIONS)
    return "\n\n".join(sections).strip()


def _normalize_section_body(body: str) -> str:
    return body.strip() or "（无内容）"


def _contains_markdown_heading(content: str) -> bool:
    return re.search(r"(?m)^#{1,2}\s+", content) is not None


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


def append_markdown_item(content: str, item: str) -> str:
    normalized = _normalize_markdown_item(item)
    if not normalized:
        return content.strip()
    existing = content.strip()
    if _has_markdown_item(existing, normalized):
        return existing
    line = f"- {normalized}"
    return f"{existing}\n{line}".strip() if existing else line


def remove_markdown_item(content: str, item: str) -> str:
    normalized = _normalize_markdown_item(item)
    if not normalized:
        return content.strip()
    kept: list[str] = []
    changed = False
    for line in content.strip().splitlines():
        if line.strip().startswith("- ") and _normalize_markdown_item(line) == normalized:
            changed = True
            continue
        kept.append(line)
    return "\n".join(kept).strip() if changed else content.strip()


def _has_markdown_item(content: str, item: str) -> bool:
    normalized = _normalize_markdown_item(item)
    return any(
        line.strip().startswith("- ") and _normalize_markdown_item(line) == normalized
        for line in content.splitlines()
    )


def _normalize_markdown_item(item: str) -> str:
    line = " ".join(item.strip().splitlines()).strip()
    if line.startswith("- "):
        line = line[2:].strip()
    return line


def _render_input(
    *,
    user_message: str,
    assistant_reply: str,
    user_md: str,
    soul_md: str,
    first_token_reply: str = "",
    main_assistant_reply: str = "",
    postprocess_hints: list[dict] | None = None,
) -> str:
    """构造 user message：只含本轮动态数据（对话 + 当前租户文档）。

    USER.md 格式模板已合并进 system prompt（稳定不变，享 prefix cache），
    不在这里重复。
    """
    first_token_reply = str(first_token_reply or "").strip()
    main_assistant_reply = str(main_assistant_reply or "").strip()
    if first_token_reply:
        formal_reply = main_assistant_reply or str(assistant_reply or "").strip()
        turn_text = (
            f"## 本轮对话\n\n"
            f"用户：{user_message}\n\n"
            f"魔镜开场（first_token_llm）：{first_token_reply}\n\n"
            f"魔镜正式回复：{formal_reply}"
        )
    else:
        turn_text = f"## 本轮对话\n\n用户：{user_message}\n\n魔镜：{assistant_reply}"

    parts = [turn_text]
    if postprocess_hints:
        parts.append(
            "## 结构化归档提示\n\n"
            "以下 JSON 来自上游结构化提示，优先按其中的 target_doc / operation / instructions 更新文档；"
            "不要把它输出给用户。\n\n"
            f"```json\n{json.dumps(postprocess_hints, ensure_ascii=False, indent=2)}\n```"
        )
    if user_md:
        parts.append(f"## 当前 USER.md（此用户现有内容）\n\n{user_md}")
    else:
        parts.append("## 当前 USER.md（此用户现有内容）\n\n（暂无，首次写入）")

    if soul_md:
        parts.append(f"## 当前 SOUL.md（此用户沟通偏好 / 红线）\n\n{soul_md}")
    else:
        parts.append("## 当前 SOUL.md（此用户沟通偏好 / 红线）\n\n（暂无，首次写入）")
    return _SEP.join(parts)


def _build_system_prompt(postprocess_prompt: str, user_md_template: str) -> str:
    """合并 postprocess.md + USER.md 格式模板为 system prompt（稳定，进 prefix cache）。"""
    return f"{postprocess_prompt}{_SEP}## USER.md 格式模板\n\n{user_md_template}"


class PostprocessHook(PostrunHook):
    """在每轮主 Agent 对话结束后运行后处理归档 Agent。"""

    def __init__(self, llm: LLMProvider, document_repo: DocumentRepository) -> None:
        self._llm = llm
        self._doc_repo = document_repo
        # 系统提示 = postprocess.md 规则 + USER.md 格式骨架（都稳定不变）
        # → 整体进 prefix cache，每轮 LLM 调用都能复用，省 prefill
        self._system_prompt = _build_system_prompt(
            _load_postprocess_prompt(),
            _load_user_md_template(),
        )

    async def on_turn_end(self, ctx: TurnContext) -> TaskExecutionResult:
        try:
            return await self._run(ctx)
        except Exception as exc:
            logger.warning(
                "postprocess 执行失败 tenant={} session={}：{}",
                ctx.tenant_key, ctx.session_key, exc,
            )
            return TaskExecutionResult.failed(
                f"postprocess failed: {exc}",
                summary="postprocess execution failed",
            )

    async def _run(self, ctx: TurnContext) -> TaskExecutionResult:
        # 获取该租户当前的动态文档（可能为空，新用户首次写入）
        user_md = await self._doc_repo.get(ctx.tenant_key, "USER.md")
        soul_md = await self._doc_repo.get(ctx.tenant_key, "SOUL.md")

        input_text = _render_input(
            user_message=ctx.user_message,
            assistant_reply=ctx.assistant_reply,
            first_token_reply=ctx.first_token_reply,
            main_assistant_reply=ctx.main_assistant_reply,
            user_md=user_md or "",
            soul_md=soul_md or "",
            postprocess_hints=list(ctx.postprocess_hints or []),
        )

        # 为该租户构建一个全新的 ToolRegistry，注册局部文档 patch 工具
        registry = ToolRegistry()
        changed_docs: list[str] = []
        for tool in (
            _UpdateUserSectionTool(ctx.tenant_key, self._doc_repo, changed_docs),
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
        # 消费循环事件——仅关注副作用（文档写入）
        async for event in loop.run(input_text):
            if isinstance(event, ErrorEvent):
                error_message = event.message

        if error_message:
            return TaskExecutionResult.failed(
                error_message,
                summary="postprocess react loop returned error",
            )

        logger.debug(
            "postprocess 完成 tenant={} session={}",
            ctx.tenant_key, ctx.session_key,
        )
        if changed_docs:
            return TaskExecutionResult.succeeded(
                summary=f"updated {', '.join(changed_docs)}",
                details={"changed_docs": list(changed_docs)},
            )
        return TaskExecutionResult.noop(
            summary="postprocess completed with no document changes",
            details={"changed_docs": []},
        )
