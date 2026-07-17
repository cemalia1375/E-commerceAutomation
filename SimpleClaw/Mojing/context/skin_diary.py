"""Skin diary context and attention providers.

This module keeps the skin diary sub-agent assembly declarative: the sub-agent
declares provider groups, while providers own the domain-specific DB reads and
wording.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from simpleclaw.context.providers import (
    AttentionPacket,
    ContextBuildContext,
    ContextSection,
)

from Mojing.context import DocumentContextProvider, DocumentContextSpec
from Mojing.harness.readiness.base import normalize_status
from Mojing.runtime.task_types import MojingTaskType
from Mojing.subagent.skin_diary_postprocess import SKIN_DIARY_TODO_DOC

if TYPE_CHECKING:
    from Mojing.storage.runtime_task_repo import RuntimeTaskRepository
    from Mojing.storage.skin_diary_result_repo import SkinDiaryResultRepository


_ACTIVE_GENERATION_STATUSES = {"queued", "running", "wait_external"}


def skin_diary_document_provider(document_repo) -> DocumentContextProvider:
    """Dynamic tenant documents used by the skin diary sub-agent."""

    return DocumentContextProvider(
        document_repo=document_repo,
        specs=[
            DocumentContextSpec("USER.md", _plain_document),
            DocumentContextSpec("SOUL.md", _format_soul_document),
            DocumentContextSpec(SKIN_DIARY_TODO_DOC, _format_skin_diary_todo_document),
        ],
        source="skin_diary_documents",
    )


@dataclass(slots=True)
class SkinDiaryResultContextProvider:
    """Inject only the latest skin diary result as lightweight dynamic context."""

    result_repo: "SkinDiaryResultRepository"
    runtime_task_repo: "RuntimeTaskRepository | None" = None
    source: str = "skin_diary_result"

    async def collect_dynamic_context(
        self,
        ctx: ContextBuildContext,
    ) -> list[ContextSection]:
        latest = await self.result_repo.get_latest(ctx.tenant_key)
        if not latest:
            return []

        latest_task = await self._latest_generation_task(ctx.tenant_key)
        generation_active = _task_status(latest_task) in _ACTIVE_GENERATION_STATUSES

        content = _format_analysis(latest, generation_active=generation_active)
        if not content.strip():
            return []
        return [ContextSection(content=content, source=self.source)]

    async def _latest_generation_task(self, tenant_key: str) -> dict[str, Any] | None:
        if self.runtime_task_repo is None:
            return None
        try:
            return await self.runtime_task_repo.find_latest_task_for(
                tenant_key=tenant_key,
                task_type=MojingTaskType.SKIN_DIARY_GENERATION,
            )
        except Exception:
            return None


@dataclass(slots=True)
class SkinDiaryImageUploadAttentionProvider:
    """Current-turn image reminder for the skin diary sub-agent."""

    source: str = "skin_diary_image_upload_state"
    priority: int = 10
    placement: str = "after_history"

    async def collect_attention(
        self,
        ctx: ContextBuildContext,
    ) -> list[AttentionPacket]:
        if not bool(ctx.metadata.get("image_just_uploaded")):
            return []
        return [AttentionPacket(
            content=(
                "【本轮图片状态】用户本轮上传了新图。"
                "先把它当作本轮复核图，不要当作历史图，也不要当作新版肌肤日记已经生成。"
                "如果需要和旧图对比，再调用 retrieve_evidence(route=historical_image)。"
            ),
            source=self.source,
            priority=self.priority,
            lifetime="one_turn",
            placement=self.placement,
        )]


@dataclass(slots=True)
class SkinDiaryHandoffContractAttentionProvider:
    """Translate structured handoff contracts into one-turn execution attention."""

    source: str = "skin_diary_handoff_contract"
    priority: int = 85
    placement: str = "before_last_user"

    async def collect_attention(
        self,
        ctx: ContextBuildContext,
    ) -> list[AttentionPacket]:
        contract = ctx.metadata.get("handoff_contract")
        if not isinstance(contract, dict) or not contract:
            return []
        if str(contract.get("kind") or "").strip() != "skin_diary":
            return []

        intent = str(contract.get("intent") or "").strip() or "chat"
        required_tool = str(contract.get("required_tool") or "").strip()
        if required_tool != "generate_skin_diary":
            content = (
                "【当前肌肤日记转交】主 Agent 把用户这句话转交给肌肤日记助手继续回答。"
                f"当前意图是 {intent}。"
                "请基于已注入的用户画像、当前可用肌肤日记和用户原话回答。"
                "不要主动调用 `generate_skin_diary`。"
            )
            return [AttentionPacket(
                content=content,
                source=self.source,
                priority=self.priority,
                lifetime="one_turn",
                placement=self.placement,
                metadata={
                    "intent": intent,
                    "required_tool": required_tool,
                },
            )]
        forbid_claiming = bool(contract.get("forbid_claiming_completion_without_tool"))

        content = (
            "【当前肌肤日记工具任务】主 Agent 已判断用户明确需要生成、刷新、更新或重生成肌肤日记。"
            f"当前意图是 {intent}。"
            "本轮优先目标：直接调用 `generate_skin_diary`。"
            "不要先展示已有肌肤日记，不要直接输出完整护理建议。"
        )
        if forbid_claiming:
            content += "在未调用 `generate_skin_diary` 前，不要说新版肌肤日记已经进入生成队列，也不要说已经把新版卡片推给用户。"
        content += "只有当工具返回 deferred/deduped/failed 时，才根据工具反馈自然回复。"

        return [AttentionPacket(
            content=content,
            source=self.source,
            priority=self.priority,
            lifetime="one_turn",
            placement=self.placement,
            metadata={
                "intent": intent,
                "required_tool": required_tool,
            },
        )]


def _plain_document(content: str) -> str:
    return content.strip()


def _format_soul_document(content: str) -> str:
    return (
        "【用户沟通偏好 / 红线 · SOUL.md】\n"
        "以下是该用户明确表达过的长期沟通偏好、硬拒或红线，本轮回复要遵守；"
        "若她自己重新起头某条红线，可以接，但不要绕回劝说：\n\n"
        + content.strip()
    )


def _format_skin_diary_todo_document(content: str) -> str:
    return (
        "## 用户皮肤任务（SKIN_DIARY_TODO.md）\n"
        f"{content.strip()}\n\n"
        "这些是用户已确认的皮肤护理/观察/复盘任务；优先用于承接安排，"
        "不要把它当作图片检测结论。"
    )


def _format_analysis(result: dict[str, Any], *, generation_active: bool = False) -> str:
    summary = result.get("summary", "")
    analyzed_at = result.get("analyzed_at", "")
    diary_date = result.get("diary_date", "")
    diary_slot = result.get("diary_slot", "")
    generation_reason = result.get("generation_reason", "")
    morning: list[dict] = result.get("morning_steps") or []
    evening: list[dict] = result.get("evening_steps") or []

    title = "## 当前可用肌肤日记分析（新版生成中）" if generation_active else "## 当前可用肌肤日记分析"
    lines = [title]
    if generation_active:
        lines.append("- 注入边界：这份是新版生成任务完成前的现有结果，不代表新版已经生成完成。")
    if analyzed_at:
        lines.append(f"- 生成时间：{analyzed_at}")
    if diary_date:
        slot_text = {
            "morning": "晨间",
            "evening": "晚间",
            "midday": "午间补生成",
            "manual": "手动生成",
        }.get(str(diary_slot), str(diary_slot) or "未标注")
        lines.append(f"- 归属日记：{diary_date} {slot_text}")
    if generation_reason:
        lines.append(f"- 推导生成窗口：{generation_reason}")
    if summary:
        lines.append(f"- 总结：{summary}")

    if generation_active:
        return "\n".join(lines)

    def _fmt_steps(steps: list[dict], title: str) -> None:
        if not steps:
            return
        lines.append(f"\n### {title}")
        for step in steps[:4]:
            t = step.get("title", "")
            effect = _short_step_effect(str(step.get("effect") or ""))
            detail = f"：{effect}" if effect else ""
            lines.append(f"{step.get('order', '')}. {t}{detail}")

    _fmt_steps(morning, "晨间护肤步骤")
    _fmt_steps(evening, "晚间护肤步骤")

    return "\n".join(lines)


def _task_status(task: dict[str, Any] | None) -> str:
    return normalize_status((task or {}).get("status"))


def _short_step_effect(text: str, *, limit: int = 28) -> str:
    text = " ".join(str(text or "").split()).strip()
    if not text:
        return ""
    return text if len(text) <= limit else text[:limit].rstrip("，。；、 ") + "..."
