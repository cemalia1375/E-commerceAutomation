"""Mojing 主会话 ingress 协调器。

这层只负责把业务输入翻译成 SessionIngressItem 并入队：

  - HTTP 用户消息 -> user_message ingress
  - 后台完成事实 -> system_activation ingress
  - request-local execution context 保存在进程内
  - 真正的 dropped / preempt / busy 决策由 scheduler 负责
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from simpleclaw.context import AttentionPacket
from simpleclaw.core.messages import AssistantMessage
from simpleclaw.llm.chunks import TextChunk, ToolCallChunk
from simpleclaw.runtime import (
    InMemorySessionIngressStore,
    SessionIngressDispatchResult,
    SessionIngressItem,
    SessionIngressQueue,
    SessionTurnScheduler,
)

if TYPE_CHECKING:
    from Mojing.storage.completion_event_repo import CompletionEventRepository
    from Mojing.storage.session_store import SessionStore


TextFormatter = Callable[[str], str]
StatusFormatter = Callable[..., str]
PublishEvent = Callable[[str, str, dict[str, Any]], Awaitable[int]]
PromptMessagesCallback = Callable[[list[dict[str, Any]]], None]
AttentionPacketsCallback = Callable[[list[AttentionPacket]], None]


@dataclass(slots=True)
class UserTurnExecutionContext:
    """HTTP request-local turn execution context."""

    ingress_id: str
    session_key: str
    tenant_key: str
    message: str
    queue: asyncio.Queue[str | None]
    on_text: TextFormatter
    on_done: Callable[[], str]
    on_error: Callable[[str], str]
    on_first_token_text: TextFormatter | None = None
    on_first_token_status: StatusFormatter | None = None
    media: list[str] | None = None
    message_id: str | None = None
    device_id: int | str | None = None
    device_code: str | None = None
    prompt_surface: str = "app"
    capture_photo_enabled: bool = True
    report_id: str | None = None
    origin_session_key: str | None = None
    on_prompt_messages: PromptMessagesCallback | None = None
    on_attention_packets: AttentionPacketsCallback | None = None
    cancelled: bool = False
    run_task: asyncio.Task[None] | None = None


@dataclass(slots=True)
class SystemActivationExecutionContext:
    """Background activation context for proactive main-session reminders."""

    ingress_id: str
    session_key: str
    tenant_key: str
    activation_kind: str
    summary: str
    reminder_text: str
    payload_json: dict[str, Any]
    cancelled: bool = False
    run_task: asyncio.Task[None] | None = None


TurnRunner = Callable[[UserTurnExecutionContext], Awaitable[None]]


class MainSessionIngressCoordinator:
    """Bridge between SimpleClaw session ingress scheduler and Mojing turn runner."""

    def __init__(
        self,
        run_turn: TurnRunner,
        *,
        sessions: "SessionStore | None" = None,
        publish_event: PublishEvent | None = None,
        completion_event_repo: "CompletionEventRepository | None" = None,
    ) -> None:
        self._store = InMemorySessionIngressStore()
        self._queue = SessionIngressQueue(self._store)
        self._contexts: dict[str, UserTurnExecutionContext | SystemActivationExecutionContext] = {}
        self._contexts_lock = asyncio.Lock()
        self._scheduler = SessionTurnScheduler(
            self._queue,
            self._execute_turn,
            on_terminal=self._on_terminal_ingress,
        )
        self._run_turn = run_turn
        self._sessions = sessions
        self._publish_event = publish_event
        self._completion_event_repo = completion_event_repo

    @property
    def scheduler(self) -> SessionTurnScheduler:
        return self._scheduler

    @property
    def store(self) -> InMemorySessionIngressStore:
        return self._store

    async def submit_user_message(
        self,
        *,
        session_key: str,
        tenant_key: str,
        message: str,
        queue: asyncio.Queue[str | None],
        on_text: TextFormatter,
        on_done: Callable[[], str],
        on_error: Callable[[str], str],
        on_first_token_text: TextFormatter | None = None,
        on_first_token_status: StatusFormatter | None = None,
        media: list[str] | None = None,
        message_id: str | None = None,
        device_id: int | str | None = None,
        device_code: str | None = None,
        prompt_surface: str = "app",
        capture_photo_enabled: bool = True,
        report_id: str | None = None,
        origin_session_key: str | None = None,
        on_prompt_messages: PromptMessagesCallback | None = None,
        on_attention_packets: AttentionPacketsCallback | None = None,
    ) -> str:
        item = SessionIngressItem.user_message(
            session_key=session_key,
            tenant_key=tenant_key,
            content=message,
            source="http",
            payload_json={
                "message_id": message_id,
                "media": list(media or []),
                "device_id": device_id,
                "device_code": device_code,
                "prompt_surface": prompt_surface,
                "capture_photo_enabled": capture_photo_enabled,
                "report_id": report_id,
                "origin_session_key": origin_session_key,
            },
        )
        stored = await self._queue.enqueue(item)
        ctx = UserTurnExecutionContext(
            ingress_id=stored.ingress_id,
            session_key=session_key,
            tenant_key=tenant_key,
            message=message,
            queue=queue,
            on_text=on_text,
            on_done=on_done,
            on_error=on_error,
            on_first_token_text=on_first_token_text,
            on_first_token_status=on_first_token_status,
            media=media,
            message_id=message_id,
            device_id=device_id,
            device_code=device_code,
            prompt_surface=prompt_surface,
            capture_photo_enabled=capture_photo_enabled,
            report_id=report_id,
            origin_session_key=origin_session_key,
            on_prompt_messages=on_prompt_messages,
            on_attention_packets=on_attention_packets,
        )
        async with self._contexts_lock:
            self._contexts[stored.ingress_id] = ctx

        logger.info(
            "session_ingress.submit_user_message queued ingress={} session={} tenant={}",
            stored.ingress_id,
            session_key,
            tenant_key,
        )
        asyncio.create_task(self._drain(stored.ingress_id, session_key))
        return stored.ingress_id

    async def submit_system_activation(
        self,
        *,
        session_key: str,
        tenant_key: str,
        activation_kind: str,
        task_id: str,
        summary: str,
        reminder_text: str,
        source_type: str = "runtime_task",
        source_id: str | None = None,
        source_session_key: str | None = None,
        business_ref_type: str | None = None,
        business_ref_id: str | None = None,
        priority: str = "low",
        delivery_policy: str = "best_effort",
        preempt_policy: str = "drop_if_session_busy_or_user_arrives",
        expires_at_ms: int | None = None,
        dedupe_key: str | None = None,
        persist_completion_event: bool = True,
        payload_json: dict[str, Any] | None = None,
    ) -> str:
        payload = dict(payload_json or {})
        effective_source_id = str(source_id or task_id or "").strip()
        payload.update({
            "activation_kind": activation_kind,
            "task_id": task_id,
            "summary": summary,
            "reminder_text": reminder_text,
            "source_type": source_type,
            "source_id": effective_source_id,
            "source_session_key": source_session_key,
            "target_session_key": session_key,
            "business_ref_type": business_ref_type,
            "business_ref_id": business_ref_id,
            "dedupe_key": dedupe_key,
        })
        item = SessionIngressItem.system_activation(
            session_key=session_key,
            tenant_key=tenant_key,
            source=f"{source_type}:{activation_kind}",
            priority=priority,  # type: ignore[arg-type]
            delivery_policy=delivery_policy,  # type: ignore[arg-type]
            preempt_policy=preempt_policy,  # type: ignore[arg-type]
            dedupe_key=dedupe_key,
            expires_at_ms=expires_at_ms,
            summary=summary,
            payload_json=payload,
        )
        stored = await self._queue.enqueue(item)
        if persist_completion_event and self._completion_event_repo is not None:
            from Mojing.runtime.activations.models import ActivationRequest

            await self._completion_event_repo.upsert_from_activation(
                ActivationRequest(
                    session_key=session_key,
                    tenant_key=tenant_key,
                    activation_kind=activation_kind,
                    task_id=task_id,
                    summary=summary,
                    reminder_text=reminder_text,
                    source_type=source_type,  # type: ignore[arg-type]
                    source_id=source_id,
                    source_session_key=source_session_key,
                    business_ref_type=business_ref_type,
                    business_ref_id=business_ref_id,
                    priority=priority,  # type: ignore[arg-type]
                    delivery_policy=delivery_policy,  # type: ignore[arg-type]
                    preempt_policy=preempt_policy,  # type: ignore[arg-type]
                    expires_at_ms=expires_at_ms,
                    dedupe_key=dedupe_key,
                    persist_completion_event=persist_completion_event,
                    payload_json=payload_json or {},
                ),
                ingress_id=stored.ingress_id,
            )
        ctx = SystemActivationExecutionContext(
            ingress_id=stored.ingress_id,
            session_key=session_key,
            tenant_key=tenant_key,
            activation_kind=activation_kind,
            summary=summary,
            reminder_text=reminder_text,
            payload_json=dict(stored.payload_json or {}),
        )
        async with self._contexts_lock:
            self._contexts[stored.ingress_id] = ctx

        logger.info(
            "session_ingress.submit_system_activation queued ingress={} session={} tenant={} source={} kind={} source_id={} task_id={}",
            stored.ingress_id,
            session_key,
            tenant_key,
            source_type,
            activation_kind,
            effective_source_id,
            task_id,
        )
        drop_summary = await self._scheduler.should_drop_on_enqueue(stored)
        if drop_summary is not None:
            await self._store.mark_dropped(stored.ingress_id, summary=drop_summary)
            await self._cleanup_context(stored.ingress_id)
            logger.info(
                "session_ingress.submit_system_activation dropped ingress={} session={} kind={} reason={}",
                stored.ingress_id,
                session_key,
                activation_kind,
                drop_summary,
            )
            return stored.ingress_id

        asyncio.create_task(self._drain(stored.ingress_id, session_key))
        return stored.ingress_id

    async def cancel(self, ingress_id: str) -> None:
        async with self._contexts_lock:
            ctx = self._contexts.get(ingress_id)
        if ctx is None:
            return
        ctx.cancelled = True
        if ctx.run_task is not None and not ctx.run_task.done():
            ctx.run_task.cancel()

    async def _execute_turn(
        self,
        item: SessionIngressItem,
    ) -> SessionIngressDispatchResult:
        logger.info(
            "session_ingress.execute_turn.start ingress={} session={} type={}",
            item.ingress_id,
            item.session_key,
            item.message_type,
        )
        async with self._contexts_lock:
            ctx = self._contexts.get(item.ingress_id)
        if ctx is None:
            return SessionIngressDispatchResult.dropped(
                "missing request-local ingress execution context",
            )
        if getattr(ctx, "cancelled", False):
            await self._cleanup_context(item.ingress_id)
            return SessionIngressDispatchResult.dropped(
                "request cancelled before dispatch",
            )
        if isinstance(ctx, UserTurnExecutionContext):
            logger.info(
                "session_ingress.execute_turn.run_user ingress={} session={}",
                item.ingress_id,
                item.session_key,
            )
            ctx.run_task = asyncio.create_task(self._run_turn(ctx))
        else:
            logger.info(
                "session_ingress.execute_turn.run_activation ingress={} session={}",
                item.ingress_id,
                item.session_key,
            )
            ctx.run_task = asyncio.create_task(self._run_system_activation(ctx))
        try:
            await ctx.run_task
        except asyncio.CancelledError:
            return SessionIngressDispatchResult.dropped(
                "request cancelled during dispatch",
            )
        except Exception as exc:
            if isinstance(ctx, UserTurnExecutionContext):
                try:
                    await ctx.queue.put(ctx.on_error(str(exc) or exc.__class__.__name__))
                    await ctx.queue.put(None)
                except Exception:
                    logger.exception(
                        "session_ingress failed to emit user turn error ingress={}",
                        item.ingress_id,
                    )
            raise
        finally:
            await self._cleanup_context(item.ingress_id)
        return SessionIngressDispatchResult.delivered(
            f"{item.turn_kind} delivered",
        )

    async def _cleanup_context(self, ingress_id: str) -> None:
        async with self._contexts_lock:
            self._contexts.pop(ingress_id, None)

    async def _on_terminal_ingress(self, item: SessionIngressItem, status: str) -> None:
        del status
        await self._cleanup_context(item.ingress_id)

    async def _run_system_activation(self, ctx: SystemActivationExecutionContext) -> None:
        if self._sessions is None or self._publish_event is None:
            raise RuntimeError("system activation runtime dependencies are not configured")

        session_lock = self._sessions.get_lock(ctx.session_key)
        async with session_lock:
            loop = await self._sessions.get_or_create(ctx.session_key, ctx.tenant_key)
            await self._sessions.maybe_compress(ctx.session_key, ctx.tenant_key)
            messages_before = loop.absolute_message_count
            source_type = str(ctx.payload_json.get("source_type") or "unknown")
            source_id = str(ctx.payload_json.get("source_id") or "")
            attention_packets = [
                AttentionPacket(
                    content=(
                        "【系统触发】以下是一个系统主动事件，只围绕这一条事件提醒用户：\n"
                        f"- 来源：{source_type}\n"
                        f"- 来源ID：{source_id}\n"
                        f"- 类型：{ctx.activation_kind}\n"
                        f"- 摘要：{ctx.summary}\n"
                        f"- 提醒意图：{ctx.reminder_text}\n"
                        "只围绕本条 activation 的提醒意图回复。"
                        "不要复述历史里已经说过的其他任务状态，不要把图片分析、肌肤日记、深度报告等多个任务合并成阶段汇总。"
                        "不要伪装成用户输入，也不要重复提醒。"
                    ),
                    source=f"system_activation:{ctx.activation_kind}",
                    priority=85,
                    lifetime="one_turn",
                    placement="tail",
                    metadata={
                        "activation_kind": ctx.activation_kind,
                        "task_id": str(ctx.payload_json.get("task_id") or ""),
                        "source_type": source_type,
                        "source_id": source_id,
                        "source_session_key": str(ctx.payload_json.get("source_session_key") or ""),
                    },
                )
            ]
            try:
                await self._run_activation_generation(
                    ctx,
                    loop=loop,
                    attention_packets=attention_packets,
                    source_type=source_type,
                    source_id=source_id,
                )
            finally:
                try:
                    await self._sessions.save_turn(ctx.session_key, ctx.tenant_key, messages_before)
                except Exception as exc:
                    logger.warning("system activation save_turn failed: session={} err={}", ctx.session_key, exc)

    async def _run_activation_generation(
        self,
        ctx: SystemActivationExecutionContext,
        *,
        loop: Any,
        attention_packets: list[AttentionPacket],
        source_type: str,
        source_id: str,
    ) -> None:
        """Generate a read-only proactive notification without exposing tools."""
        context_metadata = {
            "tenant_key": ctx.tenant_key,
            "session_key": ctx.session_key,
            "entrypoint": "system_activation",
            "activation_kind": ctx.activation_kind,
            "runtime_task_id": str(ctx.payload_json.get("task_id") or ""),
            "activation_source_type": source_type,
            "activation_source_id": source_id,
            "source_session_key": str(ctx.payload_json.get("source_session_key") or ""),
        }
        loop.normalize_message_window()
        if loop.context_builder is not None:
            messages = await loop.context_builder.build(
                loop.messages,
                dynamic_context_sections=[],
                attention_packets=attention_packets,
                metadata=context_metadata,
                query=ctx.reminder_text,
            )
        else:
            messages = loop._build_messages_from(loop.messages)

        text_parts: list[str] = []
        try:
            async for chunk in loop.llm.stream_with_retry(messages, tools=None):
                if isinstance(chunk, TextChunk):
                    text_parts.append(chunk.token)
                    await self._publish_event(
                        ctx.tenant_key,
                        ctx.session_key,
                        {
                            "type": "chunk",
                            "node": "expert",
                            "data": {
                                "text": chunk.token,
                                "source": "system_activation",
                                "activation_kind": ctx.activation_kind,
                            },
                        },
                    )
                elif isinstance(chunk, ToolCallChunk):
                    logger.warning(
                        "system activation ignored tool call: tenant={} session={} kind={} tool={}",
                        ctx.tenant_key,
                        ctx.session_key,
                        ctx.activation_kind,
                        chunk.name,
                    )
            reply_text = "".join(text_parts).strip()
            if reply_text:
                loop.messages.append(AssistantMessage(reply_text))
            await self._publish_event(
                ctx.tenant_key,
                ctx.session_key,
                {
                    "type": "done",
                    "node": None,
                    "data": {
                        "current_state": "expert",
                        "source": "system_activation",
                        "activation_kind": ctx.activation_kind,
                    },
                },
            )
            if self._completion_event_repo is not None:
                await self._completion_event_repo.mark_consumed_by_activation(
                    tenant_key=ctx.tenant_key,
                    dedupe_key=str(ctx.payload_json.get("dedupe_key") or "") or None,
                    ingress_id=ctx.ingress_id,
                )
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            await self._publish_event(
                ctx.tenant_key,
                ctx.session_key,
                {
                    "type": "error",
                    "node": None,
                    "data": {
                        "error": message,
                        "source": "system_activation",
                        "activation_kind": ctx.activation_kind,
                    },
                },
            )

    async def _drain(self, ingress_id: str, session_key: str) -> None:
        try:
            logger.info("session_ingress.drain.start ingress={} session={}", ingress_id, session_key)
            await self._scheduler.drain(session_key)
            logger.info("session_ingress.drain.done ingress={} session={}", ingress_id, session_key)
        except Exception as exc:
            logger.exception("session ingress drain failed: session={} err={}", session_key, exc)
            await self._store.mark_failed(
                ingress_id,
                str(exc) or exc.__class__.__name__,
                summary="session ingress drain task failed",
            )
            await self._emit_error_and_close(
                ingress_id,
                f"session ingress drain failed: {exc}",
            )

    async def _emit_error_and_close(self, ingress_id: str, message: str) -> None:
        async with self._contexts_lock:
            ctx = self._contexts.pop(ingress_id, None)
        if ctx is None or not isinstance(ctx, UserTurnExecutionContext):
            return
        try:
            await ctx.queue.put(ctx.on_error(message))
        finally:
            await ctx.queue.put(None)
