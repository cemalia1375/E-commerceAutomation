"""ColdPathHook — extract durable obligations after a completed turn.

The cold path no longer produces topic reminders. It only extracts explicit
user todos and agent commitments that may need to be dispatched after a future
dependency becomes true.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from loguru import logger

from simpleclaw.harness.hooks import PostrunHook, TurnContext
from simpleclaw.llm.base import LLMProvider
from simpleclaw.llm.chunks import TextChunk
from simpleclaw.runtime.task_protocol import TaskExecutionResult

from Mojing.runtime.obligations import (
    ACTION_CONFIRM_SKINCARE_CABINET_RECORD,
    ACTION_GENERATE_DEEP_REPORT,
    ACTION_GENERATE_SKIN_DIARY,
    DEPENDENCY_CABINET_PRODUCT_RESEARCH_SUCCEEDED,
    DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
)
from Mojing.storage.obligation_repo import ObligationRepository

_COLD_PATH_PROMPT_PATH = Path(__file__).parent.parent / "workspace" / "cold_path.md"
_SPLIT_MARKER = "===SPLIT_SYSTEM_USER==="


def _load_split_prompt(path: Path) -> tuple[str, str]:
    if not path.exists():
        return "", ""
    text = path.read_text(encoding="utf-8").strip()
    if _SPLIT_MARKER not in text:
        return text, ""
    system_part, user_part = text.split(_SPLIT_MARKER, 1)
    return system_part.strip(), user_part.strip()


class ColdPathHook(PostrunHook):
    """Extract obligations from the latest completed turn."""

    def __init__(
        self,
        llm: LLMProvider,
        obligation_repo: ObligationRepository,
        *,
        cache_repo: Any | None = None,
        cache_window_turns: int | None = None,
        runtime_task_repo: Any | None = None,
    ) -> None:
        del cache_repo, cache_window_turns
        self._llm = llm
        self._obligation_repo = obligation_repo
        self._runtime_task_repo = runtime_task_repo
        self._system_prompt, self._user_template = _load_split_prompt(_COLD_PATH_PROMPT_PATH)

    async def on_turn_end(self, ctx: TurnContext) -> TaskExecutionResult:
        try:
            return await self._run(ctx)
        except Exception as exc:
            logger.warning(
                "cold path obligation extraction failed tenant={} session={}：{}",
                ctx.tenant_key,
                ctx.session_key,
                exc,
            )
            return TaskExecutionResult.failed(
                f"cold path failed: {exc}",
                summary="cold path obligation extraction failed",
            )

    async def _run(self, ctx: TurnContext) -> TaskExecutionResult:
        if not self._system_prompt or not self._user_template:
            return TaskExecutionResult.noop(summary="cold path prompt missing")

        user_content = _fill_user_template(
            template=self._user_template,
            user_message=ctx.user_message,
            assistant_reply=ctx.assistant_reply,
            first_token_reply=ctx.first_token_reply,
            main_assistant_reply=ctx.main_assistant_reply,
            media=ctx.media,
            tool_calls=ctx.tool_calls,
            tool_results=ctx.tool_results,
            tool_invocations=ctx.tool_invocations,
            runtime_tasks=ctx.runtime_tasks,
        )
        raw = await self._complete_obligation_extract(ctx=ctx, user_content=user_content)
        if not raw:
            return TaskExecutionResult.noop(summary="cold path returned empty output")

        data = _parse_json_safe(raw)
        if data is None:
            raise RuntimeError(f"cold path returned invalid JSON: {raw[:120]}")

        created, cancelled = await self._apply_obligation_result(ctx, data)
        if created or cancelled:
            return TaskExecutionResult.succeeded(
                summary=f"obligations created={created} cancelled={cancelled}",
                details={
                    "obligations_created": created,
                    "obligations_cancelled": cancelled,
                },
            )
        return TaskExecutionResult.noop(summary="no obligations extracted")

    async def _complete_obligation_extract(self, *, ctx: TurnContext, user_content: str) -> str:
        del ctx
        return await _llm_complete_system_user(
            self._llm,
            system=self._system_prompt,
            user=user_content,
            max_tokens=400,
            cache_tenant_key="__cold_path__",
        )

    async def _apply_obligation_result(self, ctx: TurnContext, data: dict[str, Any]) -> tuple[int, int]:
        created = 0
        cancelled = 0

        for item in _normalize_obligation_items(data.get("obligations")):
            action_type = item["action_type"]
            if _action_already_submitted(ctx, action_type):
                cancelled += await self._obligation_repo.cancel_pending(
                    tenant_key=ctx.tenant_key,
                    session_key=ctx.session_key,
                    action_type=action_type,
                )
                continue
            dependency_type = item.get("dependency_type") or ""
            if not await self._can_create_obligation(ctx, action_type, dependency_type):
                continue
            evidence = _build_evidence(ctx, item)
            payload = _build_payload(ctx, item)
            if dependency_type == DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED:
                await self._bind_recent_active_image_dependency(payload, ctx)
            elif dependency_type == DEPENDENCY_CABINET_PRODUCT_RESEARCH_SUCCEEDED:
                _bind_current_cabinet_research_dependency(payload, ctx)
            _copy_dependency_binding_to_evidence(evidence, payload)
            existing = await self._obligation_repo.find_pending_action(
                tenant_key=ctx.tenant_key,
                session_key=ctx.session_key,
                action_type=action_type,
                dependency_type=dependency_type,
            )
            if existing and _same_dependency_scope(existing, payload):
                continue
            dedupe_key = _dedupe_key(
                tenant_key=ctx.tenant_key,
                session_key=ctx.session_key,
                action_type=action_type,
                dependency_type=dependency_type,
                evidence=evidence,
            )
            record = await self._obligation_repo.create_pending(
                tenant_key=ctx.tenant_key,
                session_key=ctx.session_key,
                action_type=action_type,
                dependency_type=dependency_type,
                payload=payload,
                evidence=evidence,
                dedupe_key=dedupe_key,
            )
            if record and str(record.get("status") or "") == "pending":
                created += 1

        for action_type in _normalize_cancel_items(data.get("cancel")):
            cancelled += await self._obligation_repo.cancel_pending(
                tenant_key=ctx.tenant_key,
                session_key=ctx.session_key,
                action_type=action_type,
            )

        return created, cancelled

    async def _can_create_obligation(
        self,
        ctx: TurnContext,
        action_type: str,
        dependency_type: str,
    ) -> bool:
        if (
            action_type != ACTION_GENERATE_SKIN_DIARY
            or dependency_type != DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED
        ):
            return True
        finder = getattr(self._runtime_task_repo, "has_succeeded_task_for", None)
        if not callable(finder):
            logger.info(
                "cold_path skip skin diary obligation: no image success checker tenant={} session={}",
                ctx.tenant_key,
                ctx.session_key,
            )
            return False
        try:
            has_succeeded_image = await finder(
                tenant_key=ctx.tenant_key,
                task_type="image_analysis",
            )
        except Exception as exc:
            logger.warning(
                "cold_path image success check failed tenant={} session={} err={}",
                ctx.tenant_key,
                ctx.session_key,
                exc,
            )
            return False
        if not has_succeeded_image:
            logger.info(
                "cold_path skip first-image skin diary obligation: tenant={} session={}",
                ctx.tenant_key,
                ctx.session_key,
            )
        return bool(has_succeeded_image)

    async def _bind_recent_active_image_dependency(
        self,
        payload: dict[str, Any],
        ctx: TurnContext,
    ) -> None:
        if payload.get("dependency_ref_id"):
            return
        finder = getattr(self._runtime_task_repo, "find_latest_active_task_for", None)
        if not callable(finder):
            if not payload.get("dependency_ref_required"):
                payload["dependency_ref_required"] = True
            return

        try:
            task = await finder(
                tenant_key=ctx.tenant_key,
                session_key=ctx.session_key,
                task_type="image_analysis",
            )
        except Exception as exc:
            logger.warning(
                "cold_path active image dependency lookup failed tenant={} session={} err={}",
                ctx.tenant_key,
                ctx.session_key,
                exc,
            )
            task = None

        task_data = _task_record_to_dict(task)
        task_id = str(task_data.get("task_id") or "").strip()
        if not task_id:
            payload["dependency_ref_required"] = True
            return

        input_json = task_data.get("input_json")
        if not isinstance(input_json, dict):
            input_json = task_data.get("payload") if isinstance(task_data.get("payload"), dict) else {}
        binding = _dependency_binding(
            task_id=task_id,
            business_ref_type=str(task_data.get("business_ref_type") or "").strip(),
            business_ref_id=str(
                task_data.get("business_ref_id")
                or input_json.get("job_id")
                or input_json.get("message_id")
                or ""
            ).strip(),
            source="runtime_task_active_lookup",
        )
        payload.update(binding)
        payload.pop("dependency_ref_required", None)
        payload.pop("dependency_media_count", None)


def _fill_user_template(
    *,
    template: str,
    user_message: str,
    assistant_reply: str,
    first_token_reply: str = "",
    main_assistant_reply: str = "",
    media: list[str] | None = None,
    tool_calls: list[dict] | None = None,
    tool_results: list[dict] | None = None,
    tool_invocations: list[dict] | None = None,
    runtime_tasks: list[dict] | None = None,
    **_: Any,
) -> str:
    media_signal = f"用户在本轮上传了 {len(media)} 张图片" if media else "无"
    tool_facts = _tool_facts_text(
        tool_calls=tool_calls or [],
        tool_results=tool_results or [],
        tool_invocations=tool_invocations or [],
        runtime_tasks=runtime_tasks or [],
    )
    return (
        template
        .replace("<<<MEDIA_SIGNAL>>>", media_signal)
        .replace("<<<USER_MESSAGE>>>", user_message or "")
        .replace("<<<ASSISTANT_REPLY>>>", assistant_reply or "")
        .replace("<<<FIRST_TOKEN_REPLY>>>", first_token_reply or "")
        .replace("<<<MAIN_ASSISTANT_REPLY>>>", main_assistant_reply or assistant_reply or "")
        .replace("<<<TOOL_FACTS>>>", tool_facts)
    )


def _tool_facts_text(
    *,
    tool_calls: list[dict],
    tool_results: list[dict],
    tool_invocations: list[dict],
    runtime_tasks: list[dict],
) -> str:
    facts = {
        "tool_calls": _shorten_list(tool_calls, limit=12),
        "tool_results": _shorten_list(tool_results, limit=12),
        "tool_invocations": _shorten_list(tool_invocations, limit=20),
        "runtime_tasks": _shorten_list(runtime_tasks, limit=20),
    }
    return json.dumps(facts, ensure_ascii=False, default=str)[:6000]


def _shorten_list(items: list[dict], *, limit: int) -> list[dict]:
    return [dict(item) for item in items[:limit] if isinstance(item, dict)]


def _normalize_obligation_items(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    result: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        action_type = str(item.get("action_type") or "").strip()
        dependency_type = str(item.get("dependency_type") or "").strip()
        if action_type not in _SUPPORTED_OBLIGATION_ACTIONS:
            continue
        if dependency_type not in _SUPPORTED_DEPENDENCY_TYPES:
            continue
        result.append({
            "action_type": action_type,
            "dependency_type": dependency_type,
            "evidence": item.get("evidence"),
            "payload": item.get("payload"),
        })
    return result


def _normalize_cancel_items(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    for item in raw:
        action_type = ""
        if isinstance(item, str):
            action_type = item.strip()
        elif isinstance(item, dict):
            action_type = str(item.get("action_type") or "").strip()
        if action_type in _SUPPORTED_OBLIGATION_ACTIONS:
            result.append(action_type)
    return result


def _action_already_submitted(ctx: TurnContext, action_type: str) -> bool:
    """Return True when this turn already submitted the same executable action."""
    if action_type not in _SUPPORTED_OBLIGATION_ACTIONS:
        return False

    if action_type == ACTION_GENERATE_DEEP_REPORT:
        return _deep_report_already_submitted(ctx)

    if action_type == ACTION_CONFIRM_SKINCARE_CABINET_RECORD:
        return _cabinet_record_already_submitted(ctx)

    for invocation in ctx.tool_invocations or []:
        if not isinstance(invocation, dict):
            continue
        tool_name = str(invocation.get("tool_name") or "").strip()
        status = str(invocation.get("status") or "").strip().lower()
        if tool_name not in {"generate_skin_diary", "notify_skin_diary_chat"}:
            continue
        if status in {"submitted", "succeeded", "deduped"}:
            return True

    for task in ctx.runtime_tasks or []:
        if not isinstance(task, dict):
            continue
        task_type = str(task.get("task_type") or "").strip()
        status = str(task.get("status") or "").strip().lower()
        if task_type == "skin_diary_generation" and status in {
            "queued",
            "running",
            "wait_external",
            "succeeded",
        }:
            return True
        if task_type == "subagent_dispatch":
            payload = task.get("input_json")
            action_key = ""
            if isinstance(payload, dict):
                action_key = str(payload.get("action_key") or "").strip()
            if action_key == "skin_diary.handoff" and status in {
                "queued",
                "running",
                "wait_external",
                "succeeded",
            }:
                return True

    return False


def _cabinet_record_already_submitted(ctx: TurnContext) -> bool:
    for invocation in ctx.tool_invocations or []:
        if not isinstance(invocation, dict):
            continue
        tool_name = str(invocation.get("tool_name") or "").strip()
        status = str(invocation.get("status") or "").strip().lower()
        if tool_name == "confirm_skincare_cabinet_record" and status in {"submitted", "succeeded", "deduped"}:
            return True

    for task in ctx.runtime_tasks or []:
        if not isinstance(task, dict):
            continue
        task_type = str(task.get("task_type") or "").strip()
        status = str(task.get("status") or "").strip().lower()
        if task_type == "cabinet_product_record" and status in {
            "queued",
            "running",
            "wait_external",
            "succeeded",
        }:
            return True
    return False


def _deep_report_already_submitted(ctx: TurnContext) -> bool:
    for invocation in ctx.tool_invocations or []:
        if not isinstance(invocation, dict):
            continue
        tool_name = str(invocation.get("tool_name") or "").strip()
        status = str(invocation.get("status") or "").strip().lower()
        if tool_name not in {"deep_report_chat", "deep_research"}:
            continue
        if status in {"submitted", "succeeded", "deduped"}:
            return True

    for task in ctx.runtime_tasks or []:
        if not isinstance(task, dict):
            continue
        task_type = str(task.get("task_type") or "").strip()
        status = str(task.get("status") or "").strip().lower()
        if task_type == "deep_research" and status in {
            "queued",
            "running",
            "wait_external",
            "succeeded",
        }:
            return True
        if task_type == "subagent_dispatch":
            payload = task.get("input_json")
            if not isinstance(payload, dict):
                payload = task.get("payload") if isinstance(task.get("payload"), dict) else {}
            session_key = str(payload.get("session_key") or task.get("session_key") or "").strip()
            source = str(payload.get("source") or "").strip()
            action_key = str(payload.get("action_key") or "").strip()
            is_deep_report = (
                session_key.startswith("deep_report:")
                or source == "deep_report_chat"
                or action_key == "deep_report.handoff"
            )
            if is_deep_report and status in {
                "queued",
                "running",
                "wait_external",
                "succeeded",
            }:
                return True
    return False


def _build_evidence(ctx: TurnContext, item: dict[str, Any]) -> dict[str, Any]:
    raw = item.get("evidence")
    evidence: dict[str, Any]
    if isinstance(raw, dict):
        evidence = dict(raw)
    elif isinstance(raw, list):
        evidence = {"items": [str(v).strip() for v in raw if str(v).strip()]}
    elif raw:
        evidence = {"summary": str(raw).strip()}
    else:
        evidence = {}
    evidence.setdefault("user_message", ctx.user_message)
    evidence.setdefault("assistant_reply", ctx.assistant_reply)
    if ctx.first_token_reply:
        evidence.setdefault("first_token_reply", ctx.first_token_reply)
    if ctx.main_assistant_reply:
        evidence.setdefault("main_assistant_reply", ctx.main_assistant_reply)
    return evidence


def _build_payload(ctx: TurnContext, item: dict[str, Any]) -> dict[str, Any]:
    raw = item.get("payload")
    payload = dict(raw) if isinstance(raw, dict) else {}
    payload.setdefault("source", "cold_path")
    payload.setdefault("origin_session_key", ctx.session_key)
    dependency_type = str(item.get("dependency_type") or "").strip()
    if dependency_type == DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED:
        _apply_current_image_analysis_dependency(payload, ctx)
    elif dependency_type == DEPENDENCY_CABINET_PRODUCT_RESEARCH_SUCCEEDED:
        _apply_current_cabinet_research_dependency(payload, ctx)
    action_type = str(item.get("action_type") or "").strip()
    if action_type == ACTION_GENERATE_DEEP_REPORT:
        payload.setdefault("user_query", _short_text(ctx.user_message, ctx.assistant_reply))
        return payload
    if action_type == ACTION_CONFIRM_SKINCARE_CABINET_RECORD:
        payload.setdefault("usage_status", _infer_usage_status(ctx.user_message, ctx.assistant_reply))
        return payload
    payload.setdefault("generation_input", {})
    generation_input = payload["generation_input"] if isinstance(payload["generation_input"], dict) else {}
    generation_input.setdefault("source", "mixed")
    generation_input.setdefault("regeneration_reason", "user_requested_after_image_analysis")
    if not generation_input.get("evidence"):
        generation_input["evidence"] = _short_text(ctx.user_message, ctx.assistant_reply)
    payload["generation_input"] = generation_input
    return payload


def _apply_current_image_analysis_dependency(payload: dict[str, Any], ctx: TurnContext) -> None:
    binding = _current_image_analysis_dependency(ctx)
    if binding:
        for key, value in binding.items():
            if value:
                payload[key] = value
        return
    if ctx.media:
        payload.setdefault("dependency_ref_required", True)
        payload.setdefault("dependency_media_count", len(ctx.media))


def _current_image_analysis_dependency(ctx: TurnContext) -> dict[str, str]:
    for invocation in reversed(ctx.tool_invocations or []):
        if not isinstance(invocation, dict):
            continue
        if str(invocation.get("tool_name") or "").strip() != "analyze_image":
            continue
        task_id = str(invocation.get("runtime_task_id") or "").strip()
        if not task_id:
            continue
        return _dependency_binding(
            task_id=task_id,
            business_ref_type=str(invocation.get("business_ref_type") or "").strip(),
            business_ref_id=str(invocation.get("business_ref_id") or "").strip(),
            source="tool_invocation",
        )

    for result in reversed(ctx.tool_results or []):
        parsed = _tool_result_payload(result)
        if not parsed:
            continue
        tool_name = str(parsed.get("tool") or result.get("tool_name") or "").strip()
        if tool_name != "analyze_image":
            continue
        task_id = str(parsed.get("task_id") or parsed.get("runtime_task_id") or "").strip()
        if not task_id:
            continue
        return _dependency_binding(
            task_id=task_id,
            business_ref_type=str(parsed.get("business_ref_type") or "").strip(),
            business_ref_id=str(parsed.get("business_ref_id") or parsed.get("job_id") or "").strip(),
            source="tool_result",
        )

    for task in reversed(ctx.runtime_tasks or []):
        if not isinstance(task, dict):
            continue
        if str(task.get("task_type") or "").strip() != "image_analysis":
            continue
        task_id = str(task.get("task_id") or "").strip()
        if not task_id:
            continue
        input_json = task.get("input_json") if isinstance(task.get("input_json"), dict) else {}
        return _dependency_binding(
            task_id=task_id,
            business_ref_type=str(task.get("business_ref_type") or "").strip(),
            business_ref_id=str(
                task.get("business_ref_id")
                or input_json.get("job_id")
                or input_json.get("message_id")
                or ""
            ).strip(),
            source="runtime_task",
        )

    return {}


def _apply_current_cabinet_research_dependency(payload: dict[str, Any], ctx: TurnContext) -> None:
    binding = _current_cabinet_research_dependency(ctx)
    if binding:
        payload.update(binding)
        return
    payload.setdefault("dependency_ref_required", True)


def _bind_current_cabinet_research_dependency(payload: dict[str, Any], ctx: TurnContext) -> None:
    if payload.get("dependency_ref_id"):
        return
    _apply_current_cabinet_research_dependency(payload, ctx)


def _current_cabinet_research_dependency(ctx: TurnContext) -> dict[str, str]:
    for invocation in reversed(ctx.tool_invocations or []):
        if not isinstance(invocation, dict):
            continue
        if str(invocation.get("tool_name") or "").strip() != "research_skincare_product":
            continue
        task_id = str(invocation.get("runtime_task_id") or "").strip()
        if not task_id:
            continue
        return _dependency_binding(
            task_id=task_id,
            business_ref_type=str(invocation.get("business_ref_type") or "").strip(),
            business_ref_id=str(invocation.get("business_ref_id") or "").strip(),
            source="tool_invocation",
        )

    for result in reversed(ctx.tool_results or []):
        parsed = _tool_result_payload(result)
        if not parsed:
            continue
        tool_name = str(parsed.get("tool") or result.get("tool_name") or "").strip()
        if tool_name != "research_skincare_product":
            continue
        task_id = str(parsed.get("task_id") or parsed.get("runtime_task_id") or "").strip()
        if not task_id:
            continue
        return _dependency_binding(
            task_id=task_id,
            business_ref_type=str(parsed.get("business_ref_type") or "").strip(),
            business_ref_id=str(parsed.get("business_ref_id") or "").strip(),
            source="tool_result",
        )

    for task in reversed(ctx.runtime_tasks or []):
        if not isinstance(task, dict):
            continue
        if str(task.get("task_type") or "").strip() != "cabinet_product_research":
            continue
        task_id = str(task.get("task_id") or "").strip()
        if not task_id:
            continue
        return _dependency_binding(
            task_id=task_id,
            business_ref_type=str(task.get("business_ref_type") or "").strip(),
            business_ref_id=str(task.get("business_ref_id") or "").strip(),
            source="runtime_task",
        )

    return {}


def _infer_usage_status(*texts: str) -> str:
    joined = "\n".join(str(text or "") for text in texts)
    if any(token in joined for token in ("用完", "空瓶", "已经没了", "用光")):
        return "finished"
    if any(token in joined for token in ("未拆", "没开封", "未开封", "还没开")):
        return "unopened"
    if any(token in joined for token in ("开封", "在用", "用着", "用过", "已经用了")):
        return "using"
    return ""


def _dependency_binding(
    *,
    task_id: str,
    business_ref_type: str,
    business_ref_id: str,
    source: str,
) -> dict[str, str]:
    binding = {
        "dependency_ref_type": "runtime_task",
        "dependency_ref_id": task_id,
        "dependency_binding_source": source,
    }
    if business_ref_type:
        binding["dependency_business_ref_type"] = business_ref_type
    if business_ref_id:
        binding["dependency_business_ref_id"] = business_ref_id
    return binding


def _tool_result_payload(result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    raw = result.get("result")
    if raw is None:
        raw = result.get("content")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        parsed = _parse_json_safe(raw)
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _task_record_to_dict(task: Any) -> dict[str, Any]:
    if task is None:
        return {}
    if isinstance(task, dict):
        return dict(task)
    if hasattr(task, "to_dict"):
        try:
            data = task.to_dict()
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def _copy_dependency_binding_to_evidence(evidence: dict[str, Any], payload: dict[str, Any]) -> None:
    for key in (
        "dependency_ref_type",
        "dependency_ref_id",
        "dependency_business_ref_type",
        "dependency_business_ref_id",
        "dependency_binding_source",
        "dependency_ref_required",
        "dependency_media_count",
    ):
        value = payload.get(key)
        if value:
            evidence.setdefault(key, value)


def _same_dependency_scope(existing: dict[str, Any], payload: dict[str, Any]) -> bool:
    existing_payload = existing.get("payload") if isinstance(existing, dict) else {}
    existing_ref = _payload_dependency_ref(existing_payload)
    payload_ref = _payload_dependency_ref(payload)
    if existing_ref or payload_ref:
        return existing_ref == payload_ref

    existing_required = _payload_dependency_ref_required(existing_payload)
    payload_required = _payload_dependency_ref_required(payload)
    if existing_required or payload_required:
        return existing_required == payload_required

    return True


def _payload_dependency_ref(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("dependency_ref_id") or "").strip()


def _payload_dependency_ref_required(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    return bool(payload.get("dependency_ref_required"))


def _short_text(*parts: str) -> str:
    text = "；".join(" ".join(str(part or "").split()) for part in parts if str(part or "").strip())
    return text[:600]


def _dedupe_key(
    *,
    tenant_key: str,
    session_key: str,
    action_type: str,
    dependency_type: str,
    evidence: dict[str, Any],
) -> str:
    seed = json.dumps(
        {
            "tenant_key": tenant_key,
            "session_key": session_key,
            "action_type": action_type,
            "dependency_type": dependency_type,
            "dependency_ref_type": str(evidence.get("dependency_ref_type") or ""),
            "dependency_ref_id": str(evidence.get("dependency_ref_id") or ""),
            "dependency_business_ref_id": str(evidence.get("dependency_business_ref_id") or ""),
            "dependency_ref_required": bool(evidence.get("dependency_ref_required")),
            "dependency_media_count": int(evidence.get("dependency_media_count") or 0),
            "user": str(evidence.get("user_message") or "")[:240],
            "assistant": str(evidence.get("assistant_reply") or "")[:240],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(seed.encode()).hexdigest()[:64]


_SUPPORTED_OBLIGATION_ACTIONS = {
    ACTION_GENERATE_SKIN_DIARY,
    ACTION_GENERATE_DEEP_REPORT,
    ACTION_CONFIRM_SKINCARE_CABINET_RECORD,
}

_SUPPORTED_DEPENDENCY_TYPES = {
    DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
    DEPENDENCY_CABINET_PRODUCT_RESEARCH_SUCCEEDED,
}


async def _llm_complete_system_user(
    llm: LLMProvider,
    *,
    system: str,
    user: str,
    max_tokens: int,
    cache_tenant_key: str = "__default__",
) -> str:
    parts: list[str] = []
    async for chunk in llm.stream_with_retry(
        [
            {
                "role": "system",
                "content": system,
                "_cache_stable_prefix": system,
                "_cache_dynamic_tail": "",
                "_cache_tenant_key": cache_tenant_key,
                "_cache_session_key": "__shared__",
                "_cache_lane": "cold_path",
            },
            {"role": "user", "content": user},
        ],
        max_tokens=max_tokens,
        temperature=0.0,
    ):
        if isinstance(chunk, TextChunk):
            parts.append(chunk.token)
    return "".join(parts).strip()


def _hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode()).hexdigest()


def _parse_json_safe(raw: str) -> dict | None:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start:end])
        except json.JSONDecodeError:
            pass
    return None
