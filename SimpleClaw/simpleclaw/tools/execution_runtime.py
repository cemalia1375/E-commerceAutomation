"""
让 ReactLoop 保持简单，把 guard / 派发 / 归一化都集中在这一处

JSON 协议：工具如果在 ToolResult.content 里塞 JSON，runtime 会读这些字段判定成败
"""

from __future__ import annotations

import json
import inspect
import uuid
from typing import Any

from loguru import logger

from simpleclaw.core.messages import ToolCall
from simpleclaw.harness.lifecycle import ToolGateDecision, ToolInvocationContext, ToolLifecycle
from simpleclaw.runtime.services import RuntimeServices
from simpleclaw.runtime.task_protocol import TaskEnvelope
from simpleclaw.tools.base import Tool, ToolResult
from simpleclaw.tools.invocation import (
    ToolInvocationCompletion,
    ToolInvocationRecord,
    ToolInvocationStore,
)


class ToolExecutionRuntime:
    """Execute one ToolCall through guard, policy, dispatch, and normalization."""

    _ERROR_HINT = "\n\n[Analyze the error above and try a different approach.]"
    _SUCCESS_ACTIONS = {
        "executed",
        "submitted",
        "queued",
        "wait_external",
        "triggered",
        "accepted",
        "noop",
        "deduped",
        "blocked",
        "deferred",
        "external",
        "waiting_external",
    }
    _SUCCESS_STATUSES = {
        "executed",
        "submitted",
        "queued",
        "wait_external",
        "triggered",
        "triggered_async",
        "external",
        "waiting_external",
        "already_submitted_in_turn",
        "accepted",
        "noop",
        "deduped",
        "deferred",
    }

    def __init__(
        self,
        runtime_services: RuntimeServices | None = None,
        tool_lifecycle: ToolLifecycle | None = None,
        invocation_store: ToolInvocationStore | None = None,
    ) -> None:
        self._runtime_services = runtime_services
        self._tool_lifecycle = tool_lifecycle
        self._invocation_store = invocation_store

    @property
    def runtime_services(self) -> RuntimeServices | None:
        return self._runtime_services

    def set_runtime_services(self, runtime_services: RuntimeServices | None) -> None:
        self._runtime_services = runtime_services

    @property
    def tool_lifecycle(self) -> ToolLifecycle | None:
        return self._tool_lifecycle

    def set_tool_lifecycle(self, tool_lifecycle: ToolLifecycle | None) -> None:
        self._tool_lifecycle = tool_lifecycle

    @property
    def invocation_store(self) -> ToolInvocationStore | None:
        return self._invocation_store

    def set_invocation_store(self, invocation_store: ToolInvocationStore | None) -> None:
        self._invocation_store = invocation_store

    async def invoke(self, call: ToolCall, tool: Tool | None, available_tools: list[str]) -> ToolResult:
        if tool is None:
            available = ", ".join(available_tools)
            return self._error_result(f"Unknown tool: '{call.name}'. Available: {available}")

        invocation_id: str | None = None
        params: dict[str, Any] = dict(call.arguments or {})
        try:
            params = tool.cast_params(params)
        except Exception as exc:
            logger.warning("tool cast_params failed: tool={} err={}", call.name, exc)
            return self._error_result(f"Failed to normalize parameters for '{call.name}': {self._stringify(exc)}")

        ctx = self._build_invocation_context(call, tool, params, available_tools)
        invocation_id = await self._record_invocation_started(ctx)

        try:
            errors = tool.validate_params(params)
        except Exception as exc:
            logger.warning("tool validate_params failed: tool={} err={}", call.name, exc)
            result = self._error_result(f"Validator failed for '{call.name}': {self._stringify(exc)}")
            await self._mark_invocation_completed(invocation_id, tool, result)
            return result

        if errors:
            result = self._error_result(
                f"Invalid parameters for tool '{call.name}': " + "; ".join(str(e) for e in errors)
            )
            await self._mark_invocation_completed(invocation_id, tool, result)
            return result

        gated = await self._invoke_before_tool(ctx)
        if gated is not None:
            await self._mark_invocation_completed(invocation_id, tool, gated)
            return gated

        if tool.execution_mode == "durable":
            result = await self._invoke_durable(tool, params)
        else:
            result = await self._invoke_inline(tool, params)
        await self._mark_invocation_completed(invocation_id, tool, result)
        return result

    async def _invoke_before_tool(
        self,
        ctx: ToolInvocationContext,
    ) -> ToolResult | None:
        if self._tool_lifecycle is None:
            return None
        try:
            decision = await self._tool_lifecycle.before_tool(ctx)
        except Exception as exc:
            logger.warning("tool before_tool failed: tool={} err={}", ctx.tool_name, exc)
            return self._error_result(f"Tool '{ctx.tool_name}' before_tool check failed: {self._stringify(exc)}")
        if decision is None or decision.allowed:
            return None
        return self.normalize_result(self._gate_result(ctx.tool_name, decision))

    @staticmethod
    def _build_invocation_context(
        call: ToolCall,
        tool: Tool,
        params: dict[str, Any],
        available_tools: list[str],
    ) -> ToolInvocationContext:
        metadata: dict[str, Any] = {}
        for attr in (
            "_tenant_key",
            "_session_key",
            "_origin_session_key",
            "_query",
            "_media",
            "_message_id",
        ):
            value = getattr(tool, attr, None)
            if value not in (None, ""):
                metadata[attr.removeprefix("_")] = value

        return ToolInvocationContext(
            call=call,
            tool=tool,
            tool_name=call.name,
            params=params,
            available_tools=list(available_tools),
            tenant_key=_clean_optional_str(getattr(tool, "_tenant_key", None)),
            session_key=_clean_optional_str(getattr(tool, "_session_key", None)),
            origin_session_key=_clean_optional_str(getattr(tool, "_origin_session_key", None)),
            user_message=_clean_optional_str(getattr(tool, "_query", None)),
            metadata=metadata,
        )

    async def _invoke_inline(self, tool: Tool, params: dict[str, Any]) -> ToolResult:
        try:
            result = await tool.execute(**params)
        except Exception as exc:
            logger.warning("tool execute failed: tool={} err={}", tool.name, exc)
            return self._error_result(f"Tool '{tool.name}' raised: {self._stringify(exc)}")
        return self.normalize_result(result)

    async def _invoke_durable(self, tool: Tool, params: dict[str, Any]) -> ToolResult:
        if self._runtime_services is None:
            return self._error_result(f"Tool '{tool.name}' requires runtime services for durable execution")

        try:
            prepared = await tool.prepare_task(**params)
        except Exception as exc:
            logger.warning("tool prepare_task failed: tool={} err={}", tool.name, exc)
            return self._error_result(f"Tool '{tool.name}' failed to prepare task: {self._stringify(exc)}")

        if isinstance(prepared, ToolResult):
            return self.normalize_result(prepared)
        if not isinstance(prepared, TaskEnvelope):
            return self._error_result(f"Tool '{tool.name}' returned invalid durable task")
        context_error = self._validate_durable_task_context(tool, prepared)
        if context_error:
            return self._error_result(context_error)

        try:
            queue_id = await self._submit_runtime_task(prepared, tool_name=tool.name)
        except Exception as exc:
            logger.warning("tool durable submit failed: tool={} err={}", tool.name, exc)
            return self._error_result(f"Tool '{tool.name}' failed to submit task: {self._stringify(exc)}")

        try:
            await tool.on_task_submitted(prepared, queue_id)
        except Exception as exc:
            logger.warning("tool on_task_submitted failed: tool={} err={}", tool.name, exc)

        return self.normalize_result(tool.durable_result(prepared, queue_id))

    @staticmethod
    def _validate_durable_task_context(tool: Tool, task: TaskEnvelope) -> str | None:
        tenant_key = str(task.tenant_key or "").strip()
        session_key = str(task.session_key or "").strip()
        if not tenant_key or tenant_key == "__default__":
            return (
                f"Tool '{tool.name}' refused to submit durable task because tenant context is missing. "
                "This is a runtime wiring error; do not claim the task was submitted."
            )
        if session_key == "cli:direct":
            return (
                f"Tool '{tool.name}' refused to submit durable task because session context is missing. "
                "This is a runtime wiring error; do not claim the task was submitted."
            )
        return None

    async def _submit_runtime_task(self, task: TaskEnvelope, *, tool_name: str) -> str:
        if self._runtime_services is None:
            raise RuntimeError("runtime services are not configured")
        submit_task = self._runtime_services.submit_task
        if _call_accepts_keyword(submit_task, "tool_name"):
            return await submit_task(task, tool_name=tool_name)
        return await submit_task(task)

    async def _record_invocation_started(self, ctx: ToolInvocationContext) -> str | None:
        if self._invocation_store is None:
            return None
        invocation_id = uuid.uuid4().hex
        tool = ctx.tool
        record = ToolInvocationRecord(
            invocation_id=invocation_id,
            tenant_key=ctx.tenant_key or "",
            session_key=ctx.session_key or "",
            tool_call_id=ctx.call.id,
            tool_name=ctx.tool_name,
            tool_category=_tool_category(tool),
            execution_mode=str(getattr(tool, "execution_mode", "")),
            status="running",
            input_json=_json_object(ctx.params),
        )
        try:
            await self._invocation_store.record_started(record)
        except Exception as exc:
            logger.warning("tool invocation record_started failed: tool={} err={}", ctx.tool_name, exc)
            return None
        return invocation_id

    async def _mark_invocation_completed(
        self,
        invocation_id: str | None,
        tool: Tool,
        result: ToolResult,
    ) -> None:
        if not invocation_id or self._invocation_store is None:
            return
        payload = self._parse_payload(result.content) or {}
        completion = ToolInvocationCompletion(
            status=_invocation_status(tool, result, payload),
            output_summary=_summary(result.content),
            runtime_task_id=_optional_str(payload.get("task_id")),
            business_ref_type=_business_ref_type(tool, payload),
            business_ref_id=_business_ref_id(tool, payload),
            trace_id=_optional_str(payload.get("trace_id")),
            last_error=_last_error(result, payload),
        )
        try:
            await self._invocation_store.mark_completed(invocation_id, completion)
        except Exception as exc:
            logger.warning("tool invocation mark_completed failed: tool={} err={}", tool.name, exc)

    def normalize_result(self, result: ToolResult) -> ToolResult:
        payload = self._parse_payload(result.content)
        if payload is None:
            if result.ok:
                return result
            return ToolResult(
                content=self._with_error_hint(result.content),
                ok=False,
                persist_to_history=result.persist_to_history,
                metadata=dict(result.metadata),
            )

        ok = self._payload_indicates_success(payload)
        if ok:
            return ToolResult(
                content=result.content,
                ok=True,
                persist_to_history=result.persist_to_history,
                metadata=dict(result.metadata),
            )

        error = payload.get("error")
        if error is not None:
            return self._error_result(str(error))
        if result.ok:
            return result
        return ToolResult(
            content=self._with_error_hint(result.content),
            ok=False,
            persist_to_history=result.persist_to_history,
            metadata=dict(result.metadata),
        )

    @classmethod
    def _payload_indicates_success(cls, payload: dict[str, Any]) -> bool:
        explicit_ok = payload.get("ok")
        if isinstance(explicit_ok, bool):
            return explicit_ok
        action = str(payload.get("action") or "").strip().lower()
        status = str(payload.get("status") or "").strip().lower()
        if action in cls._SUCCESS_ACTIONS or status in cls._SUCCESS_STATUSES:
            return True
        if bool(payload.get("created_new_job")) or bool(payload.get("deduped")):
            return True
        return False

    @staticmethod
    def _parse_payload(content: str) -> dict[str, Any] | None:
        try:
            value = json.loads(content)
        except Exception:
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _stringify(exc: Exception) -> str:
        return str(exc).strip() or exc.__class__.__name__

    @classmethod
    def _with_error_hint(cls, text: str) -> str:
        if not text:
            text = "Tool execution failed"
        return text if text.endswith(cls._ERROR_HINT) else text + cls._ERROR_HINT

    @classmethod
    def _error_result(cls, error: str) -> ToolResult:
        if not error.startswith("Error"):
            error = f"Error: {error}"
        return ToolResult(content=cls._with_error_hint(error), ok=False)

    @staticmethod
    def _gate_result(tool_name: str, decision: ToolGateDecision) -> ToolResult:
        return ToolResult(
            content=json.dumps(decision.to_payload(tool_name=tool_name), ensure_ascii=False),
            ok=decision.ok,
        )


