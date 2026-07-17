"""Readiness checks for skin diary generation."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from Mojing.harness.readiness.base import (
    ACTIVE_STATUSES,
    CapabilityDecision,
    status_of,
)
from Mojing.runtime.task_types import MojingTaskType
from Mojing.utils.skin_diary_time import (
    DIARY_SLOT_MIDDAY,
    resolve_skin_diary_generation_window,
)

if TYPE_CHECKING:
    from Mojing.storage.document_repo import DocumentRepository
    from Mojing.storage.runtime_task_repo import RuntimeTaskRepository
    from Mojing.storage.skin_diary_result_repo import SkinDiaryResultRepository
    from Mojing.storage.skin_profile_repo import SkinProfileRepository


class SkinDiaryGenerationReadiness:
    """Computes objective readiness for skin diary handoff/generation."""

    def __init__(
        self,
        *,
        document_repo: "DocumentRepository | None" = None,
        skin_profile_repo: "SkinProfileRepository | None" = None,
        skin_diary_result_repo: "SkinDiaryResultRepository | None" = None,
        runtime_task_repo: "RuntimeTaskRepository | None" = None,
        now_fn: Any | None = None,
    ) -> None:
        # Profile readiness is owned by the image-analysis/sync pipeline.
        del document_repo, skin_profile_repo
        self._skin_diary_result_repo = skin_diary_result_repo
        self._runtime_task_repo = runtime_task_repo
        self._now_fn = now_fn

    async def check_skin_diary_handoff(self, tenant_key: str) -> CapabilityDecision:
        tenant_key = str(tenant_key or "").strip()
        facts: dict[str, Any] = {"tenant_key": tenant_key}

        if not tenant_key or tenant_key == "__default__":
            return CapabilityDecision(
                allowed=False,
                capability="skin_diary_handoff",
                reason="missing_context",
                phase="blocked",
                message_focus="当前缺少用户上下文，先不要派发肌肤日记助手。",
                facts=facts,
            )

        return await self._check_image_analysis_history(
            tenant_key,
            capability="skin_diary_handoff",
            facts=facts,
        )

    async def check_generate_skin_diary(
        self,
        tenant_key: str,
        *,
        generation_input: dict[str, Any] | None = None,
    ) -> CapabilityDecision:
        tenant_key = str(tenant_key or "").strip()
        generation_input = dict(generation_input or {})
        generation_reason = str(generation_input.get("regeneration_reason") or "").strip()
        facts: dict[str, Any] = {
            "tenant_key": tenant_key,
            "generation_reason": generation_reason,
        }

        if not tenant_key or tenant_key == "__default__":
            return CapabilityDecision(
                allowed=False,
                capability="skin_diary_generation",
                reason="missing_context",
                phase="blocked",
                message_focus="当前缺少用户上下文，先不要生成新版肌肤日记。",
                facts=facts,
            )

        task = await self._latest_task(tenant_key)
        task_status = status_of(task)
        facts["skin_diary_task_status"] = task_status
        facts["skin_diary_task_id"] = task.get("task_id") if task else None
        if task_status in ACTIVE_STATUSES:
            return CapabilityDecision(
                allowed=False,
                capability="skin_diary_generation",
                reason="skin_diary_generation_running",
                phase="in_progress",
                message_focus=(
                    "新版肌肤日记已经在生成中，不要重复触发。"
                    "请告诉用户正在处理，可以先继续聊当前关注。"
                ),
                facts=facts,
            )

        image_decision = await self._check_image_analysis_history(
            tenant_key,
            capability="skin_diary_generation",
            facts=facts,
        )
        if not image_decision.allowed:
            return image_decision

        now = self._now()
        window = resolve_skin_diary_generation_window(now)
        facts["business_date"] = window.business_date.isoformat() if window.business_date else ""
        facts["diary_slot"] = window.diary_slot or ""
        facts["auto_window"] = window.should_consider
        facts["local_time"] = window.local_time.strftime("%Y-%m-%d %H:%M:%S")

        has_business_date_result = False
        has_slot_result = False
        if window.business_date is not None and self._skin_diary_result_repo is not None:
            has_business_date_result = await self._skin_diary_result_repo.has_result_for_business_date(
                tenant_key,
                window.business_date,
            )
            if window.diary_slot:
                has_slot_result = await self._skin_diary_result_repo.has_result_for_business_date_slot(
                    tenant_key,
                    window.business_date,
                    window.diary_slot,
                )
        facts["has_business_date_result"] = has_business_date_result
        facts["has_slot_result"] = has_slot_result

        if _already_has_relevant_result(
            diary_slot=window.diary_slot,
            has_business_date_result=has_business_date_result,
            has_slot_result=has_slot_result,
        ) and not generation_reason:
            return CapabilityDecision(
                allowed=False,
                capability="skin_diary_generation",
                reason="existing_diary_requires_refresh_intent",
                phase="already_available",
                message_focus=(
                    "当前业务日期已经有肌肤日记。请先基于已有日记回答用户；"
                    "只有用户明确要求刷新、重新生成、再来一版，或确认把新关注纳入新版时，"
                    "才调用 generate_skin_diary，并填写 regeneration_reason。"
                ),
                facts=facts,
            )

        return CapabilityDecision(
            allowed=True,
            capability="skin_diary_generation",
            reason="ready",
            phase="ready",
            message_focus="肌肤日记生成前置条件已满足，可以触发。",
            facts=facts,
        )

    async def _latest_task(self, tenant_key: str) -> dict[str, Any] | None:
        if self._runtime_task_repo is None:
            return None
        return await self._runtime_task_repo.find_latest_task_for(
            tenant_key=tenant_key,
            task_type=MojingTaskType.SKIN_DIARY_GENERATION,
        )

    async def _check_image_analysis_history(
        self,
        tenant_key: str,
        *,
        capability: str,
        facts: dict[str, Any],
    ) -> CapabilityDecision:
        has_succeeded_image_analysis = await self._has_succeeded_image_analysis(tenant_key)
        facts["has_succeeded_image_analysis"] = has_succeeded_image_analysis
        if not has_succeeded_image_analysis:
            return CapabilityDecision(
                allowed=False,
                capability=capability,
                reason="missing_image_analysis",
                phase="blocked",
                message_focus=(
                    "当前还没有成功的图片分析结果，不能直接生成或刷新今日肌肤日记。"
                    "请自然引导用户先上传一张清晰正脸照；不要说已经开始生成，"
                    "不要改成护肤柜流程，也不要主动触发其他后台任务。"
                ),
                facts=facts,
            )
        return CapabilityDecision(
            allowed=True,
            capability=capability,
            reason="ready",
            phase="ready",
            message_focus="肌肤日记图片分析前置条件已满足。",
            facts=facts,
        )

    async def _has_succeeded_image_analysis(self, tenant_key: str) -> bool:
        if self._runtime_task_repo is None:
            return True
        checker = getattr(self._runtime_task_repo, "has_succeeded_task_for", None)
        if not callable(checker):
            return True
        return bool(await checker(
            tenant_key=tenant_key,
            task_type=MojingTaskType.IMAGE_ANALYSIS,
        ))

    def _now(self) -> datetime | None:
        if self._now_fn is None:
            return None
        return self._now_fn()


def _already_has_relevant_result(
    *,
    diary_slot: str | None,
    has_business_date_result: bool,
    has_slot_result: bool,
) -> bool:
    if diary_slot == DIARY_SLOT_MIDDAY:
        return has_business_date_result
    return has_slot_result
