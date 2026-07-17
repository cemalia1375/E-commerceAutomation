"""Mojing 任务执行器工厂。

每个 make_*_executor() 返回一个闭包，捕获服务依赖，
由 server.py 在启动时调用并注册到 TaskWorker。

任务类型对应关系：
  postprocess                    → PostprocessHook.on_turn_end()（主 Agent）
  skin_diary_postprocess         → PostprocessHook.on_turn_end()（子 Agent）
  obligation_extract             → ColdPathHook.on_turn_end()（主 Agent）
  structured_memory              → ColdPathHook.on_turn_end()（legacy alias）
  skin_profile_sync              → 将 pending 皮肤画像同步进 USER.md
  image_analysis                 → HTTP POST 到图片分析服务
  cabinet_product_record         → 将已调研产品正式加入护肤柜
  skin_diary_generation          → 生成新版肌肤日记并推送前端事件
  deep_research                  → HTTP POST 到深度报告服务
  subagent_dispatch              → SubagentStore.run_turn()

任务状态（queued / running / wait_external / succeeded / failed）由 TaskWorker
通过注入的 TaskStateStore 统一管理；executor 只返回 TaskExecutionResult，
不直接写状态表。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Awaitable, Callable

import httpx
from loguru import logger

from simpleclaw.runtime.task_protocol import TaskEnvelope, TaskExecutionResult
from simpleclaw.subagent.runtime import SubagentRunRequest
from Mojing.runtime.activations import (
    RuntimeActivationService,
    build_image_analysis_completion_activation,
    build_skin_diary_completion_activation,
)
from Mojing.runtime.streams import MojingTaskStream
from Mojing.runtime.task_types import MojingTaskType
from Mojing.utils.skin_diary_time import (
    DIARY_SLOT_MIDDAY,
    resolve_skin_diary_generation_window,
)

if TYPE_CHECKING:
    from simpleclaw.harness.hooks import PostrunHook
    from simpleclaw.llm.base import LLMProvider
    from simpleclaw.runtime.services import RuntimeServices
    from Mojing.storage.document_repo import DocumentRepository
    from Mojing.storage.image_repo import ImageRepository
    from Mojing.storage.skincare_cabinet_repo import SkincareCabinetRepository
    from Mojing.storage.skin_diary_result_repo import SkinDiaryResultRepository
    from Mojing.storage.skin_profile_repo import SkinProfileRepository
    from Mojing.storage.subagent_store import SubagentStore
    from Mojing.storage.tenant_state_repo import TenantStateRepository


# ---------------------------------------------------------------------------
# postprocess stream
# ---------------------------------------------------------------------------

def make_postprocess_executor(
    hook: "PostrunHook",
) -> Callable[[TaskEnvelope], Awaitable[TaskExecutionResult]]:
    """PostprocessHook：用 LLM 重写 USER.md。"""
    from simpleclaw.harness.hooks import TurnContext
    from Mojing.storage.document_repo import document_write_context

    async def execute(task: TaskEnvelope) -> TaskExecutionResult:
        p = task.payload
        ctx = TurnContext(
            tenant_key=p["tenant_key"],
            session_key=p["session_key"],
            user_message=p.get("user_message", ""),
            assistant_reply=p.get("assistant_reply", ""),
            media=list(p.get("media") or []),
            first_token_reply=p.get("first_token_reply", ""),
            main_assistant_reply=p.get("main_assistant_reply", ""),
            postprocess_hints=list(p.get("postprocess_hints") or []),
            tool_calls=list(p.get("tool_calls") or []),
            tool_results=list(p.get("tool_results") or []),
            tool_invocations=list(p.get("tool_invocations") or []),
            runtime_tasks=list(p.get("runtime_tasks") or []),
        )
        with document_write_context(
            change_source=str(task.task_type or "postprocess"),
            source_task_id=task.task_id,
            session_key=ctx.session_key,
            trace_id=task.trace_id,
            change_summary=f"{task.task_type} document update",
            operator_id=task.service_role or "agent",
        ):
            result = await hook.on_turn_end(ctx)
        logger.info(
            "executor.postprocess {}: tenant={} session={} summary={}",
            result.status,
            p["tenant_key"],
            p["session_key"],
            result.summary or "(none)",
        )
        return result

    return execute


def make_obligation_extract_executor(
    hook: "PostrunHook",
    *,
    runtime_task_repo: Any | None = None,
    obligation_repo: Any | None = None,
    runtime: "RuntimeServices | None" = None,
    skin_profile_repo: Any | None = None,
) -> Callable[[TaskEnvelope], Awaitable[TaskExecutionResult]]:
    """ColdPathHook：逐轮提取用户待办 / agent 承诺。

    runtime_task_repo 参数保留给旧调用点兼容；这里不能做 latest-wins，
    因为旧轮可能包含用户明确交代的后续动作。
    """
    from simpleclaw.harness.hooks import TurnContext

    async def execute(task: TaskEnvelope) -> TaskExecutionResult:
        p = task.payload
        ctx = TurnContext(
            tenant_key=p["tenant_key"],
            session_key=p["session_key"],
            user_message=p.get("user_message", ""),
            assistant_reply=p.get("assistant_reply", ""),
            media=list(p.get("media") or []),
            first_token_reply=p.get("first_token_reply", ""),
            main_assistant_reply=p.get("main_assistant_reply", ""),
            postprocess_hints=list(p.get("postprocess_hints") or []),
            tool_calls=list(p.get("tool_calls") or []),
            tool_results=list(p.get("tool_results") or []),
            tool_invocations=list(p.get("tool_invocations") or []),
            runtime_tasks=list(p.get("runtime_tasks") or []),
        )
        result = await hook.on_turn_end(ctx)
        if result.status == "succeeded":
            try:
                await _dispatch_ready_image_obligations(
                    runtime_task_repo=runtime_task_repo,
                    obligation_repo=obligation_repo,
                    runtime=runtime,
                    skin_profile_repo=skin_profile_repo,
                    tenant_key=p["tenant_key"],
                    source_session_key=p["session_key"],
                )
            except Exception as exc:
                logger.warning(
                    "obligation_extract dependency compensation failed: tenant={} session={} err={}",
                    p["tenant_key"],
                    p["session_key"],
                    exc,
                )
        logger.info(
            "executor.obligation_extract {}: tenant={} session={} summary={}",
            result.status,
            p["tenant_key"],
            p["session_key"],
            result.summary or "(none)",
        )
        return result

    return execute


def make_structured_memory_executor(
    hook: "PostrunHook",
    *,
    runtime_task_repo: Any | None = None,
    obligation_repo: Any | None = None,
    runtime: "RuntimeServices | None" = None,
    skin_profile_repo: Any | None = None,
) -> Callable[[TaskEnvelope], Awaitable[TaskExecutionResult]]:
    """Legacy alias for queued structured_memory tasks."""
    return make_obligation_extract_executor(
        hook,
        runtime_task_repo=runtime_task_repo,
        obligation_repo=obligation_repo,
        runtime=runtime,
        skin_profile_repo=skin_profile_repo,
    )


async def _dispatch_ready_image_obligations(
    *,
    runtime_task_repo: Any | None,
    obligation_repo: Any | None,
    runtime: "RuntimeServices | None",
    skin_profile_repo: Any | None,
    tenant_key: str,
    source_session_key: str,
) -> list[dict[str, Any]]:
    """Dispatch newly extracted obligations when the dependency is already true."""
    tenant_key = str(tenant_key or "").strip()
    if (
        not tenant_key
        or runtime_task_repo is None
        or obligation_repo is None
        or runtime is None
    ):
        return []

    latest = await runtime_task_repo.find_latest_task_for(
        tenant_key=tenant_key,
        task_type=MojingTaskType.IMAGE_ANALYSIS,
    )
    if str((latest or {}).get("status") or "").strip().lower() != "succeeded":
        return []

    profile_id = None
    if str((latest or {}).get("business_ref_type") or "") == "tenant_skin_profile":
        profile_id = (latest or {}).get("business_ref_id")
    if not profile_id and skin_profile_repo is not None:
        profile = await skin_profile_repo.get_latest(tenant_key)
        profile_id = (profile or {}).get("profile_id")

    from Mojing.runtime.obligations import (
        DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
        dispatch_obligations_for_dependency,
    )

    return await dispatch_obligations_for_dependency(
        obligation_repo=obligation_repo,
        runtime=runtime,
        tenant_key=tenant_key,
        dependency_type=DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
        source_session_key=str((latest or {}).get("session_key") or source_session_key or ""),
        profile_id=profile_id,
        source_task_id=str((latest or {}).get("task_id") or ""),
    )


def make_skin_profile_sync_executor(
    *,
    skin_repo: "SkinProfileRepository",
    document_repo: "DocumentRepository",
    image_repo: "ImageRepository | None" = None,
    tenant_state_repo: "TenantStateRepository | None" = None,
    action_usage_repo: Any | None = None,
    skin_diary_result_repo: "SkinDiaryResultRepository | None" = None,
    runtime_task_repo: Any | None = None,
    runtime: "RuntimeServices | None" = None,
    activation_service: RuntimeActivationService | None = None,
    obligation_repo: Any | None = None,
) -> Callable[[TaskEnvelope], Awaitable[TaskExecutionResult]]:
    """把 pending 皮肤画像同步进 USER.md。"""
    from Mojing.agent.skin_profile_sync import SkinProfileSyncer, SyncOutcome

    _trigger_outcomes = {
        SyncOutcome.FIRST_SEED,
        SyncOutcome.SELF_UPDATE,
        SyncOutcome.OVERWRITE,
    }

    async def execute(task: TaskEnvelope) -> TaskExecutionResult:
        tenant_key = task.payload.get("tenant_key") or task.tenant_key or ""
        if not tenant_key:
            return TaskExecutionResult.failed(
                "missing tenant_key",
                summary="skin_profile_sync missing tenant_key",
            )

        syncer = SkinProfileSyncer(
            skin_repo=skin_repo,
            document_repo=document_repo,
            image_repo=image_repo,
            tenant_state_repo=tenant_state_repo,
        )
        result = await syncer.sync(tenant_key)
        details = {
            "outcome": result.outcome.value,
            "profile_id": result.profile_id,
        }
        if result.outcome == SyncOutcome.NO_PENDING:
            await _finalize_source_image_analysis(
                runtime_task_repo=runtime_task_repo,
                activation_service=activation_service,
                task=task,
                success=True,
                summary="image analysis profile sync already settled",
                profile_id=result.profile_id,
                outcome=result.outcome.value,
                obligation_repo=obligation_repo,
                runtime=runtime,
                document_repo=document_repo,
            )
            return TaskExecutionResult.noop(
                summary="skin_profile_sync no pending profile",
                details=details,
            )
        if result.outcome == SyncOutcome.NO_CHANGE:
            await _finalize_source_image_analysis(
                runtime_task_repo=runtime_task_repo,
                activation_service=activation_service,
                task=task,
                success=True,
                summary="image analysis profile sync no change",
                profile_id=result.profile_id,
                outcome=result.outcome.value,
                obligation_repo=obligation_repo,
                runtime=runtime,
                document_repo=document_repo,
            )
            return TaskExecutionResult.noop(
                summary="skin_profile_sync no change",
                details=details,
            )
        if result.outcome == SyncOutcome.FAILED:
            await _finalize_source_image_analysis(
                runtime_task_repo=runtime_task_repo,
                activation_service=activation_service,
                task=task,
                success=False,
                error=result.detail or "skin_profile_sync failed",
                profile_id=result.profile_id,
                outcome=result.outcome.value,
            )
            return TaskExecutionResult.failed(
                result.detail or "skin_profile_sync failed",
                summary=result.detail or "skin_profile_sync failed",
                details=details,
            )
        if result.outcome in _trigger_outcomes:
            dispatched = await _maybe_enqueue_skin_diary_generation(
                tenant_key=tenant_key,
                profile_id=result.profile_id,
                source_task_id=str(task.payload.get("source_task_id") or "").strip(),
                tenant_state_repo=tenant_state_repo,
                action_usage_repo=action_usage_repo,
                skin_diary_result_repo=skin_diary_result_repo,
                runtime=runtime,
            )
            details["skin_diary_dispatch"] = dispatched
        await _finalize_source_image_analysis(
            runtime_task_repo=runtime_task_repo,
            activation_service=activation_service,
            task=task,
            success=True,
            summary=f"image analysis profile synced outcome={result.outcome.value}",
            profile_id=result.profile_id,
            outcome=result.outcome.value,
            obligation_repo=obligation_repo,
            runtime=runtime,
            document_repo=document_repo,
        )
        return TaskExecutionResult.succeeded(
            summary=f"skin_profile_sync outcome={result.outcome.value}",
            details=details,
        )

    return execute


async def _finalize_source_image_analysis(
    *,
    runtime_task_repo: Any | None,
    activation_service: RuntimeActivationService | None = None,
    task: TaskEnvelope,
    success: bool,
    summary: str = "",
    error: str = "",
    profile_id: int | None = None,
    outcome: str = "",
    obligation_repo: Any | None = None,
    runtime: "RuntimeServices | None" = None,
    document_repo: "DocumentRepository | None" = None,
) -> None:
    if runtime_task_repo is None:
        return
    source_task_id = str(task.payload.get("source_task_id") or "").strip()
    if not source_task_id:
        return
    if success:
        await runtime_task_repo.mark_succeeded(
            source_task_id,
            summary=summary or None,
            business_ref_type="tenant_skin_profile" if profile_id else None,
            business_ref_id=str(profile_id) if profile_id else None,
            output_json={
                "profile_id": profile_id,
                "sync_outcome": outcome,
            },
        )
        await _notify_image_analysis_succeeded(
            activation_service=activation_service,
            task=task,
            source_task_id=source_task_id,
            profile_id=profile_id,
            obligation_repo=obligation_repo,
            runtime=runtime,
            document_repo=document_repo,
        )
        return
    await runtime_task_repo.mark_task_failed(
        source_task_id,
        error=error or "skin_profile_sync failed",
    )


async def _notify_image_analysis_succeeded(
    *,
    activation_service: RuntimeActivationService | None,
    task: TaskEnvelope,
    source_task_id: str,
    profile_id: int | None = None,
    obligation_repo: Any | None = None,
    runtime: "RuntimeServices | None" = None,
    document_repo: "DocumentRepository | None" = None,
) -> None:
    tenant_key = str(task.payload.get("tenant_key") or task.tenant_key or "").strip()
    if tenant_key:
        from Mojing.runtime.obligations import (
            DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
            dispatch_obligations_for_dependency,
        )

        try:
            await dispatch_obligations_for_dependency(
                obligation_repo=obligation_repo,
                runtime=runtime,
                tenant_key=tenant_key,
                dependency_type=DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
                source_session_key=str(task.payload.get("session_key") or task.session_key or ""),
                profile_id=profile_id,
                source_task_id=source_task_id,
                document_repo=document_repo,
            )
        except Exception as exc:
            logger.warning(
                "skin_profile_sync obligation dispatch failed: tenant={} source_task_id={} err={}",
                tenant_key,
                source_task_id,
                exc,
            )
    if activation_service is None:
        return
    if not tenant_key:
        return
    request = build_image_analysis_completion_activation(
        tenant_key=tenant_key,
        source_session_key=str(task.payload.get("session_key") or task.session_key or ""),
        task_id=source_task_id,
        profile_id=str(profile_id or ""),
    )
    if request is None:
        return
    try:
        await activation_service.enqueue(request)
    except Exception as exc:
        logger.warning(
            "image_analysis completion activation enqueue failed: tenant={} task_id={} err={}",
            tenant_key,
            source_task_id,
            exc,
        )


async def _maybe_enqueue_skin_diary_generation(
    *,
    tenant_key: str,
    profile_id: int | None,
    tenant_state_repo: "TenantStateRepository | None",
    action_usage_repo: Any | None = None,
    skin_diary_result_repo: "SkinDiaryResultRepository | None",
    runtime: "RuntimeServices | None",
    source_task_id: str = "",
    now: datetime | None = None,
) -> str:
    """Auto-generate the first skin diary after profile sync completes."""
    if (
        tenant_state_repo is None
        or action_usage_repo is None
        or skin_diary_result_repo is None
        or runtime is None
    ):
        return "skipped:dependency_missing"

    try:
        counts = await action_usage_repo.get_counts(tenant_key, "skin_diary.handoff")
    except Exception:
        return "skipped:action_usage_unavailable"
    if int(counts.get("submitted_count") or 0) > 0:
        return "skipped:not_first_skin_diary"

    journey = await tenant_state_repo.get_journey(tenant_key)
    stage = str(journey.get("stage") or "novice").strip()
    if stage not in {"novice", "explore", "mature"}:
        return "skipped:not_supported_stage"

    window = resolve_skin_diary_generation_window(now)
    if not window.should_consider or window.business_date is None or window.diary_slot is None:
        return "skipped:outside_auto_window"

    if window.diary_slot == DIARY_SLOT_MIDDAY:
        if await skin_diary_result_repo.has_result_for_business_date(
            tenant_key,
            window.business_date,
        ):
            return "skipped:already_has_business_date_result"
    elif await skin_diary_result_repo.has_result_for_business_date_slot(
        tenant_key,
        window.business_date,
        window.diary_slot,
    ):
        return "skipped:already_has_business_date_slot_result"

    session_key = f"skin_diary:{tenant_key}"
    business_date_text = window.business_date.isoformat()
    triggered_at_beijing = window.local_time.strftime("%Y-%m-%d %H:%M:%S")
    task = TaskEnvelope(
        task_type=MojingTaskType.SKIN_DIARY_GENERATION,
        payload={
            "session_key": session_key,
            "tenant_key": tenant_key,
            "source": "skin_profile_sync",
            "action_key": "skin_diary.handoff",
            "profile_id": profile_id,
            "source_task_id": source_task_id,
            "diary_date": business_date_text,
            "diary_slot": window.diary_slot,
            "generation_reason": window.generation_reason,
            "triggered_at_beijing": triggered_at_beijing,
            "query": "[系统通知] 用户刚完成了一次新的肌肤检测，皮肤画像已更新。",
            "generation_input": {
                "diary_date": business_date_text,
                "diary_slot": window.diary_slot,
                "regeneration_reason": window.generation_reason,
                "notes": (
                    f"首次自动生成，触发时间={triggered_at_beijing}"
                    + (f"，来源图片分析任务={source_task_id}" if source_task_id else "")
                ),
            },
        },
        stream=MojingTaskStream.SKIN_DIARY,
        tenant_key=tenant_key,
        session_key=session_key,
        scope_key=f"{MojingTaskType.SKIN_DIARY_GENERATION}:{tenant_key}",
        service_role="mojing:skin-profile-sync-followup",
    )
    queue_id = await runtime.submit_task(task)
    logger.info(
        "skin_profile_sync dispatched skin diary generation: tenant={} profile_id={} date={} slot={} queue_id={}",
        tenant_key, profile_id, business_date_text, window.diary_slot, queue_id,
    )
    return f"queued:{business_date_text}:{window.diary_slot}"


# ---------------------------------------------------------------------------
# domain task streams
# ---------------------------------------------------------------------------

def make_skin_diary_generation_executor(
    *,
    llm: "LLMProvider",
    document_repo: "DocumentRepository",
    skin_profile_repo: "SkinProfileRepository",
    skin_diary_result_repo: "SkinDiaryResultRepository",
    skincare_cabinet_repo: "SkincareCabinetRepository | None" = None,
    weather_service: Any | None = None,
    runtime_task_repo: Any | None = None,
    publish_fn: Callable[[str, str, dict[str, Any]], Awaitable[int]] | None = None,
    activation_service: RuntimeActivationService | None = None,
    tenant_state_repo: "TenantStateRepository | None" = None,
    sessions: Any | None = None,
    crop_endpoint_url: str = "",
    crop_timeout_s: int = 20,
) -> Callable[[TaskEnvelope], Awaitable[TaskExecutionResult]]:
    """生成新版肌肤日记并推送前端卡片事件。"""

    async def execute(task: TaskEnvelope) -> TaskExecutionResult:
        from Mojing.tools.generate_skin_diary import GenerateSkinDiaryTool

        payload = task.payload or {}
        tenant_key = str(payload.get("tenant_key") or task.tenant_key or "").strip()
        session_key = str(payload.get("session_key") or task.session_key or "").strip()
        if not tenant_key:
            return TaskExecutionResult.failed(
                "missing tenant_key",
                summary="skin diary generation missing tenant_key",
            )
        if not session_key:
            session_key = f"skin_diary:{tenant_key}"

        async def publish_progress(stage_code: str, progress_percent: int, current_title: str) -> None:
            if runtime_task_repo is None:
                return
            try:
                await runtime_task_repo.mark_progress(
                    task,
                    stage_code=stage_code,
                    progress_percent=progress_percent,
                    current_title=current_title,
                    summary=f"skin diary generation {current_title}",
                    stage_name=current_title,
                )
            except Exception as exc:
                logger.warning(
                    "skin_diary_generation progress write failed: tenant={} task_id={} stage={} err={}",
                    tenant_key,
                    task.task_id,
                    stage_code,
                    exc,
                )

        await publish_progress("task_created", 5, "任务创建")
        tool = GenerateSkinDiaryTool(
            llm=llm,
            document_repo=document_repo,
            skin_profile_repo=skin_profile_repo,
            result_repo=skin_diary_result_repo,
            cabinet_repo=skincare_cabinet_repo,
            weather_service=weather_service,
            crop_endpoint_url=crop_endpoint_url,
            crop_timeout_s=crop_timeout_s,
        )
        result = await tool.generate(
            tenant_key=tenant_key,
            session_key=session_key,
            query=str(payload.get("query") or ""),
            generation_input=dict(payload.get("generation_input") or {}),
            progress_callback=publish_progress,
        )
        if not result.get("ok"):
            status = str(result.get("status") or "error")
            summary = f"skin diary generation {status}"
            if status == "missing_skin_profile":
                return TaskExecutionResult.noop(
                    summary=summary,
                    details=result,
                )
            return TaskExecutionResult.failed(
                str(result.get("error") or summary),
                summary=summary,
                details=result,
            )

        event_error = ""
        delivered = 0
        if publish_fn is not None:
            event = {
                "type": "skin_diary_generated",
                "source": "skin_diary_generation",
                "subagent": "skin_diary",
                "delivery": "defer_until_stream_idle",
                "data": {
                    "task_id": task.task_id,
                    "result_id": result.get("result_id"),
                    "state": result.get("state"),
                    "summary": result.get("summary"),
                    "card": result.get("card"),
                },
            }
            try:
                delivered = await publish_fn(tenant_key, session_key, event)
            except Exception as exc:
                event_error = str(exc) or exc.__class__.__name__
                logger.warning(
                    "skin_diary_generation event publish failed: tenant={} session={} err={}",
                    tenant_key, session_key, event_error,
                )

        logger.info(
            "executor.skin_diary_generation ok: tenant={} session={} result_id={} delivered={}",
            tenant_key, session_key, result.get("result_id"), delivered,
        )
        details: dict[str, Any] = {
            "result_id": result.get("result_id"),
            "business_ref_type": "skin_diary_result",
            "business_ref_id": str(result.get("result_id") or ""),
            "event_delivered": delivered,
            "stageCode": "completed",
            "stageName": "生成完成",
            "progress": 100,
            "progressPercent": 100,
            "currentTitle": "生成完成",
        }
        if event_error:
            details["event_error"] = event_error
        if tenant_state_repo is not None:
            try:
                from Mojing.journey.rules import record_journey_event

                stage_before, stage_after = await record_journey_event(
                    tenant_state_repo,
                    tenant_key,
                    "skin_diary_generated",
                )
                details["journey_stage_before"] = stage_before
                details["journey_stage_after"] = stage_after
                details["journey_promoted"] = stage_after != stage_before
                if stage_after != stage_before and sessions is not None:
                    swapped = await sessions.swap_tenant_overlay(tenant_key, stage_after)
                    details["journey_overlay_swapped"] = swapped
                logger.info(
                    "skin_diary_generation journey event: tenant={} {} -> {} promoted={}",
                    tenant_key, stage_before, stage_after, stage_after != stage_before,
                )
            except Exception as exc:
                details["journey_promotion_error"] = str(exc) or exc.__class__.__name__
                logger.warning(
                    "skin_diary_generation journey promotion failed: tenant={} session={} err={}",
                    tenant_key, session_key, exc,
                )
        if activation_service is not None:
            try:
                activation_ingress_id = await activation_service.enqueue(
                    build_skin_diary_completion_activation(
                        tenant_key=tenant_key,
                        source_session_key=session_key,
                        task_id=task.task_id,
                        result_id=str(result.get("result_id") or ""),
                    )
                )
                if activation_ingress_id:
                    details["activation_ingress_id"] = activation_ingress_id
            except Exception as exc:
                details["activation_error"] = str(exc) or exc.__class__.__name__
                logger.warning(
                    "skin_diary_generation activation enqueue failed: tenant={} session={} err={}",
                    tenant_key, session_key, exc,
                )
        return TaskExecutionResult.succeeded(
            summary=f"skin diary generated result_id={result.get('result_id')}",
            details=details,
        )

    return execute


def make_deep_research_executor(
    endpoint_url: str,
    timeout_s: int = 10,
) -> Callable[[TaskEnvelope], Awaitable[TaskExecutionResult]]:
    """向深度报告服务发 HTTP 请求。

    任务状态流转（queued / running / wait_external / failed）统一由 TaskWorker
    通过注入的 task_state_store 管理；executor 只返回 TaskExecutionResult。
    """

    async def execute(task: TaskEnvelope) -> TaskExecutionResult:
        payload = task.payload
        tenant_key = payload.get("user_id", task.tenant_key or "")

        try:
            async with httpx.AsyncClient(timeout=float(timeout_s)) as client:
                response = await client.post(endpoint_url, json=payload)
        except httpx.TimeoutException:
            # 超时视为已接收（深度报告服务本身是异步的，业务上不当失败）
            logger.info(
                "executor.deep_research timeout_ok (async service): tenant={}",
                tenant_key,
            )
            return TaskExecutionResult.wait_external(
                summary="deep research timed out but upstream accepted asynchronously",
            )
        except Exception as exc:
            return TaskExecutionResult.failed(
                str(exc),
                summary=f"deep research request failed: {exc}",
            )

        if 200 <= response.status_code < 300:
            logger.info(
                "executor.deep_research ok: tenant={} status={}",
                tenant_key, response.status_code,
            )
            return TaskExecutionResult.wait_external(
                summary=f"deep research accepted: HTTP {response.status_code}",
                details={"http_status": response.status_code},
            )
        body_preview = _response_body_preview(response)
        error = f"HTTP {response.status_code}"
        if body_preview:
            error = f"{error}: {body_preview}"
        logger.warning(
            "executor.deep_research http_error: tenant={} status={} body={}",
            tenant_key, response.status_code, body_preview,
        )
        return TaskExecutionResult.failed(
            error,
            summary=f"deep research upstream error: {error}",
            details={"http_status": response.status_code, "response_body": body_preview},
        )

    return execute


def make_cabinet_product_research_executor(
    *,
    endpoint_url: str,
    cabinet_repo,
    timeout_s: float = 10.0,
) -> Callable[[TaskEnvelope], Awaitable[TaskExecutionResult]]:
    """向护肤柜产品导入接口发 HTTP 请求，并等待业务表落库后由 monitor 判成功。"""

    async def execute(task: TaskEnvelope) -> TaskExecutionResult:
        payload = dict(task.payload or {})
        user_id = str(payload.get("userId") or "").strip()
        if not user_id:
            return TaskExecutionResult.failed(
                "missing userId",
                summary="cabinet product research missing userId",
            )
        try:
            async with httpx.AsyncClient(timeout=float(timeout_s)) as client:
                response = await client.post(endpoint_url, json=payload)
        except Exception as exc:
            return TaskExecutionResult.wait_external(
                summary="cabinet product research request dispatched; awaiting business visibility",
                details={
                    "request_error": str(exc),
                    "brand": str(payload.get("brand") or "").strip(),
                    "product_name": str(payload.get("productName") or "").strip(),
                    "in_cabinet": 0,
                },
            )

        body_preview = _response_body_preview(response)
        if not (200 <= response.status_code < 300):
            error = f"HTTP {response.status_code}"
            if body_preview:
                error = f"{error}: {body_preview}"
            return TaskExecutionResult.wait_external(
                summary="cabinet product research upstream accepted asynchronously; awaiting business visibility",
                details={
                    "http_status": response.status_code,
                    "response_body": body_preview,
                    "upstream_error": error,
                    "brand": str(payload.get("brand") or "").strip(),
                    "product_name": str(payload.get("productName") or "").strip(),
                    "in_cabinet": 0,
                },
            )

        data = _safe_json_dict(response)
        product_data = _cabinet_product_payload(data)
        brand = str(product_data.get("brand") or payload.get("brand") or "").strip()
        product_name = str(product_data.get("productName") or product_data.get("product_name") or payload.get("productName") or "").strip()
        if not brand or not product_name:
            return TaskExecutionResult.failed(
                "missing brand or product name",
                summary="cabinet product research response missing product identity",
                details={"http_status": response.status_code, "response_body": body_preview},
            )
        usage_status = str(payload.get("usage_status") or "").strip() or None
        try:
            product_id = await cabinet_repo.save_researched_product(
                user_id=user_id,
                brand=brand,
                product_name=product_name,
                usage_status=usage_status,
                image_url=str(payload.get("imageUrl") or "").strip(),
                category=str(product_data.get("category") or "").strip(),
                core_efficacy=product_data.get("core_efficacy") or product_data.get("coreEfficacy"),
                core_ingredients=product_data.get("core_ingredients") or product_data.get("coreIngredients"),
                risk_ingredients=product_data.get("risk_ingredients") or product_data.get("riskIngredients"),
                commercial_image=str(product_data.get("commercial_image") or product_data.get("commercialImage") or "").strip(),
                expiration_date=_coerce_date_text(product_data.get("expiration_date") or product_data.get("expirationDate")),
                storage_conditions=str(product_data.get("storage_conditions") or product_data.get("storageConditions") or "").strip(),
                specifications=str(product_data.get("specifications") or "").strip(),
            )
        except Exception as exc:
            return TaskExecutionResult.failed(
                str(exc),
                summary=f"cabinet product persistence failed: {exc}",
                details={"http_status": response.status_code, "response_body": body_preview},
            )

        return TaskExecutionResult.wait_external(
            summary=f"cabinet product record visible product_id={product_id}",
            details={
                "http_status": response.status_code,
                "product_id": product_id,
                "brand": brand,
                "product_name": product_name,
                "in_cabinet": 0,
                "business_ref_type": "skincare_cabinet_product",
                "business_ref_id": str(product_id),
            },
        )

    return execute


def make_cabinet_product_record_executor(
    *,
    cabinet_repo,
) -> Callable[[TaskEnvelope], Awaitable[TaskExecutionResult]]:
    """把已调研产品正式加入护肤柜，用于 cold path obligation 自动履约。"""

    async def execute(task: TaskEnvelope) -> TaskExecutionResult:
        payload = dict(task.payload or {})
        tenant_key = str(task.tenant_key or payload.get("tenant_key") or "").strip()
        product_id_raw = payload.get("product_id")
        try:
            product_id = int(product_id_raw)
        except Exception:
            return TaskExecutionResult.failed(
                "missing product_id",
                summary="cabinet product record missing product_id",
            )
        if product_id <= 0 or not tenant_key:
            return TaskExecutionResult.failed(
                "invalid product_id or tenant_key",
                summary="cabinet product record invalid input",
            )

        user_id = tenant_key
        existing = await cabinet_repo.get(product_id=product_id, user_id=user_id)
        if existing is None:
            return TaskExecutionResult.failed(
                "skincare cabinet product not found",
                summary="cabinet product record product not found",
                details={"product_id": product_id},
            )
        if int(existing.get("in_cabinet") or 0) == 1:
            return TaskExecutionResult.noop(
                summary="cabinet product already in cabinet",
                details={"product_id": product_id, "in_cabinet": 1},
            )

        usage_status = str(payload.get("usage_status") or "").strip() or None
        updated = await cabinet_repo.mark_in_cabinet(
            product_id=product_id,
            user_id=user_id,
            usage_status=usage_status,
        )
        if updated is None:
            return TaskExecutionResult.failed(
                "failed to update skincare cabinet product",
                summary="cabinet product record update failed",
                details={"product_id": product_id},
            )
        return TaskExecutionResult.succeeded(
            summary="cabinet product recorded in cabinet",
            details={
                "product_id": product_id,
                "in_cabinet": 1,
                "brand": updated.get("brand"),
                "product_name": updated.get("product_name"),
            },
        )

    return execute


def _response_body_preview(response: httpx.Response, *, limit: int = 800) -> str:
    text = ""
    try:
        text = response.text
    except Exception:
        return ""
    text = " ".join(str(text or "").split())
    if len(text) > limit:
        return text[:limit] + "...[truncated]"
    return text


def _safe_json_dict(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _cabinet_product_payload(data: dict[str, Any]) -> dict[str, Any]:
    nested = data.get("data")
    if isinstance(nested, dict):
        return nested
    result = data.get("result")
    if isinstance(result, dict):
        return result
    return data


def _coerce_date_text(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text[:10]


def make_image_analysis_executor(
    endpoint_url: str,
    image_repo: "ImageRepository | None" = None,
    timeout_s: float = 30.0,
) -> Callable[[TaskEnvelope], Awaitable[TaskExecutionResult]]:
    """向图片分析 webhook 发 HTTP 请求。"""

    async def execute(task: TaskEnvelope) -> TaskExecutionResult:
        payload = task.payload
        tenant_key = payload.get("tenant_key", task.tenant_key or "")
        job_id = str(payload.get("job_id") or "").strip()

        if image_repo is not None and job_id:
            try:
                await image_repo.mark_running(job_id)
            except Exception as exc:
                logger.warning("executor.image_analysis mark_running failed: {}", exc)

        try:
            async with httpx.AsyncClient(timeout=float(timeout_s)) as client:
                response = await client.post(endpoint_url, json=payload)
        except Exception as exc:
            if image_repo is not None and job_id:
                try:
                    await image_repo.mark_failed(job_id, error=str(exc))
                except Exception as mark_exc:
                    logger.warning("executor.image_analysis mark_failed failed: {}", mark_exc)
            return TaskExecutionResult.failed(
                str(exc),
                summary=f"image analysis request failed: {exc}",
            )

        if 200 <= response.status_code < 300:
            response_payload = _response_payload(response)
            external_job_id = _external_job_id(response_payload)
            if image_repo is not None and job_id:
                try:
                    await image_repo.mark_wait_external(
                        job_id,
                        external_job_id=external_job_id,
                        response=response_payload,
                    )
                except Exception as exc:
                    logger.warning("executor.image_analysis mark_wait_external failed: {}", exc)
            logger.info(
                "executor.image_analysis ok: tenant={} status={}",
                tenant_key, response.status_code,
            )
            return TaskExecutionResult.wait_external(
                summary=f"image analysis accepted: HTTP {response.status_code}",
                details={"http_status": response.status_code},
            )

        error = f"HTTP {response.status_code}"
        logger.warning(
            "executor.image_analysis http_error: tenant={} status={}",
            tenant_key, response.status_code,
        )
        if image_repo is not None and job_id:
            try:
                await image_repo.mark_failed(job_id, error=error)
            except Exception as exc:
                logger.warning("executor.image_analysis mark_failed failed: {}", exc)
        return TaskExecutionResult.failed(
            error,
            summary=f"image analysis upstream error: {error}",
            details={"http_status": response.status_code},
        )

    return execute


def _response_payload(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        text = response.text
        return {"text": text[:2000]} if text else {}


def _external_job_id(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("job_id", "task_id", "analysis_id", "id"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return None


def make_subagent_dispatch_executor(
    subagent_store: "SubagentStore",
) -> Callable[[TaskEnvelope], Awaitable[TaskExecutionResult]]:
    """在后台运行一轮子 Agent 对话（如肌肤日记）。"""

    async def execute(task: TaskEnvelope) -> TaskExecutionResult:
        p = task.payload
        session_key = p["session_key"]
        tenant_key  = p["tenant_key"]
        message     = p.get("user_query") or p["message"]
        handoff_contract = _enrich_handoff_contract(p, parent_handoff_task_id=task.task_id)
        subagent = subagent_store.find_subagent(session_key)
        subagent_name = getattr(subagent, "name", "") or _subagent_name_from_session(session_key)
        subagent_run_request = SubagentRunRequest(
            tenant_key=tenant_key,
            session_key=session_key,
            subagent_name=subagent_name,
            objective=_compact_subagent_objective(message),
            run_mode="handoff",
            owner_type="runtime_task",
            owner_id=task.task_id,
            trace_id=task.trace_id,
            input_refs={
                "runtime_task_id": task.task_id,
                "origin_session_key": str(p.get("origin_session_key") or ""),
                "source": str(p.get("source") or ""),
                "source_task_id": str(p.get("source_task_id") or ""),
                "source_image_id": str(p.get("source_image_id") or ""),
                "source_image_ref": str(p.get("source_image_ref") or ""),
            },
            payload={
                "message": message,
                "handoff_contract": handoff_contract,
                "task_type": task.task_type,
                "service_role": task.service_role or "",
            },
        )

        await subagent_store.run_turn(
            session_key=session_key,
            tenant_key=tenant_key,
            message=message,
            origin_session_key=p.get("origin_session_key"),
            handoff_contract=handoff_contract,
            subagent_run_request=subagent_run_request,
            runtime_task_id=task.task_id,
        )
        logger.info(
            "executor.subagent_dispatch ok: session={} tenant={}",
            session_key, tenant_key,
        )
        return TaskExecutionResult.succeeded(
            summary="subagent dispatch completed",
        )

    return execute


def _enrich_handoff_contract(
    payload: dict,
    *,
    parent_handoff_task_id: str,
) -> dict[str, object]:
    contract = dict(payload.get("handoff_contract") or {})
    if contract:
        contract["parent_handoff_task_id"] = str(parent_handoff_task_id or "").strip()
        original = str(payload.get("original_user_query") or payload.get("message") or "").strip()
        if original:
            contract["original_user_query"] = original
        for key in ("origin_session_key", "source_task_id", "source_image_id", "source_image_ref"):
            value = str(payload.get(key) or "").strip()
            if value:
                contract[key] = value
    return contract


def _subagent_name_from_session(session_key: str) -> str:
    text = str(session_key or "").strip()
    if ":" in text:
        return text.split(":", 1)[0] or "subagent"
    return text or "subagent"


def _compact_subagent_objective(message: object, *, limit: int = 240) -> str:
    text = " ".join(str(message or "").split()).strip()
    if not text:
        return "subagent dispatch"
    return text if len(text) <= limit else text[:limit].rstrip() + "..."