def _clean_optional_str(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _call_accepts_keyword(func: Any, keyword: str) -> bool:
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return True
    for param in signature.parameters.values():
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            return True
    return keyword in signature.parameters


def _json_object(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    try:
        json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return {"_unserializable": str(value)}
    return value


def _tool_category(tool: Tool | None) -> str:
    if tool is None:
        return "sync_read"
    explicit = str(getattr(tool, "tool_category", "") or "").strip()
    if explicit:
        return explicit
    return "async_task" if getattr(tool, "execution_mode", "inline") == "durable" else "sync_read"


def _invocation_status(tool: Tool, result: ToolResult, payload: dict[str, Any]) -> str:
    if not result.ok:
        return "failed"

    action = str(payload.get("action") or "").strip().lower()
    status = str(payload.get("status") or "").strip().lower()
    signal = action or status
    if signal in {"deduped", "already_submitted_in_turn"}:
        return "deduped"
    if signal in {"deferred", "blocked"}:
        return "blocked"
    if signal == "noop":
        return "succeeded"
    if getattr(tool, "execution_mode", "inline") == "durable":
        if signal in {
            "submitted",
            "queued",
            "wait_external",
            "triggered",
            "accepted",
            "triggered_async",
            "external",
            "waiting_external",
        }:
            return "submitted"
        return "submitted"
    return "succeeded"


def _summary(content: str) -> str:
    text = str(content or "")
    if len(text) <= 2000:
        return text
    return text[:2000] + "...[truncated]"


def _business_ref_type(tool: Tool, payload: dict[str, Any]) -> str | None:
    explicit = _optional_str(payload.get("business_ref_type"))
    if explicit:
        return explicit
    return _optional_str(getattr(tool, "business_ref_type", None))


def _business_ref_id(tool: Tool, payload: dict[str, Any]) -> str | None:
    explicit = _optional_str(payload.get("business_ref_id"))
    if explicit:
        return explicit
    field = _optional_str(getattr(tool, "business_ref_id_field", None))
    if field:
        return _optional_str(payload.get(field))
    return None


def _last_error(result: ToolResult, payload: dict[str, Any]) -> str | None:
    if result.ok:
        return None
    return _optional_str(payload.get("error")) or _summary(result.content)


def _optional_str(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
