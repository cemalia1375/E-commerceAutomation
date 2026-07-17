"""Mojing business providers for SimpleClaw context and attention.

SimpleClaw owns the provider protocols and rendering rules. This module owns
Mojing-specific database reads and wording.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Callable

from simpleclaw.context.providers import (
    AttentionPacket,
    ContextBuildContext,
    ContextSection,
)
from Mojing.harness.readiness.base import normalize_status
from Mojing.runtime.task_types import MojingTaskType

if TYPE_CHECKING:
    from Mojing.storage.completion_event_repo import CompletionEventRepository
    from Mojing.harness.readiness.deep_report import DeepReportReadiness
    from Mojing.storage.deep_report_repo import DeepReportRepository
    from Mojing.storage.document_repo import DocumentRepository
    from Mojing.storage.image_repo import ImageRepository
    from Mojing.storage.runtime_task_repo import RuntimeTaskRepository
    from Mojing.storage.tenant_state_repo import TenantStateRepository


DocumentFormatter = Callable[[str], str]


async def _consume_completion_event(
    repo: "CompletionEventRepository | None",
    event: dict[str, Any],
) -> None:
    """Consume a completion event once it is exposed through provider context."""
    if repo is None:
        return
    event_id = str(event.get("event_id") or "").strip()
    if not event_id:
        return
    try:
        await repo.mark_consumed_by_provider(event_id=event_id)
    except Exception:
        return


@dataclass(frozen=True, slots=True)
class DocumentContextSpec:
    """How to render one tenant document into dynamic context."""

    doc_name: str
    formatter: DocumentFormatter


@dataclass(slots=True)
class DocumentContextProvider:
    """Reads tenant documents and renders them as dynamic background context."""

    document_repo: "DocumentRepository"
    specs: list[DocumentContextSpec]
    source: str = "document_context"

    async def collect_dynamic_context(
        self,
        ctx: ContextBuildContext,
    ) -> list[ContextSection]:
        sections: list[ContextSection] = []
        for spec in self.specs:
            content = await self.document_repo.get(ctx.tenant_key, spec.doc_name)
            if not content or not content.strip():
                continue
            rendered = spec.formatter(content.strip())
            if rendered and rendered.strip():
                sections.append(ContextSection(content=rendered, source=self.source))
        return sections


@dataclass(slots=True)
class CurrentTimeContextProvider:
    """Injects current Beijing time for reminders and scheduled tasks."""

    source: str = "current_time"

    async def collect_dynamic_context(
        self,
        ctx: ContextBuildContext,
    ) -> list[ContextSection]:
        del ctx
        return [ContextSection(content=_current_time_note(), source=self.source)]


@dataclass(slots=True)
class SelfieAgeAttentionProvider:
    """Injects a one-turn freshness hint only when the latest user selfie is stale."""

    image_repo: "ImageRepository"
    stale_after_hours: int = 8
    source: str = "selfie_age"
    priority: int = 18
    placement: str = "before_last_user"

    async def collect_attention(
        self,
        ctx: ContextBuildContext,
    ) -> list[AttentionPacket]:
        note = await _selfie_age_note(
            self.image_repo,
            ctx.tenant_key,
            stale_after_hours=self.stale_after_hours,
        )
        if not note:
            return []
        return [AttentionPacket(
            content=note,
            source=self.source,
            priority=self.priority,
            lifetime="one_turn",
            placement=self.placement,
        )]


@dataclass(slots=True)
class ImageUploadAttentionProvider:
    """Current-turn reminder for newly uploaded images."""

    source: str = "image_upload_state"
    priority: int = 10
    placement: str = "before_last_user"

    async def collect_attention(
        self,
        ctx: ContextBuildContext,
    ) -> list[AttentionPacket]:
        if not bool(ctx.metadata.get("image_just_uploaded")):
            return []
        return [AttentionPacket(
            content=(
                "【本轮图片状态】用户本轮上传了图片，你必须先看图判断。"
                "如果是清晰的用户正脸/肤况图，对用户上传的自拍进行一个简单的分析，然后调用 analyze_image。"
                "如果是护肤品或化妆品产品图，先调用 load_skill 读取 skincare_cabinet skill，再按该 skill 的步骤继续。"
                "如果图片模糊、无关，或无法判断，不要调用分析工具；请用户补充清晰正脸照或产品正面照。"
            ),
            source=self.source,
            priority=self.priority,
            lifetime="one_turn",
            placement=self.placement,
        )]


@dataclass(slots=True)
class EvidenceAttentionProvider:
    """Hints the model to use the unified evidence retrieval entry point."""

    source: str = "evidence_retrieval_hint"
    priority: int = 12
    placement: str = "before_last_user"

    async def collect_attention(
        self,
        ctx: ContextBuildContext,
    ) -> list[AttentionPacket]:
        from Mojing.evidence import route_evidence_query

        route = route_evidence_query(
            ctx.query,
            has_current_media=bool(ctx.metadata.get("image_just_uploaded")),
        )
        if route.kind == "none":
            return []

        if route.kind == "historical_image":
            focus = (
                "用户的问题需要查看历史图片证据后再回答。"
                "请先用一句温暖简短的话告知用户稍等，然后调用 retrieve_evidence。"
                "不要先调用文字记忆；工具返回历史图片后，再基于图片回答。"
            )
        else:
            focus = (
                "用户的问题需要查看历史文字记忆后再回答。"
                "请先用一句温暖简短的话告知用户稍等，然后调用 retrieve_evidence。"
                "工具返回后，结合召回内容回答；不要编造记忆。"
            )

        return [AttentionPacket(
            content=f"【需要补充证据】{focus}",
            source=self.source,
            priority=self.priority,
            lifetime="one_turn",
            placement=self.placement,
            metadata={"route": route.kind, "reason": route.reason},
        )]


@dataclass(slots=True)
class SkinDiaryCompletionAttentionProvider:
    """Notify the main agent once when latest skin diary generation reaches an outcome."""

    runtime_task_repo: "RuntimeTaskRepository"
    emission_state: dict[str, Any]
    completion_event_repo: "CompletionEventRepository | None" = None
    source: str = "skin_diary_completion_state"
    priority: int = 19
    placement: str = "after_history"

    async def collect_attention(
        self,
        ctx: ContextBuildContext,
    ) -> list[AttentionPacket]:
        tenant_key = str(ctx.tenant_key or "").strip()
        if not tenant_key:
            return []

        event = await self._pending_completion_event(
            tenant_key=tenant_key,
            session_key=str(ctx.metadata.get("session_key") or ""),
        )
        if self.completion_event_repo is not None:
            if not event:
                return []
            latest = await self._task_by_event(event)
            if not latest:
                return []
            status = normalize_status(latest.get("status"))
            if status != "succeeded" and not _is_final_failed_outcome(latest, status):
                return []
            await _consume_completion_event(self.completion_event_repo, event)
            return [AttentionPacket(
                content=_skin_diary_outcome_content(latest, status),
                source=self.source,
                priority=self.priority,
                lifetime="one_turn",
                placement=self.placement,
                metadata={
                    "event_id": str(event.get("event_id") or ""),
                    "task_id": str(latest.get("task_id") or ""),
                    "status": status,
                },
            )]

        latest = await self._latest_skin_diary_generation_task(tenant_key)
        if not latest:
            self._clear_state(tenant_key)
            return []

        status = normalize_status(latest.get("status"))
        if status != "succeeded" and not _is_final_failed_outcome(latest, status):
            self._clear_state(tenant_key)
            return []

        signature = ":".join([
            str(latest.get("task_id") or ""),
            status,
            str(latest.get("completed_at") or latest.get("updated_at") or ""),
        ])
        if not self._should_emit(tenant_key, signature=signature):
            return []

        return [AttentionPacket(
            content=_skin_diary_outcome_content(latest, status),
            source=self.source,
            priority=self.priority,
            lifetime="one_turn",
            placement=self.placement,
            metadata={
                "task_id": str(latest.get("task_id") or ""),
                "status": status,
            },
        )]

    async def _latest_skin_diary_generation_task(self, tenant_key: str) -> dict[str, Any] | None:
        try:
            return await self.runtime_task_repo.find_latest_task_for(
                tenant_key=tenant_key,
                task_type=MojingTaskType.SKIN_DIARY_GENERATION,
            )
        except Exception:
            return None

    async def _pending_completion_event(
        self,
        *,
        tenant_key: str,
        session_key: str,
    ) -> dict[str, Any] | None:
        if self.completion_event_repo is None:
            return None
        try:
            return await self.completion_event_repo.find_oldest_pending(
                tenant_key=tenant_key,
                session_key=session_key or None,
                activation_kinds=("skin_diary_completion", "skin_diary_generation_failure"),
            )
        except Exception:
            return None

    async def _task_by_event(self, event: dict[str, Any]) -> dict[str, Any] | None:
        task_id = str(event.get("task_id") or "").strip()
        if not task_id:
            return None
        try:
            task = await self.runtime_task_repo.get(task_id)
        except Exception:
            return None
        return _task_record_to_dict(task)

    def _should_emit(self, tenant_key: str, *, signature: str) -> bool:
        key = f"{tenant_key}:{self.source}:signature"
        previous = self.emission_state.get(key)
        if previous != signature:
            self.emission_state[key] = signature
            return True
        return False

    def _clear_state(self, tenant_key: str) -> None:
        self.emission_state.pop(f"{tenant_key}:{self.source}:signature", None)


@dataclass(slots=True)
class ImageAnalysisCompletionAttentionProvider:
    """Notify the main agent once when latest image analysis succeeds."""

    runtime_task_repo: "RuntimeTaskRepository"
    emission_state: dict[str, Any]
    completion_event_repo: "CompletionEventRepository | None" = None
    source: str = "image_analysis_completion_state"
    priority: int = 19
    placement: str = "after_history"

    async def collect_attention(
        self,
        ctx: ContextBuildContext,
    ) -> list[AttentionPacket]:
        tenant_key = str(ctx.tenant_key or "").strip()
        if not tenant_key:
            return []

        event = await self._pending_completion_event(
            tenant_key=tenant_key,
            session_key=str(ctx.metadata.get("session_key") or ""),
        )
        if self.completion_event_repo is not None:
            if not event:
                return []
            latest = await self._task_by_event(event)
            if not latest:
                return []
            status = normalize_status(latest.get("status"))
            if status != "succeeded":
                return []
            await _consume_completion_event(self.completion_event_repo, event)
            return [AttentionPacket(
                content=_image_analysis_completion_content(latest),
                source=self.source,
                priority=self.priority,
                lifetime="one_turn",
                placement=self.placement,
                metadata={
                    "event_id": str(event.get("event_id") or ""),
                    "task_id": str(latest.get("task_id") or ""),
                    "status": status,
                },
            )]

        latest = await self._latest_image_analysis_task(tenant_key)
        if not latest:
            self._clear_state(tenant_key)
            return []

        status = normalize_status(latest.get("status"))
        if status != "succeeded":
            self._clear_state(tenant_key)
            return []

        signature = ":".join([
            str(latest.get("task_id") or ""),
            status,
            str(latest.get("completed_at") or latest.get("updated_at") or ""),
        ])
        if not self._should_emit(tenant_key, signature=signature):
            return []

        return [AttentionPacket(
            content=_image_analysis_completion_content(latest),
            source=self.source,
            priority=self.priority,
            lifetime="one_turn",
            placement=self.placement,
            metadata={
                "task_id": str(latest.get("task_id") or ""),
                "status": status,
            },
        )]

    async def _latest_image_analysis_task(self, tenant_key: str) -> dict[str, Any] | None:
        try:
            return await self.runtime_task_repo.find_latest_task_for(
                tenant_key=tenant_key,
                task_type=MojingTaskType.IMAGE_ANALYSIS,
            )
        except Exception:
            return None

    async def _pending_completion_event(
        self,
        *,
        tenant_key: str,
        session_key: str,
    ) -> dict[str, Any] | None:
        if self.completion_event_repo is None:
            return None
        try:
            return await self.completion_event_repo.find_oldest_pending(
                tenant_key=tenant_key,
                session_key=session_key or None,
                activation_kinds=("image_analysis_completion",),
            )
        except Exception:
            return None

    async def _task_by_event(self, event: dict[str, Any]) -> dict[str, Any] | None:
        task_id = str(event.get("task_id") or "").strip()
        if not task_id:
            return None
        try:
            task = await self.runtime_task_repo.get(task_id)
        except Exception:
            return None
        return _task_record_to_dict(task)

    def _should_emit(self, tenant_key: str, *, signature: str) -> bool:
        key = f"{tenant_key}:{self.source}:signature"
        previous = self.emission_state.get(key)
        if previous != signature:
            self.emission_state[key] = signature
            return True
        return False

    def _clear_state(self, tenant_key: str) -> None:
        self.emission_state.pop(f"{tenant_key}:{self.source}:signature", None)


@dataclass(slots=True)
class ImageAnalysisFailureAttentionProvider:
    """Notify the main agent once when latest image analysis finally fails."""

    runtime_task_repo: "RuntimeTaskRepository"
    emission_state: dict[str, Any]
    completion_event_repo: "CompletionEventRepository | None" = None
    source: str = "image_analysis_failure_state"
    priority: int = 19
    placement: str = "after_history"

    async def collect_attention(
        self,
        ctx: ContextBuildContext,
    ) -> list[AttentionPacket]:
        tenant_key = str(ctx.tenant_key or "").strip()
        if not tenant_key:
            return []

        event = await self._pending_completion_event(
            tenant_key=tenant_key,
            session_key=str(ctx.metadata.get("session_key") or ""),
        )
        if self.completion_event_repo is not None:
            if not event:
                return []
            latest = await self._task_by_event(event)
            if not latest:
                return []
            status = normalize_status(latest.get("status"))
            if not _is_final_failed_outcome(latest, status):
                return []
            await _consume_completion_event(self.completion_event_repo, event)
            return [AttentionPacket(
                content=_image_analysis_failure_content(latest),
                source=self.source,
                priority=self.priority,
                lifetime="one_turn",
                placement=self.placement,
                metadata={
                    "event_id": str(event.get("event_id") or ""),
                    "task_id": str(latest.get("task_id") or ""),
                    "status": status,
                },
            )]

        latest = await self._latest_image_analysis_task(tenant_key)
        if not latest:
            self._clear_state(tenant_key)
            return []

        status = normalize_status(latest.get("status"))
        if not _is_final_failed_outcome(latest, status):
            self._clear_state(tenant_key)
            return []

        signature = ":".join([
            str(latest.get("task_id") or ""),
            status,
            str(latest.get("completed_at") or latest.get("updated_at") or ""),
            str(latest.get("last_error") or "")[:120],
        ])
        if not self._should_emit(tenant_key, signature=signature):
            return []

        return [AttentionPacket(
            content=_image_analysis_failure_content(latest),
            source=self.source,
            priority=self.priority,
            lifetime="one_turn",
            placement=self.placement,
            metadata={
                "task_id": str(latest.get("task_id") or ""),
                "status": status,
            },
        )]

    async def _latest_image_analysis_task(self, tenant_key: str) -> dict[str, Any] | None:
        try:
            return await self.runtime_task_repo.find_latest_task_for(
                tenant_key=tenant_key,
                task_type=MojingTaskType.IMAGE_ANALYSIS,
            )
        except Exception:
            return None

    async def _pending_completion_event(
        self,
        *,
        tenant_key: str,
        session_key: str,
    ) -> dict[str, Any] | None:
        if self.completion_event_repo is None:
            return None
        try:
            return await self.completion_event_repo.find_oldest_pending(
                tenant_key=tenant_key,
                session_key=session_key or None,
                activation_kinds=("image_analysis_failure",),
            )
        except Exception:
            return None

    async def _task_by_event(self, event: dict[str, Any]) -> dict[str, Any] | None:
        task_id = str(event.get("task_id") or "").strip()
        if not task_id:
            return None
        try:
            task = await self.runtime_task_repo.get(task_id)
        except Exception:
            return None
        return _task_record_to_dict(task)

    def _should_emit(self, tenant_key: str, *, signature: str) -> bool:
        key = f"{tenant_key}:{self.source}:signature"
        previous = self.emission_state.get(key)
        if previous != signature:
            self.emission_state[key] = signature
            return True
        return False

    def _clear_state(self, tenant_key: str) -> None:
        self.emission_state.pop(f"{tenant_key}:{self.source}:signature", None)


@dataclass(slots=True)
class DeepReportOutcomeAttentionProvider:
    """Notify the main agent once when latest deep report generation reaches an outcome."""

    runtime_task_repo: "RuntimeTaskRepository"
    emission_state: dict[str, Any]
    report_repo: "DeepReportRepository | None" = None
    completion_event_repo: "CompletionEventRepository | None" = None
    source: str = "deep_report_outcome_state"
    priority: int = 19
    placement: str = "after_history"

    async def collect_attention(
        self,
        ctx: ContextBuildContext,
    ) -> list[AttentionPacket]:
        tenant_key = str(ctx.tenant_key or "").strip()
        if not tenant_key:
            return []

        event = await self._pending_completion_event(
            tenant_key=tenant_key,
            session_key=str(ctx.metadata.get("session_key") or ""),
        )
        if self.completion_event_repo is not None:
            if not event:
                return []
            latest = await self._task_by_event(event)
            if not latest:
                return []
            status = normalize_status(latest.get("status"))
            if status != "succeeded" and not _is_final_failed_outcome(latest, status):
                return []
            latest_report = await self._latest_report(tenant_key) if status == "succeeded" else None
            await _consume_completion_event(self.completion_event_repo, event)
            return [AttentionPacket(
                content=_deep_report_outcome_content(latest, status, latest_report=latest_report),
                source=self.source,
                priority=self.priority,
                lifetime="one_turn",
                placement=self.placement,
                metadata={
                    "event_id": str(event.get("event_id") or ""),
                    "task_id": str(latest.get("task_id") or ""),
                    "status": status,
                },
            )]

        latest = await self._latest_deep_report_task(tenant_key)
        if not latest:
            self._clear_state(tenant_key)
            return []

        status = normalize_status(latest.get("status"))
        if status != "succeeded" and not _is_final_failed_outcome(latest, status):
            self._clear_state(tenant_key)
            return []

        signature = ":".join([
            str(latest.get("task_id") or ""),
            status,
            str(latest.get("completed_at") or latest.get("updated_at") or ""),
            str(latest.get("last_error") or "")[:120],
        ])
        if not self._should_emit(tenant_key, signature=signature):
            return []

        latest_report = await self._latest_report(tenant_key) if status == "succeeded" else None
        return [AttentionPacket(
            content=_deep_report_outcome_content(latest, status, latest_report=latest_report),
            source=self.source,
            priority=self.priority,
            lifetime="one_turn",
            placement=self.placement,
            metadata={
                "task_id": str(latest.get("task_id") or ""),
                "status": status,
            },
        )]

    async def _latest_deep_report_task(self, tenant_key: str) -> dict[str, Any] | None:
        try:
            return await self.runtime_task_repo.find_latest_task_for(
                tenant_key=tenant_key,
                task_type=MojingTaskType.DEEP_RESEARCH,
            )
        except Exception:
            return None

    async def _pending_completion_event(
        self,
        *,
        tenant_key: str,
        session_key: str,
    ) -> dict[str, Any] | None:
        if self.completion_event_repo is None:
            return None
        try:
            return await self.completion_event_repo.find_oldest_pending(
                tenant_key=tenant_key,
                session_key=session_key or None,
                activation_kinds=("deep_report_completion", "deep_research_failure"),
            )
        except Exception:
            return None

    async def _task_by_event(self, event: dict[str, Any]) -> dict[str, Any] | None:
        task_id = str(event.get("task_id") or "").strip()
        if not task_id:
            return None
        try:
            task = await self.runtime_task_repo.get(task_id)
        except Exception:
            return None
        return _task_record_to_dict(task)

    async def _latest_report(self, tenant_key: str) -> dict[str, Any] | None:
        if self.report_repo is None:
            return None
        try:
            return await self.report_repo.find_latest(tenant_key)
        except Exception:
            return None

    def _should_emit(self, tenant_key: str, *, signature: str) -> bool:
        key = f"{tenant_key}:{self.source}:signature"
        previous = self.emission_state.get(key)
        if previous != signature:
            self.emission_state[key] = signature
            return True
        return False

    def _clear_state(self, tenant_key: str) -> None:
        self.emission_state.pop(f"{tenant_key}:{self.source}:signature", None)


def _skin_diary_outcome_content(task: dict[str, Any], status: str) -> str:
    if status == "failed":
        error = str(task.get("last_error") or "").strip()
        suffix = f"失败原因：{error[:120]}。" if error else ""
        return (
            "【肌肤日记失败状态】最近一次肌肤日记没有生成成功。"
            f"{suffix}"
            "只说明肌肤日记失败，不要复述图片分析、深度分析报告或其他任务状态；"
            "请自然告诉用户这次没有生成出来，并询问是否需要重新生成一版；不要说已经完成。"
        )
    return (
        "【肌肤日记完成状态】最近一次肌肤日记已经完成。"
        "只说明肌肤日记完成，可以去【肌肤日记】页面看今天的护肤安排或这次更新结果。"
        "不要复述图片分析、深度分析报告或其他任务的完成/处理中/失败状态；不要作阶段汇总。"
    )


def _image_analysis_completion_content(task: dict[str, Any]) -> str:
    del task
    return (
        "【图片分析完成状态】刚刚这次图片分析已经完成，新的肤况信息也同步好了。"
        "只说明图片分析完成，不要复述肌肤日记、深度分析报告或其他任务的完成/处理中/失败状态；不要作阶段汇总。"
    )


def _image_analysis_failure_content(task: dict[str, Any]) -> str:
    error = str(task.get("last_error") or "").strip()
    suffix = f"失败原因：{error[:120]}。" if error else ""
    return (
        "【图片分析失败状态】刚刚这次图片分析断掉了，没有拿到结果。"
        f"{suffix}"
        "只说明图片分析失败，不要复述肌肤日记、深度分析报告或其他任务状态；"
        "请自然告诉用户，并询问是否需要用刚才那张照片重新分析一次；不要说还在分析中。"
        "只有当用户刚才那张照片明显不可用或用户主动想换图时，才引导重新上传清晰正脸照。"
    )


def _deep_report_outcome_content(
    task: dict[str, Any],
    status: str,
    *,
    latest_report: dict[str, Any] | None,
) -> str:
    if status == "failed":
        error = str(task.get("last_error") or "").strip()
        suffix = f"失败原因：{error[:120]}。" if error else ""
        return (
            "【深度分析报告失败状态】最近一次深度分析报告没有生成成功。"
            f"{suffix}"
            "只说明深度分析报告失败，不要复述图片分析、肌肤日记或其他任务状态；"
            "请自然告诉用户这次没有生成出来，并询问是否需要重新生成一次；不要说还在生成中，也不要编造报告内容。"
        )
    report_time = ""
    if latest_report:
        raw_time = str(latest_report.get("create_time") or latest_report.get("update_time") or "").strip()
        report_time = f"最近报告时间：{raw_time}。" if raw_time else ""
    return (
        "【深度分析报告完成状态】最近一次深度分析报告已经生成完成。"
        f"{report_time}"
        "只说明深度分析报告完成，不要复述图片分析、肌肤日记或其他任务状态；"
        "请自然提醒用户去「我的报告」页面或【深度分析报告】页面查看结果；不要重复触发生成。"
    )


def _is_final_failed_outcome(task: dict[str, Any], status: str) -> bool:
    if status != "failed":
        return False
    if str(task.get("summary") or "").strip() == "final_failure":
        return True
    try:
        attempt = int(task.get("attempt") or 0)
        max_attempts = int(task.get("max_attempts") or 0)
    except (TypeError, ValueError):
        return False
    return max_attempts > 0 and attempt + 1 >= max_attempts


def _task_record_to_dict(task: Any) -> dict[str, Any] | None:
    if task is None:
        return None
    if isinstance(task, dict):
        return task
    try:
        data = asdict(task)
    except TypeError:
        return None
    return data if isinstance(data, dict) else None


_DEEP_REPORT_TOOLS = ("deep_report_chat", "deep_research")
_DEEP_REPORT_BLOCKED_STATUSES = {"blocked", "deduped"}


@dataclass(slots=True)
class DeepReportGateAttentionProvider:
    """Inject facts for recent deep-report gate denials.

    Gate observations are persisted in conversation history. This provider reads
    the latest blocked/deduped invocation and the current readiness snapshot, so
    the main agent can correct stale gate observations without relying on query
    regexes.
    """

    tool_invocation_repo: Any
    readiness: "DeepReportReadiness"
    emission_state: dict[str, Any]
    source: str = "deep_report_gate_state"
    priority: int = 18
    placement: str = "after_history"
    max_emits_per_signature: int = 3

    async def collect_attention(
        self,
        ctx: ContextBuildContext,
    ) -> list[AttentionPacket]:
        latest = await self._latest_invocation(ctx.tenant_key)
        if not latest:
            return []

        invocation_status = str(latest.get("status") or "").strip().lower()
        if invocation_status not in _DEEP_REPORT_BLOCKED_STATUSES:
            self._clear_state(ctx.tenant_key)
            return []

        payload = _parse_invocation_payload(latest.get("output_summary"))
        reason = str(payload.get("reason") or latest.get("last_error") or "").strip()
        current = await self.readiness.check_deep_report(ctx.tenant_key)
        signature = ":".join([
            str(latest.get("invocation_id") or ""),
            invocation_status,
            reason,
            str(current.allowed),
            current.reason,
            current.phase,
        ])
        if not self._should_emit(ctx.tenant_key, signature):
            return []

        previous_focus = str(payload.get("message_focus") or "").strip()
        base = (
            "【深度报告工具状态】上一次 deep_report_chat 没有真正触发业务任务，"
            f"原因是 {reason or 'unknown'}。"
        )
        if current.allowed:
            content = (
                f"{base}"
                "现在前置条件已满足。"
                "如果用户仍在延续深度报告、报告进度或同一皮肤焦虑话题，本轮应重新调用 deep_report_chat；"
                "不要只口头承诺“稍后/已经安排/会自动触发”。"
                "如果用户已经切到无关闲聊，不要主动拉回深度报告。"
            )
        else:
            current_focus = str(current.message_focus or "").strip()
            focus = current_focus or previous_focus
            content = (
                f"{base}"
                f"{focus}"
                "请按当前状态自然说明，不要编造深度报告已经触发或生成。"
            )

        return [AttentionPacket(
            content=content,
            source=self.source,
            priority=self.priority,
            lifetime="one_turn",
            placement=self.placement,
            metadata={
                "invocation_id": str(latest.get("invocation_id") or ""),
                "previous_status": invocation_status,
                "previous_reason": reason,
                "current_allowed": current.allowed,
                "current_reason": current.reason,
            },
        )]

    async def _latest_invocation(self, tenant_key: str) -> dict[str, Any] | None:
        finder = getattr(self.tool_invocation_repo, "find_latest_for_tools", None)
        if finder is None:
            return None
        try:
            return await finder(tenant_key=tenant_key, tool_names=_DEEP_REPORT_TOOLS)
        except Exception:
            return None

    def _should_emit(self, tenant_key: str, signature: str) -> bool:
        key = f"{tenant_key}:{self.source}"
        signature_key = f"{key}:signature"
        count_key = f"{key}:count"
        previous = self.emission_state.get(signature_key)
        if previous != signature:
            self.emission_state[signature_key] = signature
            self.emission_state[count_key] = 1
            return True

        count = int(self.emission_state.get(count_key) or 0)
        if count >= max(1, int(self.max_emits_per_signature or 1)):
            return False
        self.emission_state[count_key] = count + 1
        return True

    def _clear_state(self, tenant_key: str) -> None:
        key = f"{tenant_key}:{self.source}"
        self.emission_state.pop(f"{key}:signature", None)
        self.emission_state.pop(f"{key}:count", None)


def _parse_invocation_payload(value: Any) -> dict[str, Any]:
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def main_agent_document_specs() -> list[DocumentContextSpec]:
    """Document specs for the main Mojing agent."""

    return [
        DocumentContextSpec("USER.md", _plain_document),
        DocumentContextSpec("SOUL.md", _format_soul_document),
    ]


async def _selfie_age_note(
    image_repo: "ImageRepository",
    tenant_key: str,
    *,
    stale_after_hours: int = 8,
) -> str | None:
    last_time = await image_repo.get_latest_time(tenant_key)
    if last_time is None:
        return None

    now = datetime.utcnow()
    if last_time.tzinfo is not None:
        now = datetime.now(timezone.utc)

    delta = now - last_time.replace(tzinfo=None) if last_time.tzinfo is None else now - last_time
    if delta < timedelta(hours=max(0, stale_after_hours)):
        return None

    days = delta.days
    hours = delta.seconds // 3600

    if days == 0:
        age = f"今日（约{hours}小时前）" if hours > 0 else "刚刚"
    elif days == 1:
        age = "昨天"
    else:
        age = f"{days}天前"

    return (
        "【自拍时效】"
        f"上次自拍是{age}。"
        "若用户要判断当前肤况、更新肌肤日记或生成/重生成深度报告，本轮没有新自拍时，先建议补一张当前清晰自拍。"
        "若用户明确不拍、选择沿用旧图，先调用 retrieve_evidence(route=historical_image) 拿到之前的图片证据，再继续处理。"
    )


def _current_time_note() -> str:
    now_cn = datetime.now(timezone(timedelta(hours=8)))
    weekday_map = {
        "Monday": "周一",
        "Tuesday": "周二",
        "Wednesday": "周三",
        "Thursday": "周四",
        "Friday": "周五",
        "Saturday": "周六",
        "Sunday": "周日",
    }
    weekday_en = now_cn.strftime("%A")
    weekday_cn = weekday_map.get(weekday_en, weekday_en)
    return (
        f"当前时间（北京，UTC+8）：{now_cn.strftime('%Y-%m-%d %H:%M:%S')} {weekday_cn}。"
        "涉及提醒、定时任务或“多久之后”的请求时，必须基于这个时间计算未来时间。"
    )


def _plain_document(content: str) -> str:
    return content.strip()


def _format_soul_document(content: str) -> str:
    return (
        "【用户沟通偏好 / 红线 · SOUL.md】\n"
        "以下是该用户明确表达过的长期沟通偏好、硬拒或红线，本轮回复要遵守；"
        "若她自己重新起头某条红线，可以接，但不要绕回劝说：\n\n"
        + content.strip()
    )
