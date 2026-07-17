"""SubagentStore — 持久化子 Agent 会话的入口。

职责：
  - 根据 session_key 前缀路由到对应的 SubagentBase 实现
  - 内存缓存消息历史（与 SessionStore 对齐，避免每轮 DB load）
  - 调用 SubagentRunner 执行单轮对话
  - 将新消息写回 SessionRepository

不负责：
  - LLM 实例管理（由构造参数传入）
  - 子 Agent 类型注册（由调用方在构造时传入 subagents 列表）
  - SSE 格式化（on_token 回调由调用方提供）

内存缓存策略（对齐 SessionStore）：
  - session_key → (messages, last_consolidated) 缓存在 _sessions
  - scene skill 激活状态单独缓存在 _active_scene_skills
  - TTL 淘汰：不活跃超过 session_ttl 秒后下次冷启动
  - 冷启动：从 DB 读完整历史；热路径：直接取内存

消息持久化复用 SessionRepository（nb_session_messages 表），
通过不同的 session_key 前缀与主 Agent 隔离。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from simpleclaw.context import AttentionPacket
from simpleclaw.context.compressor import ContextCompressor
from simpleclaw.core.loop import _build_user_content
from simpleclaw.core.messages import AssistantMessage, ToolCall, ToolResultMessage, UserMessage
from simpleclaw.harness.hooks import TurnContext
from simpleclaw.llm.base import LLMProvider
from simpleclaw.memory.ledger import MemoryLedgerRecord
from simpleclaw.memory.ledger_store import MemoryLedgerStore
from simpleclaw.runtime.task_protocol import TaskEnvelope
from simpleclaw.subagent.base import SubagentBase
from simpleclaw.subagent.runner import SubagentRunner
from simpleclaw.subagent.runtime import SubagentRunRequest, SubagentRunResult

from Mojing.agent.first_token import (
    build_first_token_continuation_instruction,
    build_first_token_context_message,
    build_first_token_user_message,
    join_first_token_reply,
)
from Mojing.runtime.streams import MojingTaskStream
from Mojing.storage.session_repo import SessionRepository

if TYPE_CHECKING:
    from simpleclaw.runtime.services import RuntimeServices
    from Mojing.agent.memory_extract import ExtractCallback
    from Mojing.storage.session_store import SessionStore


class SubagentStore:
    """子 Agent 会话的持久化执行入口。"""

    def __init__(
        self,
        llm: LLMProvider,
        subagents: list[SubagentBase],
        session_repo: SessionRepository,
        session_store: "SessionStore",
        postprocess_runtime: "RuntimeServices | None" = None,
        compressor: ContextCompressor | None = None,
        memory_extractors: dict[str, "ExtractCallback"] | None = None,
        memory_ledger_store: MemoryLedgerStore | None = None,
        subagent_runtime_repo: Any | None = None,
        publish_fn: Callable[[str, str, dict[str, Any]], Awaitable[int]] | None = None,
        session_ttl: int = 1800,
    ) -> None:
        self._runner = SubagentRunner(llm)
        self._subagents: list[SubagentBase] = subagents
        self._repo = session_repo
        self._session_store = session_store
        self._postprocess_runtime = postprocess_runtime
        self._compressor = compressor
        self._memory_extractors = memory_extractors or {}
        self._memory_ledger_store = memory_ledger_store
        self._subagent_runtime_repo = subagent_runtime_repo
        self._publish_fn = publish_fn
        self._session_ttl = session_ttl
        # session_key → (messages, last_consolidated)
        self._sessions: dict[str, tuple[list, int]] = {}
        self._active_scene_skills: dict[str, list[str]] = {}
        self._last_active: dict[str, float] = {}

    @property
    def active_sessions(self) -> list[str]:
        return list(self._sessions.keys())

    def _evict(self, session_key: str) -> None:
        self._sessions.pop(session_key, None)
        self._active_scene_skills.pop(session_key, None)
        self._last_active.pop(session_key, None)

    def find_subagent(self, session_key: str) -> SubagentBase | None:
        """根据 session_key 找到对应的子 Agent 类型，无匹配返回 None。"""
        for agent in self._subagents:
            if agent.matches(session_key):
                return agent
        return None

    async def _begin_subagent_run(
        self,
        *,
        subagent: SubagentBase,
        tenant_key: str,
        session_key: str,
        message: str,
        media: list[str] | None,
        message_id: str | None,
        report_id: str | None,
        origin_session_key: str | None,
        handoff_contract: dict[str, object] | None,
        ingress_id: str | None,
        request: SubagentRunRequest | None,
        runtime_task_id: str | None,
    ) -> str | None:
        if self._subagent_runtime_repo is None:
            return None

        req = request or SubagentRunRequest(
            tenant_key=tenant_key,
            session_key=session_key,
            subagent_name=subagent.name,
            objective=_compact_objective(message),
            run_mode="chat",
            owner_type="session_ingress" if ingress_id else "manual",
            owner_id=str(ingress_id or message_id or "").strip() or None,
            input_refs={
                "ingress_id": str(ingress_id or ""),
                "message_id": str(message_id or ""),
                "origin_session_key": str(origin_session_key or ""),
            },
            payload={
                "message": message,
                "media_count": len(media or []),
                "report_id": str(report_id or ""),
                "handoff_contract": handoff_contract or {},
            },
        )
        admitted = req.admitted() if req.status == "candidate" else req
        try:
            await self._subagent_runtime_repo.create_run(
                admitted,
                runtime_task_id=runtime_task_id,
            )
            await self._subagent_runtime_repo.mark_run_status(
                admitted.run_id,
                "running",
                summary=f"{subagent.name} run started",
            )
        except Exception as exc:
            logger.warning(
                "subagent runtime create failed: subagent={} tenant={} session={} err={}",
                subagent.name,
                tenant_key,
                session_key,
                exc,
            )
            return None
        return admitted.run_id

    async def _complete_subagent_run(
        self,
        run_id: str | None,
        *,
        subagent_name: str,
        reply_text: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if not run_id or self._subagent_runtime_repo is None:
            return
        try:
            await self._subagent_runtime_repo.complete_run(
                SubagentRunResult.completed(
                    run_id,
                    summary=f"{subagent_name} run completed",
                    reply_text=reply_text,
                    metadata=metadata or {},
                )
            )
        except Exception as exc:
            logger.warning(
                "subagent runtime complete failed: subagent={} run_id={} err={}",
                subagent_name,
                run_id,
                exc,
            )

    async def _fail_subagent_run(
        self,
        run_id: str | None,
        *,
        error: str,
        subagent_name: str,
    ) -> None:
        if not run_id or self._subagent_runtime_repo is None:
            return
        try:
            await self._subagent_runtime_repo.complete_run(
                SubagentRunResult.failed(
                    run_id,
                    error,
                    summary=f"{subagent_name} run failed",
                )
            )
        except Exception as exc:
            logger.warning(
                "subagent runtime fail failed: subagent={} run_id={} err={}",
                subagent_name,
                run_id,
                exc,
            )

    async def run_turn(
        self,
        session_key: str,
        tenant_key: str,
        message: str,
        *,
        on_token: Callable[[str], Awaitable[None]] | None = None,
        media: list[str] | None = None,
        message_id: str | None = None,
        report_id: str | None = None,
        origin_session_key: str | None = None,
        handoff_contract: dict[str, object] | None = None,
        first_token_agent: Any | None = None,
        on_first_token: Callable[[str], Awaitable[None]] | None = None,
        on_first_token_status: Callable[..., Awaitable[None]] | None = None,
        on_prompt_messages: Callable[[list[dict]], None] | None = None,
        on_attention_packets: Callable[[list[Any]], None] | None = None,
        ingress_id: str | None = None,
        subagent_run_request: SubagentRunRequest | None = None,
        runtime_task_id: str | None = None,
    ) -> str:
        """Run one subagent turn and optionally persist subagent runtime facts."""
        subagent = self.find_subagent(session_key)
        if subagent is None:
            raise ValueError(f"No subagent registered for session_key={session_key!r}")

        run_id = await self._begin_subagent_run(
            subagent=subagent,
            tenant_key=tenant_key,
            session_key=session_key,
            message=message,
            media=media,
            message_id=message_id,
            report_id=report_id,
            origin_session_key=origin_session_key,
            handoff_contract=handoff_contract,
            ingress_id=ingress_id,
            request=subagent_run_request,
            runtime_task_id=runtime_task_id,
        )
        try:
            reply = await self._run_turn_impl(
                session_key=session_key,
                tenant_key=tenant_key,
                message=message,
                on_token=on_token,
                media=media,
                message_id=message_id,
                report_id=report_id,
                origin_session_key=origin_session_key,
                handoff_contract=handoff_contract,
                first_token_agent=first_token_agent,
                on_first_token=on_first_token,
                on_first_token_status=on_first_token_status,
                on_prompt_messages=on_prompt_messages,
                on_attention_packets=on_attention_packets,
            )
        except Exception as exc:
            await self._fail_subagent_run(
                run_id,
                error=str(exc) or exc.__class__.__name__,
                subagent_name=subagent.name,
            )
            raise

        await self._complete_subagent_run(
            run_id,
            subagent_name=subagent.name,
            reply_text=reply,
            metadata={
                "message_id": str(message_id or ""),
                "ingress_id": str(ingress_id or ""),
                "origin_session_key": str(origin_session_key or ""),
            },
        )
        return reply

    async def _run_turn_impl(
        self,
        session_key: str,
        tenant_key: str,
        message: str,
        *,
        on_token: Callable[[str], Awaitable[None]] | None = None,
        media: list[str] | None = None,
        message_id: str | None = None,
        report_id: str | None = None,
        origin_session_key: str | None = None,
        handoff_contract: dict[str, object] | None = None,
        first_token_agent: Any | None = None,
        on_first_token: Callable[[str], Awaitable[None]] | None = None,
        on_first_token_status: Callable[..., Awaitable[None]] | None = None,
        on_prompt_messages: Callable[[list[dict]], None] | None = None,
        on_attention_packets: Callable[[list[Any]], None] | None = None,
    ) -> str:
        """执行单轮子 Agent 对话，持久化新消息，返回完整回复文本。

        Args:
            session_key:  子 Agent 会话标识（如 "skin_diary:{tenant_key}"）
            tenant_key:   租户标识
            message:      本轮用户消息
            on_token:     流式 token 回调（可选，用于 SSE 实时推送）
            report_id:    可选的深度报告 ID；仅深度报告子 Agent 会消费，
                          其他子 Agent 忽略。缺省时子 Agent 走 latest fallback。
            first_token_agent:
                          可选的旁路 first_token_llm；仅直连 /agent/chat 传入，
                          后台 subagent_dispatch 不启用。
            on_prompt_messages:
                          可选调试观察回调。每次子 Agent 调用主模型前，
                          收到最终 messages；生产路径通常不传。
            on_attention_packets:
                          可选调试观察回调。每次子 Agent 实际注入
                          attention packets 时触发；生产路径通常不传。

        Returns:
            完整回复文本（同时已写入 DB）
        """
        subagent = self.find_subagent(session_key)
        if subagent is None:
            raise ValueError(f"No subagent registered for session_key={session_key!r}")

        dispatch_ctx: TurnContext | None = None
        visible_reply = ""
        session_lock = self._session_store.get_lock(session_key)
        async with session_lock:
            # 内存缓存命中检查（对齐 SessionStore）
            now = time.monotonic()
            if session_key in self._sessions and now - self._last_active.get(session_key, 0) > self._session_ttl:
                self._evict(session_key)

            if session_key in self._sessions:
                history, last_consolidated = self._sessions[session_key]
                logger.info(
                    "subagent session.ready: cache_hit session={} history_n={} active_skills={}",
                    session_key, len(history), self._active_scene_skills.get(session_key) or [],
                )
            else:
                stored, last_consolidated = await self._repo.load_messages(tenant_key, session_key)
                loaded_history = _deserialize_messages(stored)
                history_offset = min(max(int(last_consolidated or 0), 0), len(loaded_history))
                history = loaded_history[history_offset:]
                last_consolidated = history_offset
                logger.info(
                    "subagent session.cold: db_loaded session={} history_n={}",
                    session_key, len(history),
                )

            history, last_consolidated = await self._maybe_compress(
                subagent=subagent,
                tenant_key=tenant_key,
                session_key=session_key,
                history=history,
                last_consolidated=last_consolidated,
            )
            messages_before = last_consolidated + len(history)
            messages_before_local = len(history)

            # actual_media 是用户本轮真实上传；model_media 允许子 Agent 在明确
            # 复核历史图时附带最近图片，但不污染 cold_path 的“本轮上传”信号。
            actual_media = list(media or [])
            model_media = await subagent.prepare_turn_media(
                tenant_key,
                message=message,
                media=actual_media,
            )
            opener_task: asyncio.Task | None = None
            opener_buffer: list[str] = []
            opener_input = build_first_token_user_message(message, actual_media)
            if first_token_agent is not None and on_first_token is not None and opener_input.strip():
                if on_first_token_status is not None:
                    await on_first_token_status(
                        "started",
                        model=first_token_agent.config.model,
                        timeout_ms=int(first_token_agent.timeout_s * 1000),
                        agent_lane=subagent.name,
                    )

                async def _on_opener_token(token: str) -> None:
                    opener_buffer.append(token)
                    await on_first_token(token)

                opener_task = asyncio.create_task(
                    first_token_agent.generate_stream(
                        tenant_key=tenant_key,
                        session_key=session_key,
                        user_message=opener_input,
                        history=history,
                        consolidated_from=0,
                        history_offset=last_consolidated,
                        agent_lane=subagent.name,
                        on_token=_on_opener_token,
                    )
                )
            elif on_first_token_status is not None:
                await on_first_token_status("disabled", agent_lane=subagent.name)

            # 构建本轮上下文
            context_builder = await subagent.make_context_builder(tenant_key)
            for skill_name in self._active_scene_skills.get(session_key) or []:
                try:
                    context_builder.activate_skill(skill_name)
                except Exception as exc:
                    logger.warning(
                        "subagent active scene restore failed: subagent={} tenant={} session={} skill={} err={}",
                        subagent.name, tenant_key, session_key, skill_name, exc,
                    )
            dynamic_context_sections = await subagent.fetch_dynamic_context_sections(
                tenant_key,
                message=message,
                media=actual_media,
                report_id=report_id,
            )
            attention_packets = await subagent.fetch_attention_packets(
                tenant_key,
                message=message,
                media=actual_media,
                report_id=report_id,
            )
            tool_registry = subagent.make_tool_registry(tenant_key)
            if self._postprocess_runtime is not None:
                tool_registry.set_runtime_services(self._postprocess_runtime)

            # 为工具注入租户上下文（支持 set_context() 协议）
            for tool in tool_registry.tools:
                if hasattr(tool, "set_context"):
                    tool.set_context(
                        tenant_key=tenant_key,
                        session_key=session_key,
                        origin_session_key=origin_session_key,
                        query=message,
                        media=actual_media,
                        message_id=message_id,
                        context_builder=context_builder,
                        handoff_contract=handoff_contract,
                    )

            opener_text, opener_status, opener_detail = await _resolve_opener_text(
                opener_task=opener_task,
                timeout_s=getattr(first_token_agent, "timeout_s", 0.0),
                opener_buffer=opener_buffer,
                tenant_key=tenant_key,
                session_key=session_key,
                subagent_name=subagent.name,
            )
            if on_first_token_status is not None and opener_task is not None:
                await on_first_token_status(
                    opener_status,
                    chars=len(opener_text),
                    detail=opener_detail,
                    agent_lane=subagent.name,
                )
            if opener_text:
                logger.info(
                    "subagent first_token opener.sent subagent={} tenant={} session={} chars={}",
                    subagent.name, tenant_key, session_key, len(opener_text),
                )
                history.append(UserMessage(_build_user_content(message, actual_media)))
                history.append(AssistantMessage(build_first_token_context_message(opener_text)))
                continuation = build_first_token_continuation_instruction(opener_text)
                if continuation:
                    attention_packets.append(AttentionPacket(
                        content=continuation,
                        source="first_token_continuation",
                        priority=1000,
                        lifetime="one_turn",
                        role="system",
                        placement="tail",
                    ))

            main_separator_pending = bool(opener_text)

            async def _on_token(token: str) -> None:
                nonlocal main_separator_pending
                visible_token = token
                if main_separator_pending:
                    visible_token = visible_token if visible_token.startswith("\n") else "\n" + visible_token
                    main_separator_pending = False
                if on_token is not None:
                    await on_token(visible_token)
                # Direct /agent/chat calls already stream tokens through on_token.
                # EventHub is only for background subagent_dispatch output.
                if on_token is None and self._publish_fn is not None:
                    await self._publish_fn(
                        tenant_key,
                        session_key,
                        {
                            "type": "chunk",
                            "source": "subagent",
                            "subagent": subagent.name,
                            "data": {"text": visible_token},
                        },
                    )

            # 执行单轮 ReactLoop
            updated_history, reply = await self._runner.run_turn(
                message=message,
                history=history,
                context_builder=context_builder,
                tool_registry=tool_registry,
                dynamic_context_sections=dynamic_context_sections,
                media=actual_media,
                model_media=model_media,
                persist_user_input=not bool(opener_text),
                on_token=_on_token,
                consolidated_from=0,
                history_offset=last_consolidated,
                attention_packets=attention_packets,
                context_metadata={
                    "tenant_key": tenant_key,
                    "session_key": session_key,
                    "message_id": message_id,
                    "report_id": report_id,
                    "origin_session_key": origin_session_key,
                    "handoff_contract": handoff_contract or {},
                    "subagent": subagent.name,
                    "image_just_uploaded": bool(actual_media),
                    "media": actual_media,
                },
                on_prompt_messages=on_prompt_messages,
                on_attention_packets=on_attention_packets,
            )

            visible_reply = join_first_token_reply(opener_text, reply)

            # 持久化：只要 ReactLoop 追加了新消息就落库（哪怕只有 user message / 失败轮）。
            # ReactLoop.run() 会在入口立即追加 UserMessage，所以 new_msgs 在成功轮、
            # 空回复轮、异常轮都可能非空。
            all_serialized = _serialize_messages(updated_history)
            new_message_objects = updated_history[messages_before_local:]
            postprocess_hints = _extract_postprocess_hints(new_message_objects)
            new_msgs = all_serialized[messages_before_local:]
            if new_msgs:
                try:
                    await self._repo.append_messages(
                        tenant_key, session_key, new_msgs,
                        start_seq=messages_before,
                        last_consolidated=last_consolidated,
                    )
                except Exception as exc:
                    logger.warning("SubagentStore.append_messages failed: {}", exc)

            # 回写内存缓存（DB 落库后再更新，保证一致性）
            self._sessions[session_key] = (updated_history, last_consolidated)
            active_skill_names = list(getattr(context_builder, "active_skill_names", []) or [])
            if active_skill_names:
                self._active_scene_skills[session_key] = active_skill_names
            else:
                self._active_scene_skills.pop(session_key, None)
            self._last_active[session_key] = time.monotonic()

            # Dispatch：只有 assistant 可见回复才触发 post-turn 副作用。
            # first_token opener 已经展示给用户时，也应作为本轮 assistant_reply 的前缀。
            if visible_reply:
                dispatch_ctx = TurnContext(
                    tenant_key=tenant_key,
                    session_key=session_key,
                    user_message=message,
                    assistant_reply=visible_reply,
                    media=actual_media,
                    first_token_reply=opener_text,
                    main_assistant_reply=reply,
                    postprocess_hints=postprocess_hints,
                )
                try:
                    await subagent.on_turn_completed(dispatch_ctx)
                except Exception as exc:
                    logger.warning(
                        "subagent on_turn_completed failed: subagent={} tenant={} session={} err={}",
                        subagent.name, tenant_key, session_key, exc,
                    )

        if dispatch_ctx is not None:
            # 三档优先级（从最优到 fallback）：
            #   1. effects.dispatch() 自定义副作用 —— 当前未注入，预留扩展点
            #   2. runtime task queue —— 生产路径走这一档：post-turn 任务进
            #      nb_runtime_tasks，失败可重试、admin 可见
            #   3. asyncio.create_task fire-and-forget —— 兜底降级，仅在未注入
            #      runtime 时走（生产环境必然注入，不会走）；保留给本地调试或
            #      未来新子 Agent 迁移期的过渡形态
            effects = subagent.make_post_turn_effects()
            if effects is not None:
                for item in await effects.dispatch(dispatch_ctx):
                    if item.ok:
                        logger.info(
                            "subagent post-turn queued: subagent={} type={} stream={} "
                            "tenant={} session={} queue_id={}",
                            subagent.name, item.task_type, item.stream,
                            tenant_key, session_key, item.queue_id,
                        )
                    else:
                        logger.warning(
                            "subagent post-turn dispatch failed: subagent={} type={} "
                            "stream={} tenant={} session={} err={}",
                            subagent.name, item.task_type, item.stream,
                            tenant_key, session_key, item.error,
                        )
            elif self._postprocess_runtime is not None:
                await self._enqueue_post_turn_tasks(subagent, dispatch_ctx)
            else:
                postprocess_hook = subagent.make_postprocess_hook()
                if postprocess_hook is not None:
                    asyncio.create_task(postprocess_hook.on_turn_end(dispatch_ctx))

                cold_path_hook = subagent.make_cold_path_hook()
                if cold_path_hook is not None:
                    asyncio.create_task(cold_path_hook.on_turn_end(dispatch_ctx))

        if on_token is None and self._publish_fn is not None:
            await self._publish_fn(
                tenant_key,
                session_key,
                {
                    "type": "done",
                    "source": "subagent",
                    "subagent": subagent.name,
                    "data": {"current_state": subagent.name},
                },
            )

        return visible_reply

    async def _maybe_compress(
        self,
        *,
        subagent: SubagentBase,
        tenant_key: str,
        session_key: str,
        history: list,
        last_consolidated: int,
    ) -> tuple[list, int]:
        """对子 Agent 历史做与主 Agent 对齐的 pre-turn compression。"""
        if self._compressor is None:
            return history, last_consolidated

        result = await self._compressor.compress_window(history)
        if not result.changed:
            return history, last_consolidated

        dropped_messages = result.dropped
        old_ptr = last_consolidated
        new_ptr = last_consolidated + result.dropped_count

        try:
            await self._repo.update_consolidated(tenant_key, session_key, new_ptr)
        except Exception as exc:
            logger.warning("SubagentStore.update_consolidated failed: {}", exc)
            return history, last_consolidated

        extractor = self._memory_extractors.get(subagent.memory_source())
        if extractor is not None and dropped_messages:
            ledger = await self._create_memory_ledger(
                tenant_key=tenant_key,
                session_key=session_key,
                source=subagent.memory_source(),
                dropped_messages=dropped_messages,
                last_consolidated_from=old_ptr,
                last_consolidated_to=new_ptr,
                tokens_before=result.tokens_before,
                tokens_after=result.tokens_after,
            )
            try:
                await extractor(
                    tenant_key,
                    dropped_messages,
                    session_key=session_key,
                    ledger_id=ledger.ledger_id if ledger else None,
                    last_consolidated_from=old_ptr,
                    last_consolidated_to=new_ptr,
                    message_seq_start=old_ptr,
                    message_seq_end=max(old_ptr, new_ptr - 1),
                    trigger_type="context_compression",
                    tokens_before=result.tokens_before,
                    tokens_after=result.tokens_after,
                )
            except Exception as exc:
                logger.warning("SubagentStore.memory_extract enqueue failed: {}", exc)

        logger.info(
            "subagent maybe_compress: subagent={} tenant={} session={} dropped={} new_ptr={}",
            subagent.name, tenant_key, session_key, result.dropped_count, new_ptr,
        )
        return result.current, new_ptr

    async def _create_memory_ledger(
        self,
        *,
        tenant_key: str,
        session_key: str,
        source: str,
        dropped_messages: list,
        last_consolidated_from: int,
        last_consolidated_to: int,
        tokens_before: int,
        tokens_after: int,
    ) -> MemoryLedgerRecord | None:
        if self._memory_ledger_store is None or not dropped_messages:
            return None
        source_chunk = _serialize_messages(dropped_messages)
        try:
            return await self._memory_ledger_store.create_ledger(MemoryLedgerRecord(
                tenant_key=tenant_key,
                session_key=session_key,
                source=source,
                trigger_type="context_compression",
                last_consolidated_from=last_consolidated_from,
                last_consolidated_to=last_consolidated_to,
                message_seq_start=last_consolidated_from,
                message_seq_end=max(last_consolidated_from, last_consolidated_to - 1),
                dropped_count=len(dropped_messages),
                tokens_before=tokens_before,
                tokens_after=tokens_after,
                source_chunk=source_chunk,
                source_chunk_hash=_source_chunk_hash(source_chunk),
            ))
        except Exception as exc:
            logger.warning(
                "subagent memory ledger create failed tenant={} session={} source={} err={}",
                tenant_key,
                session_key,
                source,
                exc,
            )
            return None

    async def _enqueue_post_turn_tasks(self, subagent: SubagentBase, ctx: TurnContext) -> None:
        """把子 Agent 的 post-turn 副作用入队到 runtime。"""
        assert self._postprocess_runtime is not None

        payload = {
            "tenant_key": ctx.tenant_key,
            "session_key": ctx.session_key,
            "user_message": ctx.user_message,
            "assistant_reply": ctx.assistant_reply,
            "media": list(ctx.media or []),
            "first_token_reply": ctx.first_token_reply,
            "main_assistant_reply": ctx.main_assistant_reply or ctx.assistant_reply,
            "postprocess_hints": list(ctx.postprocess_hints or []),
        }
        tasks: list[TaskEnvelope] = []
        if subagent.make_postprocess_hook() is not None:
            tasks.append(TaskEnvelope(
                task_type=f"{subagent.name}_postprocess",
                payload=payload,
                stream=MojingTaskStream.POSTPROCESS,
                tenant_key=ctx.tenant_key,
                session_key=ctx.session_key,
                scope_key=f"postprocess:{ctx.tenant_key}:USER.md",
                service_role="mojing:subagent-post-turn",
            ))
        # 子 Agent 的输出是执行事实，不再独立写全局 obligation ledger。
        # 全局待办由主会话 cold path 统一维护，避免子会话把“正在生成中”
        # 这类通知文本再次解释成新的待办。
        if not tasks:
            return

        results = await asyncio.gather(
            *[self._postprocess_runtime.submit_task(task) for task in tasks],
            return_exceptions=True,
        )
        for task, result in zip(tasks, results):
            if isinstance(result, Exception):
                logger.warning(
                    "subagent post-turn enqueue failed: subagent={} type={} tenant={} session={} err={}",
                    subagent.name, task.task_type, ctx.tenant_key, ctx.session_key, result,
                )
            else:
                logger.info(
                    "subagent post-turn queued: subagent={} type={} tenant={} session={} queue_id={}",
                    subagent.name, task.task_type, ctx.tenant_key, ctx.session_key, result,
                )


async def _resolve_opener_text(
    *,
    opener_task: asyncio.Task | None,
    timeout_s: float,
    opener_buffer: list[str],
    tenant_key: str,
    session_key: str,
    subagent_name: str,
) -> tuple[str, str, str]:
    if opener_task is None:
        return "", "disabled", ""
    try:
        result = await asyncio.wait_for(asyncio.shield(opener_task), timeout=timeout_s)
    except asyncio.TimeoutError:
        buffered_text = "".join(opener_buffer).strip()
        if not buffered_text:
            opener_task.cancel()
            logger.info(
                "subagent first_token opener timeout before first delta after {}s subagent={} tenant={} session={}",
                timeout_s, subagent_name, tenant_key, session_key,
            )
            return "", "timeout", f"timeout after {timeout_s}s"
        logger.info(
            "subagent first_token opener started before timeout; waiting for completion after {}s subagent={} tenant={} session={}",
            timeout_s, subagent_name, tenant_key, session_key,
        )
        try:
            result = await opener_task
        except Exception as exc:
            logger.warning(
                "subagent first_token opener failed after partial output subagent={} tenant={} session={}: {}",
                subagent_name, tenant_key, session_key, exc,
            )
            return buffered_text, "failed_after_partial", str(exc)
        text = str(getattr(result, "text", "") or "").strip() if result is not None else buffered_text
        return text or buffered_text, "done_after_timeout", f"first delta arrived before {timeout_s}s"
    except Exception as exc:
        logger.warning(
            "subagent first_token opener failed subagent={} tenant={} session={}: {}",
            subagent_name, tenant_key, session_key, exc,
        )
        return "".join(opener_buffer).strip(), "failed", str(exc)
    text = str(getattr(result, "text", "") or "").strip() if result is not None else ""
    return text, "done" if text else "empty", ""


# ------------------------------------------------------------------
# 消息序列化辅助（与 SessionStore 保持一致）
# ------------------------------------------------------------------

def _serialize_messages(messages: list) -> list[dict]:
    result = []
    for msg in messages:
        if isinstance(msg, UserMessage):
            result.append({"role": "user", "content": msg.content})
        elif isinstance(msg, AssistantMessage):
            if msg.tool_calls:
                result.append({
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": tc.arguments},
                        }
                        for tc in msg.tool_calls
                    ],
                })
            else:
                result.append({"role": "assistant", "content": msg.content})
        elif isinstance(msg, ToolResultMessage):
            result.append({
                "role": "tool",
                "tool_call_id": msg.call_id,
                "content": msg.content,
            })
    return result


def _source_chunk_hash(messages: list[dict]) -> str:
    raw = json.dumps(messages, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _compact_objective(message: str, *, limit: int = 240) -> str:
    text = " ".join(str(message or "").split()).strip()
    if not text:
        return "subagent turn"
    return text if len(text) <= limit else text[:limit].rstrip() + "..."


def _extract_postprocess_hints(messages: list) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, ToolResultMessage):
            continue
        try:
            payload = json.loads(msg.content or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        hint = payload.get("postprocess_hint") if isinstance(payload, dict) else None
        if isinstance(hint, dict):
            hints.append(hint)
    return hints


def _deserialize_messages(openai_msgs: list[dict]) -> list:
    result = []
    for msg in openai_msgs:
        role = msg.get("role", "")
        if role == "user":
            result.append(UserMessage(content=msg.get("content") or ""))
        elif role == "assistant":
            tool_calls = []
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function") or {}
                tool_calls.append(ToolCall(
                    id=tc.get("id") or "",
                    name=fn.get("name") or "",
                    arguments=fn.get("arguments") or {},
                ))
            result.append(AssistantMessage(
                content=msg.get("content") or "",
                tool_calls=tool_calls,
            ))
        elif role == "tool":
            result.append(ToolResultMessage(
                call_id=msg.get("tool_call_id") or "",
                content=msg.get("content") or "",
            ))
    return result
