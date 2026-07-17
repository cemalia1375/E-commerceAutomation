"""Readiness checks for deep report capabilities."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from Mojing.harness.readiness.base import (
    ACTIVE_STATUSES,
    CapabilityDecision,
    is_after,
    normalize_status,
    status_of,
    stringify_time,
)
from Mojing.harness.readiness.image_analysis import ImageAnalysisReadiness
from Mojing.runtime.task_types import MojingTaskType

if TYPE_CHECKING:
    from Mojing.storage.deep_report_repo import DeepReportRepository
    from Mojing.storage.document_repo import DocumentRepository
    from Mojing.storage.image_repo import ImageRepository
    from Mojing.storage.runtime_task_repo import RuntimeTaskRepository
    from Mojing.storage.skin_profile_repo import SkinProfileRepository


_REPORT_READY_IMAGE_PHASE = "ready"


class DeepReportReadiness:
    """Computes business readiness for deep report tools."""

    def __init__(
        self,
        *,
        document_repo: "DocumentRepository | None" = None,
        image_repo: "ImageRepository | None" = None,
        skin_profile_repo: "SkinProfileRepository | None" = None,
        image_analysis_readiness: "ImageAnalysisReadiness | None" = None,
        runtime_task_repo: "RuntimeTaskRepository | None" = None,
        deep_report_repo: "DeepReportRepository | None" = None,
        timezone_name: str = "Asia/Shanghai",
    ) -> None:
        self._document_repo = document_repo
        self._image_analysis_readiness = image_analysis_readiness or ImageAnalysisReadiness(
            image_repo=image_repo,
            document_repo=document_repo,
            runtime_task_repo=runtime_task_repo,
            skin_profile_repo=skin_profile_repo,
            timezone_name=timezone_name,
        )
        self._runtime_task_repo = runtime_task_repo
        self._deep_report_repo = deep_report_repo

    async def check_deep_report(self, tenant_key: str) -> CapabilityDecision:
        tenant_key = str(tenant_key or "").strip()
        if not tenant_key:
            return CapabilityDecision(
                allowed=False,
                capability="deep_report",
                reason="missing_context",
                phase="blocked",
                message_focus="当前缺少用户上下文，先不要触发深度分析报告。",
            )

        facts: dict[str, Any] = {"tenant_key": tenant_key}
        image_analysis_status = await self._latest_image_analysis_status(tenant_key)
        if image_analysis_status is not None:
            facts.update(image_analysis_status.facts)

        latest_image_at = (image_analysis_status.latest_job or {}).get("created_at")
        facts["latest_image_at"] = stringify_time(latest_image_at)
        facts["has_fresh_image_today"] = image_analysis_status.has_fresh_image_today
        image_phase = _deep_report_image_phase(image_analysis_status)
        facts["image_analysis_phase"] = image_phase
        facts["latest_image_status"] = image_phase

        deep_task = await self._latest_task(tenant_key, MojingTaskType.DEEP_RESEARCH)
        deep_status = status_of(deep_task)
        facts["deep_report_task_status"] = deep_status
        facts["deep_report_task_id"] = deep_task.get("task_id") if deep_task else None
        dispatch_task = await self._latest_deep_report_dispatch(tenant_key)
        dispatch_status = status_of(dispatch_task)
        facts["deep_report_dispatch_status"] = dispatch_status
        facts["deep_report_dispatch_task_id"] = dispatch_task.get("task_id") if dispatch_task else None

        facts["has_learned_skin_profile"] = image_analysis_status.has_learned_skin_profile

        user_meta = await self._user_md_metadata(tenant_key)
        facts["user_md_updated_at"] = user_meta.get("updated_at") if user_meta else None
        facts["user_md_content_hash"] = user_meta.get("content_hash") if user_meta else None

        latest_report = await self._latest_deep_report(tenant_key, deep_task)
        facts["latest_deep_report_at"] = _report_time(latest_report)
        facts["latest_deep_report_id"] = latest_report.get("report_id") if latest_report else None
        facts["user_md_updated_after_latest_report"] = is_after(
            facts.get("user_md_updated_at"),
            facts.get("latest_deep_report_at"),
        )

        if deep_status in ACTIVE_STATUSES:
            return CapabilityDecision(
                allowed=False,
                capability="deep_report",
                reason="deep_report_running",
                phase=deep_status,
                message_focus=(
                    "已有深度分析报告任务正在处理中，不要重复触发。"
                    "请告诉用户报告还在处理，稍后去报告页或深度报告会话查看。"
                ),
                facts=facts,
            )
        if image_phase in ACTIVE_STATUSES:
            return CapabilityDecision(
                allowed=False,
                capability="deep_report",
                reason="image_analysis_running",
                phase=image_phase,
                message_focus=(
                    "基础图片分析还在处理中，暂时不能生成深度报告。"
                    "先回应用户当前问题，再简短说明当前情况；"
                    "如果已答应生成，就说等分析完成后继续处理。"
                ),
                facts=facts,
            )

        if image_phase != _REPORT_READY_IMAGE_PHASE or (
            not facts["has_fresh_image_today"] and not facts["user_md_updated_after_latest_report"]
        ):
            return CapabilityDecision(
                allowed=False,
                capability="deep_report",
                reason="need_fresh_photo",
                phase="stale" if image_phase == _REPORT_READY_IMAGE_PHASE else image_phase,
                message_focus=(
                    "深度报告这次还不能生成：需要先有一张今天可用的清晰正脸自拍。"
                    "不要说报告已经安排；请自然告诉用户先上传或重拍一张清晰正脸照，"
                    "等基础分析可用后再生成新的深度分析报告。"
                ),
                facts=facts,
            )

        return CapabilityDecision(
            allowed=True,
            capability="deep_report",
            reason="ready",
            phase="ready",
            message_focus="深度分析报告前置条件已满足，可以触发。",
            facts=facts,
        )

    async def _latest_image_analysis_status(self, tenant_key: str):
        return await self._image_analysis_readiness.get_latest_status(tenant_key)

    async def _latest_task(self, tenant_key: str, task_type: str) -> dict[str, Any] | None:
        if self._runtime_task_repo is None:
            return None
        return await self._runtime_task_repo.find_latest_task_for(
            tenant_key=tenant_key,
            task_type=str(task_type),
        )

    async def _latest_deep_report_dispatch(self, tenant_key: str) -> dict[str, Any] | None:
        if self._runtime_task_repo is None or not hasattr(self._runtime_task_repo, "find_latest_by_scope_key"):
            return None
        session_key = f"deep_report:{tenant_key}"
        return await self._runtime_task_repo.find_latest_by_scope_key(
            tenant_key=tenant_key,
            task_type=str(MojingTaskType.SUBAGENT_DISPATCH),
            scope_key=f"{MojingTaskType.SUBAGENT_DISPATCH}:{session_key}",
        )

    async def _user_md_metadata(self, tenant_key: str) -> dict[str, Any] | None:
        if self._document_repo is None or not hasattr(self._document_repo, "get_metadata"):
            return None
        return await self._document_repo.get_metadata(tenant_key, "USER.md")

    async def _latest_deep_report(
        self,
        tenant_key: str,
        deep_task: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if self._deep_report_repo is None:
            return None
        latest = await self._deep_report_repo.find_latest(tenant_key)
        report_user_id = _payload_user_id(deep_task)
        if latest is None and report_user_id and report_user_id != tenant_key:
            latest = await self._deep_report_repo.find_latest(report_user_id)
        return latest


def _payload_user_id(task: dict[str, Any] | None) -> str:
    payload = (task or {}).get("payload")
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("user_id") or "").strip()


def _report_time(report: dict[str, Any] | None) -> str | None:
    if not report:
        return None
    return stringify_time(report.get("create_time") or report.get("update_time"))


def _deep_report_image_phase(status: Any) -> str:
    if status is None:
        return "no_photo"
    if not getattr(status, "has_image", False):
        return "no_photo"
    phase = normalize_status(getattr(status, "phase", ""))
    if phase in ACTIVE_STATUSES or phase in {"ready", "asset_available", "failed", "no_photo"}:
        return phase
    if phase in {"profile_available", "succeeded", "user_md_synced", "profile_sync_pending", "profile_sync_failed"}:
        return _REPORT_READY_IMAGE_PHASE
    latest_job = getattr(status, "latest_job", None) or {}
    raw = normalize_status(latest_job.get("status") or "stored") or "stored"
    if raw in ACTIVE_STATUSES:
        return raw
    if raw in {"succeeded", "profile_available", "user_md_synced"}:
        return _REPORT_READY_IMAGE_PHASE
    if raw in {"uploaded", "stored", "asset_available", "", "unknown"}:
        return "asset_available"
    if raw == "failed":
        return "failed"
    return raw
