"""Monitor wait_external runtime tasks until their business result is visible."""

from __future__ import annotations

import asyncio
import socket
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from loguru import logger

from simpleclaw.runtime.task_protocol import TaskEnvelope
from Mojing.runtime.activations import (
    build_deep_report_completion_activation,
    build_image_analysis_completion_activation,
    build_runtime_task_failure_activation,
)
from Mojing.runtime.task_types import MojingTaskType
from Mojing.runtime.streams import MojingTaskStream
from Mojing.runtime.obligation_actions import DEPENDENCY_CABINET_PRODUCT_RESEARCH_SUCCEEDED
from Mojing.runtime.obligation_dispatcher import dispatch_obligations_for_dependency
from Mojing.storage.deep_report_repo import DeepReportRepository
from Mojing.storage.image_repo import ImageRepository
from Mojing.storage.runtime_task_repo import RuntimeTaskRepository
from Mojing.storage.skincare_cabinet_repo import SkincareCabinetRepository
from Mojing.storage.skin_profile_repo import SkinProfileRepository

if TYPE_CHECKING:
    from Mojing.runtime.activations import RuntimeActivationService
    from simpleclaw.runtime.services import RuntimeServices
    from Mojing.storage.document_repo import DocumentRepository
    from Mojing.storage.obligation_repo import ObligationRepository


