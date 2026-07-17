"""Readiness/status view for image analysis jobs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from Mojing.harness.readiness.base import (
    ACTIVE_STATUSES,
    normalize_status,
    parse_time,
    status_of,
    stringify_time,
)
from Mojing.runtime.task_types import MojingTaskType

if TYPE_CHECKING:
    from Mojing.storage.document_repo import DocumentRepository
    from Mojing.storage.image_repo import ImageRepository
    from Mojing.storage.runtime_task_repo import RuntimeTaskRepository
    from Mojing.storage.skin_profile_repo import SkinProfileRepository


_LEARNED_SKIN_PROFILE_HEADING = "## Learned Skin Profile"
_RAW_ACTIVE_STATUSES = {"queued", "running", "wait_external"}


def normalize_image_analysis_phase(status: Any) -> str:
    """Map image job DB statuses to canonical business phases.

    RuntimeTask owns queued/running/wait_external. The image job table records
    the durable business asset/result state.
    """
    raw = normalize_status(status)
    if raw in {"", "unknown"}:
        return "asset_available"
    if raw in {"uploaded", "stored"}:
        return "asset_available"
    if raw in _RAW_ACTIVE_STATUSES:
        return raw
    if raw in {"profile_available", "succeeded", "user_md_synced"}:
        return "ready"
    if raw == "failed":
        return "failed"
    return raw


@dataclass(slots=True)
class ImageAnalysisStatus:
    phase: str
    latest_job: dict[str, Any] | None = None
    has_image: bool = False
    has_fresh_image_today: bool = False
    has_learned_skin_profile: bool = False
    facts: dict[str, Any] = field(default_factory=dict)


class ImageAnalysisReadiness:
    """Reads the latest image analysis business state for a tenant."""

    def __init__(
        self,
        *,
        image_repo: "ImageRepository | None" = None,
        document_repo: "DocumentRepository | None" = None,
        runtime_task_repo: "RuntimeTaskRepository | None" = None,
        skin_profile_repo: "SkinProfileRepository | None" = None,
        timezone_name: str = "Asia/Shanghai",
    ) -> None:
        self._image_repo = image_repo
        self._document_repo = document_repo
        self._runtime_task_repo = runtime_task_repo
        self._skin_profile_repo = skin_profile_repo
        self._tz = ZoneInfo(timezone_name)

    async def get_latest_status(self, tenant_key: str) -> ImageAnalysisStatus:
        tenant_key = str(tenant_key or "").strip()
        latest_task = await self._latest_task(tenant_key)
        task_status = status_of(latest_task)
        has_task_image = _task_has_image(latest_task)
        latest_job = await self._job_for_task(tenant_key, latest_task)
        if latest_task is None:
            latest_job = await self._latest_job(tenant_key)
        task_matches_job = _task_matches_job(latest_task, latest_job)
        user_md = await self._user_md(tenant_key)
        has_lsp = _has_learned_skin_profile(user_md)

        if latest_job is None and not has_task_image:
            return ImageAnalysisStatus(
                phase="no_photo",
                latest_job=None,
                has_image=False,
                has_fresh_image_today=False,
                has_learned_skin_profile=has_lsp,
                facts={
                    "tenant_key": tenant_key,
                    "phase": "no_photo",
                    "image_analysis_task_status": task_status,
                    "image_analysis_task_id": (latest_task or {}).get("task_id"),
                    "has_learned_skin_profile": has_lsp,
                },
            )

        if latest_task is not None:
            raw_status = task_status or "unknown"
            phase = _phase_from_runtime_task(task_status)
            created_at = _task_time(latest_task) or (latest_job or {}).get("created_at")
            updated_at = latest_task.get("updated_at") or (latest_job or {}).get("updated_at")
        else:
            raw_status = normalize_status((latest_job or {}).get("status") or "stored") or "stored"
            job_phase = normalize_image_analysis_phase(raw_status) if latest_job is not None else ""
            phase = _merge_image_analysis_phase(
                job_phase=job_phase,
                task_status=task_status,
                task_applies=task_matches_job or (latest_job is None and has_task_image),
            )
            created_at = (latest_job or {}).get("created_at")
            updated_at = (latest_job or {}).get("updated_at")
        latest_profile = await self._latest_profile(tenant_key)
        profile_sync_status = normalize_status((latest_profile or {}).get("sync_status"))
        fresh = self._is_today(created_at)
        facts = {
            "tenant_key": tenant_key,
            "phase": phase,
            "latest_image_job_id": (latest_job or {}).get("job_id"),
            "latest_image_source": "runtime_task" if latest_task is not None else "image_job",
            "latest_image_status": phase,
            "latest_image_status_raw": raw_status,
            "latest_image_at": stringify_time(created_at),
            "latest_image_updated_at": stringify_time(updated_at),
            "latest_image_last_error": (latest_job or {}).get("last_error"),
            "image_analysis_task_status": task_status,
            "image_analysis_task_id": (latest_task or {}).get("task_id"),
            "image_analysis_task_matches_latest_job": task_matches_job,
            "has_fresh_image_today": fresh,
            "has_learned_skin_profile": has_lsp,
            "latest_profile_id": (latest_profile or {}).get("profile_id"),
            "latest_profile_sync_status": profile_sync_status,
            "latest_profile_synced_to_user_doc_at": stringify_time(
                (latest_profile or {}).get("synced_to_user_doc_at")
            ),
            "latest_profile_created_at": stringify_time((latest_profile or {}).get("created_at")),
            "latest_profile_sync_error": (latest_profile or {}).get("sync_error"),
        }
        return ImageAnalysisStatus(
            phase=phase,
            latest_job=latest_job,
            has_image=True,
            has_fresh_image_today=fresh,
            has_learned_skin_profile=has_lsp,
            facts=facts,
        )

    async def _latest_job(self, tenant_key: str) -> dict[str, Any] | None:
        if self._image_repo is None or not hasattr(self._image_repo, "find_latest_job"):
            return None
        return await self._image_repo.find_latest_job(tenant_key)

    async def _latest_task(self, tenant_key: str) -> dict[str, Any] | None:
        if self._runtime_task_repo is None or not hasattr(self._runtime_task_repo, "find_latest_task_for"):
            return None
        return await self._runtime_task_repo.find_latest_task_for(
            tenant_key=tenant_key,
            task_type=str(MojingTaskType.IMAGE_ANALYSIS),
        )

    async def _job_for_task(self, tenant_key: str, task: dict[str, Any] | None) -> dict[str, Any] | None:
        if self._image_repo is None or task is None:
            return None
        job_id = str(_task_payload(task).get("job_id") or "").strip()
        if not job_id:
            return None
        getter = getattr(self._image_repo, "get_job_by_id", None)
        if not callable(getter):
            return None
        return await getter(tenant_key, job_id)

    async def _user_md(self, tenant_key: str) -> str:
        if self._document_repo is None:
            return ""
        return await self._document_repo.get(tenant_key, "USER.md") or ""

    async def _latest_profile(self, tenant_key: str) -> dict[str, Any] | None:
        if self._skin_profile_repo is None or not hasattr(self._skin_profile_repo, "get_latest"):
            return None
        return await self._skin_profile_repo.get_latest(tenant_key)

    def _is_today(self, value: Any) -> bool:
        dt = parse_time(value)
        if dt is None:
            return False
        return dt.astimezone(self._tz).date() == datetime.now(self._tz).date()


def _has_learned_skin_profile(content: str) -> bool:
    text = str(content or "")
    idx = text.find(_LEARNED_SKIN_PROFILE_HEADING)
    if idx < 0:
        return False
    body = text[idx + len(_LEARNED_SKIN_PROFILE_HEADING):]
    next_heading = body.find("\n## ")
    if next_heading >= 0:
        body = body[:next_heading]
    return bool(body.strip())


def _merge_image_analysis_phase(
    *,
    job_phase: str,
    task_status: str,
    task_applies: bool,
) -> str:
    if job_phase == "ready":
        return "ready"
    if job_phase == "failed":
        return "failed"
    if task_applies and task_status in ACTIVE_STATUSES:
        return task_status
    if task_applies and task_status == "succeeded":
        return "ready"
    if task_applies and task_status == "failed":
        return "failed"
    return job_phase or "asset_available"


def _phase_from_runtime_task(task_status: str) -> str:
    status = normalize_status(task_status)
    if status in ACTIVE_STATUSES:
        return status
    if status == "succeeded":
        return "ready"
    if status == "failed":
        return "failed"
    return status or "asset_available"


def _task_matches_job(task: dict[str, Any] | None, job: dict[str, Any] | None) -> bool:
    if not task:
        return False
    if not job:
        return _task_has_image(task)
    payload = _task_payload(task)
    comparisons = [
        (payload.get("job_id"), job.get("job_id")),
        (payload.get("image_id"), job.get("image_id")),
        (payload.get("image") or payload.get("image_ref"), job.get("image_ref")),
    ]
    for left, right in comparisons:
        if str(left or "").strip() and str(left or "").strip() == str(right or "").strip():
            return True

    has_identifiers = any(str(left or "").strip() for left, _ in comparisons)
    if has_identifiers:
        return False

    task_created = parse_time(task.get("created_at"))
    job_created = parse_time(job.get("created_at"))
    if task_created is None or job_created is None:
        return True
    return task_created >= job_created


def _task_has_image(task: dict[str, Any] | None) -> bool:
    if not task:
        return False
    payload = _task_payload(task)
    return any(
        str(payload.get(key) or "").strip()
        for key in ("image", "image_ref", "image_id", "job_id")
    )


def _task_payload(task: dict[str, Any] | None) -> dict[str, Any]:
    payload = (task or {}).get("payload")
    return payload if isinstance(payload, dict) else {}


def _task_time(task: dict[str, Any] | None) -> Any:
    if not task:
        return None
    return task.get("completed_at") or task.get("updated_at") or task.get("created_at")
