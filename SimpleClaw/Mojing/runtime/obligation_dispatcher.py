"""Dispatch pending obligations whose dependencies became true."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from Mojing.runtime.obligation_actions import build_obligation_runtime_task

if TYPE_CHECKING:
    from simpleclaw.runtime.services import RuntimeServices
    from Mojing.storage.document_repo import DocumentRepository
    from Mojing.storage.obligation_repo import ObligationRepository


async def dispatch_obligations_for_dependency(
    *,
    obligation_repo: "ObligationRepository | None",
    runtime: "RuntimeServices | None",
    tenant_key: str,
    dependency_type: str,
    source_session_key: str = "",
    profile_id: int | str | None = None,
    source_task_id: str = "",
    dependency_business_ref_type: str = "",
    dependency_business_ref_id: str = "",
    document_repo: "DocumentRepository | None" = None,
) -> list[dict[str, Any]]:
    """Dispatch pending obligations whose dependency has just become true."""
    tenant_key = str(tenant_key or "").strip()
    dependency_type = str(dependency_type or "").strip()
    if obligation_repo is None or runtime is None or not tenant_key or not dependency_type:
        return []

    pending = await obligation_repo.list_pending_for_dependency(
        tenant_key=tenant_key,
        dependency_type=dependency_type,
    )
    user_profile = await _load_user_profile(document_repo, tenant_key)
    dispatched: list[dict[str, Any]] = []
    dispatched_actions: set[str] = set()
    eligible = [
        obligation
        for obligation in pending
        if _matches_dependency_source(obligation, source_task_id=source_task_id)
    ]
    eligible.sort(key=lambda obligation: 0 if _dependency_ref_id(obligation) else 1)
    for obligation in eligible:
        action_type = str(obligation.get("action_type") or "").strip()
        if action_type in dispatched_actions:
            await _cancel_duplicate_obligation(obligation_repo, obligation)
            logger.info(
                "obligation duplicate pending cancelled: tenant={} action={} obligation={}",
                tenant_key,
                action_type,
                obligation.get("obligation_id"),
            )
            continue

        obligation_for_build = _with_dependency_business_ref(
            obligation,
            dependency_business_ref_type=dependency_business_ref_type,
            dependency_business_ref_id=dependency_business_ref_id,
        )
        built = build_obligation_runtime_task(
            obligation_for_build,
            tenant_key=tenant_key,
            source_session_key=source_session_key,
            profile_id=profile_id,
            source_task_id=source_task_id,
            user_profile=user_profile,
        )
        if built is None:
            continue

        task = built.task
        claimed = await obligation_repo.mark_dispatched_if_pending(
            obligation_id=str(obligation.get("obligation_id") or ""),
            dispatched_task_id=task.task_id,
        )
        if not claimed:
            continue

        try:
            queue_id = await runtime.submit_task(task, summary=built.summary)
        except Exception as exc:
            await obligation_repo.revert_dispatched_to_pending(
                obligation_id=str(obligation.get("obligation_id") or ""),
                dispatched_task_id=task.task_id,
            )
            logger.warning(
                "obligation dispatch failed: tenant={} obligation={} task_id={} err={}",
                tenant_key,
                obligation.get("obligation_id"),
                task.task_id,
                exc,
            )
            continue

        dispatched.append({
            "obligation_id": obligation.get("obligation_id"),
            "task_id": task.task_id,
            "queue_id": queue_id,
            "action_type": action_type or obligation.get("action_type"),
        })
        if action_type:
            dispatched_actions.add(action_type)
        logger.info(
            "obligation dispatched: tenant={} obligation={} action={} task_id={} queue_id={}",
            tenant_key,
            obligation.get("obligation_id"),
            obligation.get("action_type"),
            task.task_id,
            queue_id,
        )
    return dispatched


def _matches_dependency_source(obligation: dict[str, Any], *, source_task_id: str) -> bool:
    ref_id = _dependency_ref_id(obligation)
    if ref_id:
        return ref_id == str(source_task_id or "").strip()
    payload = obligation.get("payload") if isinstance(obligation, dict) else {}
    if isinstance(payload, dict) and payload.get("dependency_ref_required"):
        return False
    return True


def _dependency_ref_id(obligation: dict[str, Any]) -> str:
    payload = obligation.get("payload") if isinstance(obligation, dict) else {}
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("dependency_ref_id") or "").strip()


def _with_dependency_business_ref(
    obligation: dict[str, Any],
    *,
    dependency_business_ref_type: str,
    dependency_business_ref_id: str,
) -> dict[str, Any]:
    ref_id = str(dependency_business_ref_id or "").strip()
    ref_type = str(dependency_business_ref_type or "").strip()
    if not ref_id and not ref_type:
        return obligation
    updated = dict(obligation)
    payload = dict(updated.get("payload") or {})
    if ref_id:
        payload.setdefault("dependency_business_ref_id", ref_id)
        payload.setdefault("product_id", ref_id)
    if ref_type:
        payload.setdefault("dependency_business_ref_type", ref_type)
    updated["payload"] = payload
    return updated


async def _cancel_duplicate_obligation(
    obligation_repo: "ObligationRepository",
    obligation: dict[str, Any],
) -> None:
    cancel_one = getattr(obligation_repo, "cancel_pending_obligation", None)
    if not callable(cancel_one):
        return
    obligation_id = str(obligation.get("obligation_id") or "").strip()
    if not obligation_id:
        return
    await cancel_one(obligation_id=obligation_id)


async def _load_user_profile(
    document_repo: "DocumentRepository | None",
    tenant_key: str,
) -> str:
    if document_repo is None or not tenant_key:
        return ""
    try:
        return await document_repo.get(tenant_key, "USER.md") or ""
    except Exception as exc:
        logger.warning("obligation dispatch load USER.md failed: tenant={} err={}", tenant_key, exc)
        return ""