class WaitExternalTaskMonitor:
    """Poll business tables and finalize runtime tasks that are waiting on external systems."""

    def __init__(
        self,
        *,
        runtime_task_repo: RuntimeTaskRepository,
        deep_report_repo: DeepReportRepository,
        skin_profile_repo: SkinProfileRepository,
        skincare_cabinet_repo: SkincareCabinetRepository,
        image_repo: ImageRepository | None = None,
        document_repo: "DocumentRepository | None" = None,
        runtime: "RuntimeServices | None" = None,
        activation_service: "RuntimeActivationService | None" = None,
        obligation_repo: "ObligationRepository | None" = None,
        interval_s: float = 30.0,
        batch_size: int = 100,
        image_timeout_min: int = 5,
        deep_research_timeout_min: int = 30,
        cabinet_product_timeout_min: int = 3,
        claimed_by_values: tuple[str, ...] | list[str] | None = None,
        claimed_by_hosts: tuple[str, ...] | list[str] | None = None,
    ) -> None:
        self._runtime_task_repo = runtime_task_repo
        self._deep_report_repo = deep_report_repo
        self._skin_profile_repo = skin_profile_repo
        self._skincare_cabinet_repo = skincare_cabinet_repo
        self._image_repo = image_repo
        self._document_repo = document_repo
        self._runtime = runtime
        self._activation_service = activation_service
        self._obligation_repo = obligation_repo
        self._interval_s = max(1.0, float(interval_s))
        self._batch_size = max(1, int(batch_size))
        self._image_timeout = timedelta(minutes=max(1, image_timeout_min))
        self._deep_timeout = timedelta(minutes=max(1, deep_research_timeout_min))
        self._cabinet_timeout = timedelta(minutes=max(1, cabinet_product_timeout_min))
        self._claimed_by_values = tuple(
            value for value in (claimed_by_values or ())
            if str(value or "").strip()
        )
        self._claimed_by_hosts = tuple(
            host for host in (claimed_by_hosts or (socket.gethostname(),))
            if str(host or "").strip()
        )
        self._running = False

    async def run(self) -> None:
        self._running = True
        logger.info(
            "WaitExternalTaskMonitor started interval_s={} claimed_by_values={} claimed_by_hosts={}",
            self._interval_s,
            list(self._claimed_by_values),
            list(self._claimed_by_hosts),
        )
        while self._running:
            try:
                await self.check_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("WaitExternalTaskMonitor check failed: {}", exc)
            await asyncio.sleep(self._interval_s)

    def stop(self) -> None:
        self._running = False

    async def check_once(self) -> None:
        tasks = await self._runtime_task_repo.list_wait_external(
            task_types=[
                MojingTaskType.IMAGE_ANALYSIS,
                MojingTaskType.DEEP_RESEARCH,
                MojingTaskType.CABINET_PRODUCT_RESEARCH,
            ],
            limit=self._batch_size,
            claimed_by_values=self._claimed_by_values,
            claimed_by_hosts=self._claimed_by_hosts,
        )
        for task in tasks:
            await self._check_task(task)

    async def _check_task(self, task: dict[str, Any]) -> None:
        task_type = str(task.get("task_type") or "")
        if task_type == MojingTaskType.DEEP_RESEARCH:
            await self._check_deep_research(task)
        elif task_type == MojingTaskType.IMAGE_ANALYSIS:
            await self._check_image_analysis(task)
        elif task_type == MojingTaskType.CABINET_PRODUCT_RESEARCH:
            await self._check_cabinet_product_research(task)

    async def _check_deep_research(self, task: dict[str, Any]) -> None:
        payload = task.get("payload") or {}
        report_user_id = str(payload.get("user_id") or task.get("tenant_key") or "")
        if not report_user_id:
            return
        created_at = task.get("created_at") or ""
        trace_id = str(payload.get("trace_id") or task.get("trace_id") or "").strip() or None
        report_id = str(payload.get("report_id") or "").strip() or None
        session_id = str(payload.get("session_id") or task.get("session_key") or "").strip() or None

        error_row = await self._deep_report_repo.find_error_since(
            tenant_key=report_user_id,
            since=created_at,
            trace_id=trace_id,
            report_id=report_id,
            session_id=session_id,
        )
        if error_row is not None:
            error = "deep research business status=error"
            await self._runtime_task_repo.mark_failed(str(task["task_id"]), error, summary="final_failure")
            await self._notify_task_failed(task, error)
            return

        done_row = await self._deep_report_repo.find_done_since(
            tenant_key=report_user_id,
            since=created_at,
            trace_id=trace_id,
            report_id=report_id,
            session_id=session_id,
        )
        if done_row is not None:
            resolved_report_id = str(done_row.get("report_id") or report_id or "").strip()
            await self._runtime_task_repo.mark_succeeded(
                str(task["task_id"]),
                summary="deep research report is available",
                business_ref_type="deep_report",
                business_ref_id=resolved_report_id or None,
            )
            await self._notify_deep_report_succeeded(task, report_id=resolved_report_id)
            return

        if _is_timeout(created_at, self._deep_timeout):
            error = "deep research completion timeout"
            await self._runtime_task_repo.mark_failed(str(task["task_id"]), error, summary="final_failure")
            await self._notify_task_failed(task, error)

    async def _check_image_analysis(self, task: dict[str, Any]) -> None:
        payload = task.get("payload") or {}
        tenant_key = str(task.get("tenant_key") or payload.get("tenant_key") or "")
        if not tenant_key:
            return
        created_at = task.get("created_at") or ""
        profile = await self._skin_profile_repo.find_profile_since(
            tenant_key=tenant_key,
            since=created_at,
            image_id=str(payload.get("image_id") or "").strip() or None,
            image_ref=str(payload.get("image") or payload.get("image_ref") or "").strip() or None,
            message_id=str(payload.get("message_id") or "").strip() or None,
        )
        if profile is not None:
            sync_status = str(profile.get("sync_status") or "").strip().lower()
            if self._image_repo is not None:
                job_id = str(payload.get("job_id") or "").strip()
                try:
                    if job_id:
                        await self._image_repo.mark_succeeded(
                            job_id,
                            profile_id=profile.get("profile_id"),
                        )
                    else:
                        await self._image_repo.mark_succeeded_for_profile(
                            tenant_key,
                            profile,
                        )
                except Exception as exc:
                    logger.warning("WaitExternalTaskMonitor image job mark_succeeded failed: {}", exc)
            if sync_status in {"synced", "skipped"}:
                await self._runtime_task_repo.mark_task_succeeded(
                    str(task["task_id"]),
                    summary="image analysis profile synced to USER.md",
                )
                await self._dispatch_image_analysis_obligations(task, profile)
                await self._notify_image_analysis_succeeded(task, profile)
                return
            if sync_status == "failed":
                error = "skin profile sync failed"
                await self._runtime_task_repo.mark_failed(str(task["task_id"]), error, summary="final_failure")
                await self._notify_task_failed(task, error)
                return
            sync_enqueued = await self._enqueue_skin_profile_sync(task, profile)
            if not sync_enqueued:
                return
            return

        if _is_timeout(created_at, self._image_timeout):
            if self._image_repo is not None:
                job_id = str(payload.get("job_id") or "").strip()
                if job_id:
                    try:
                        await self._image_repo.mark_failed(job_id, error="image analysis completion timeout")
                    except Exception as exc:
                        logger.warning("WaitExternalTaskMonitor image job mark_failed failed: {}", exc)
            error = "image analysis completion timeout"
            await self._runtime_task_repo.mark_failed(str(task["task_id"]), error, summary="final_failure")
            await self._notify_task_failed(task, error)

    async def _check_cabinet_product_research(self, task: dict[str, Any]) -> None:
        payload = task.get("payload") or {}
        user_id = str(payload.get("userId") or task.get("tenant_key") or "").strip()
        brand = str(payload.get("brand") or "").strip()
        product_name = str(payload.get("productName") or "").strip()
        created_at = task.get("created_at") or ""
        if not user_id or not brand or not product_name:
            return

        product = await self._skincare_cabinet_repo.find_latest_by_name(
            user_id=user_id,
            brand=brand,
            product_name=product_name,
        )
        if product is not None and _product_visible_since(product, created_at):
            product_id = int(product.get("id") or 0)
            await self._runtime_task_repo.mark_succeeded(
                str(task["task_id"]),
                summary=f"cabinet product record available product_id={product_id}",
                business_ref_type="skincare_cabinet_product",
                business_ref_id=str(product_id),
                output_json={
                    "product_id": product_id,
                    "brand": brand,
                    "product_name": product_name,
                    "in_cabinet": int(product.get("in_cabinet") or 0),
                },
            )
            await dispatch_obligations_for_dependency(
                obligation_repo=self._obligation_repo,
                runtime=self._runtime,
                tenant_key=user_id,
                dependency_type=DEPENDENCY_CABINET_PRODUCT_RESEARCH_SUCCEEDED,
                source_session_key=str(task.get("session_key") or ""),
                source_task_id=str(task.get("task_id") or ""),
                dependency_business_ref_type="skincare_cabinet_product",
                dependency_business_ref_id=str(product_id),
                document_repo=self._document_repo,
            )
            return

        if _is_timeout(created_at, self._cabinet_timeout):
            await self._runtime_task_repo.mark_task_failed(
                str(task["task_id"]),
                error="cabinet product research completion timeout",
            )

    async def _notify_task_failed(self, task: dict[str, Any], error: str) -> None:
        if self._activation_service is None:
            return
        request = build_runtime_task_failure_activation(
            tenant_key=str(task.get("tenant_key") or (task.get("payload") or {}).get("tenant_key") or "").strip(),
            source_session_key=str(task.get("session_key") or (task.get("payload") or {}).get("session_key") or ""),
            task_id=str(task.get("task_id") or ""),
            task_type=str(task.get("task_type") or ""),
            error=error,
            business_ref_type=str(task.get("business_ref_type") or "") or None,
            business_ref_id=str(task.get("business_ref_id") or (task.get("payload") or {}).get("job_id") or "") or None,
        )
        if request is None:
            return
        try:
            await self._activation_service.enqueue(request)
        except Exception as exc:
            logger.warning(
                "WaitExternalTaskMonitor failure activation enqueue failed: type={} task_id={} err={}",
                task.get("task_type"),
                task.get("task_id"),
                exc,
            )

    async def _notify_deep_report_succeeded(self, task: dict[str, Any], *, report_id: str) -> None:
        if self._activation_service is None:
            return
        request = build_deep_report_completion_activation(
            tenant_key=str(task.get("tenant_key") or (task.get("payload") or {}).get("user_id") or "").strip(),
            source_session_key=str(task.get("session_key") or ""),
            task_id=str(task.get("task_id") or ""),
            report_id=report_id,
        )
        if request is None:
            return
        try:
            await self._activation_service.enqueue(request)
        except Exception as exc:
            logger.warning(
                "WaitExternalTaskMonitor deep report completion activation enqueue failed: task_id={} err={}",
                task.get("task_id"),
                exc,
            )

    async def _notify_image_analysis_succeeded(
        self,
        task: dict[str, Any],
        profile: dict[str, Any],
    ) -> None:
        if self._activation_service is None:
            return
        payload = task.get("payload") or {}
        request = build_image_analysis_completion_activation(
            tenant_key=str(task.get("tenant_key") or payload.get("tenant_key") or "").strip(),
            source_session_key=str(task.get("session_key") or payload.get("session_key") or ""),
            task_id=str(task.get("task_id") or ""),
            profile_id=str(profile.get("profile_id") or ""),
        )
        if request is None:
            return
        try:
            await self._activation_service.enqueue(request)
        except Exception as exc:
            logger.warning(
                "WaitExternalTaskMonitor image analysis completion activation enqueue failed: task_id={} err={}",
                task.get("task_id"),
                exc,
            )

    async def _dispatch_image_analysis_obligations(
        self,
        task: dict[str, Any],
        profile: dict[str, Any],
    ) -> None:
        from Mojing.runtime.obligations import (
            DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
            dispatch_obligations_for_dependency,
        )

        payload = task.get("payload") or {}
        tenant_key = str(task.get("tenant_key") or payload.get("tenant_key") or "").strip()
        if not tenant_key:
            return
        try:
            await dispatch_obligations_for_dependency(
                obligation_repo=self._obligation_repo,
                runtime=self._runtime,
                tenant_key=tenant_key,
                dependency_type=DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
                source_session_key=str(task.get("session_key") or payload.get("session_key") or ""),
                profile_id=profile.get("profile_id"),
                source_task_id=str(task.get("task_id") or ""),
                document_repo=self._document_repo,
            )
        except Exception as exc:
            logger.warning(
                "WaitExternalTaskMonitor obligation dispatch failed: task_id={} err={}",
                task.get("task_id"),
                exc,
            )

    async def _enqueue_skin_profile_sync(
        self,
        task: dict[str, Any],
        profile: dict[str, Any],
    ) -> bool:
        """After image analysis succeeds, enqueue USER.md sync as its own runtime task."""
        if self._runtime is None:
            return False
        sync_status = str(profile.get("sync_status") or "").strip().lower()
        if sync_status and sync_status != "pending":
            return True

        payload = task.get("payload") or {}
        tenant_key = str(task.get("tenant_key") or payload.get("tenant_key") or profile.get("tenant_key") or "").strip()
        if not tenant_key:
            return True
        session_key = str(
            task.get("session_key")
            or payload.get("session_key")
            or profile.get("session_key")
            or f"main:{tenant_key}"
        ).strip()
        profile_id = profile.get("profile_id")

        trace_id = str(task.get("trace_id") or "").strip()
        task_kwargs = {
            "task_type": MojingTaskType.SKIN_PROFILE_SYNC,
            "payload": {
                "tenant_key": tenant_key,
                "session_key": session_key,
                "profile_id": profile_id,
                "source": "image_analysis_monitor",
                "source_task_id": task.get("task_id"),
            },
            "stream": MojingTaskStream.POSTPROCESS,
            "tenant_key": tenant_key,
            "session_key": session_key,
            "scope_key": f"postprocess:{tenant_key}:USER.md",
            "service_role": "mojing:skin-profile-sync:auto",
        }
        if trace_id:
            task_kwargs["trace_id"] = trace_id
        sync_task = TaskEnvelope(**task_kwargs)
        try:
            queue_id = await self._runtime.submit_task(
                sync_task,
                summary="auto sync image analysis profile to USER.md",
            )
        except Exception as exc:
            logger.warning(
                "WaitExternalTaskMonitor enqueue skin_profile_sync failed: tenant={} profile={} err={}",
                tenant_key,
                profile_id,
                exc,
            )
            return False
        logger.info(
            "WaitExternalTaskMonitor enqueued skin_profile_sync: tenant={} profile={} queue_id={}",
            tenant_key,
            profile_id,
            queue_id,
        )
        return True


def _is_timeout(created_at: Any, timeout: timedelta) -> bool:
    parsed = _parse_datetime(created_at)
    if parsed is None:
        return False
    return datetime.utcnow() - parsed > timeout


def _product_visible_since(product: dict[str, Any], created_at: Any) -> bool:
    task_created = _parse_datetime(created_at)
    if task_created is None:
        return True
    product_updated = _parse_datetime(product.get("update_time")) or _parse_datetime(product.get("create_time"))
    if product_updated is None:
        return False
    return product_updated >= task_created


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


TriggeredTaskMonitor = WaitExternalTaskMonitor
