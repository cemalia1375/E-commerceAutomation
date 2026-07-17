"""SessionStore — 将 session_key 映射到 ReactLoop 实例，以 MySQL 作为持久化后端。

职责：
  - get_or_create：按 session_key 返回 ReactLoop，冷启动时调 MainAgent 装配
  - save_turn：将本轮新增消息 diff 追加写回 DB
  - get_lock：同一会话串行化的锁
  - TTL 淘汰 / clear / active_sessions 等生命周期管理

不负责：
  - 稳定前缀、工具、动态上下文、attention 的装配（全部委托给 MainAgent）
  - 其他 repo 读写（由调用方注入）
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import TYPE_CHECKING

from loguru import logger

from simpleclaw.context.compressor import ContextCompressionEvent, ContextCompressor
from simpleclaw.core.loop import ReactLoop
from simpleclaw.core.messages import AssistantMessage, ToolCall, ToolResultMessage, UserMessage
from simpleclaw.llm.base import LLMProvider
from simpleclaw.memory.ledger import MemoryLedgerRecord
from simpleclaw.memory.ledger_store import MemoryLedgerStore
from Mojing.agent.capabilities import AgentCapabilities
from Mojing.storage.session_repo import SessionRepository
from Mojing.storage.tenant_state_repo import TenantStateRepository

if TYPE_CHECKING:
    from Mojing.agent.main_agent import MainAgent
    from Mojing.agent.memory_extract import ExtractCallback

class SessionStore:
    def __init__(
        self,
        llm: LLMProvider,
        main_agent: "MainAgent",
        session_repo: SessionRepository,
        tenant_state_repo: TenantStateRepository | None = None,
        compressor: ContextCompressor | None = None,
        memory_extractor: "ExtractCallback | None" = None,
        memory_ledger_store: MemoryLedgerStore | None = None,
        session_ttl: int = 1800,  # 不活跃超过此秒数时淘汰内存会话（默认 30 分钟）
    ) -> None:
        self._llm = llm
        self._main_agent = main_agent
        self._repo = session_repo
        self._tenant_state_repo = tenant_state_repo
        self._compressor = compressor
        self._memory_extractor = memory_extractor
        self._memory_ledger_store = memory_ledger_store
        self._session_ttl = session_ttl
        self._sessions: dict[str, ReactLoop] = {}
        # session_key → 当前热装配能力。用于同一会话在 app / device 上下文间切换时
        # 重建 tool_registry/context_builder，但保留消息历史。
        self._session_capabilities: dict[str, AgentCapabilities] = {}
        # session_key → 当前热装配 journey stage。DB 里的 journey_json 是事实来源；
        # 这里仅用于判断内存 ReactLoop 是否需要刷新阶段 prompt。
        self._session_stages: dict[str, str] = {}
        # session_key → 最后活跃时间戳（用于 TTL 淘汰）
        self._last_active: dict[str, float] = {}
        # tenant_key → set of session_keys，用于 clear_tenant_sessions
        self._tenant_index: dict[str, set[str]] = {}
        # session_key → asyncio.Lock，防止同一会话并发写
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def main_agent(self) -> "MainAgent":
        """暴露持有的 MainAgent，供 CronScheduler 等需要装配上下文的外部调用方复用。"""
        return self._main_agent

    def get_lock(self, session_key: str) -> asyncio.Lock:
        """返回该 session 的专属锁，不存在时自动创建。

        调用方应在整个 run_turn 生命周期内持有锁，防止同一会话并发写入。
        """
        if session_key not in self._locks:
            self._locks[session_key] = asyncio.Lock()
        return self._locks[session_key]

    async def get_or_create(
        self,
        session_key: str,
        tenant_key: str,
        *,
        capabilities: AgentCapabilities | None = None,
    ) -> ReactLoop:
        """返回该会话对应的 ReactLoop，必要时从数据库加载历史记录。

        冷启动时从 nb_tenant_state 读取该租户的当前 journey stage，
        再委托 MainAgent 装配 ContextBuilder 和 ToolRegistry。
        TTL 到期的会话会被驱逐，下次访问时重新冷启动。
        """
        capabilities = capabilities or AgentCapabilities()

        # TTL 淘汰：长时间不活跃的会话从内存中移除
        now = time.monotonic()
        if session_key in self._sessions:
            if now - self._last_active.get(session_key, 0) > self._session_ttl:
                self._evict(session_key)
            else:
                self._last_active[session_key] = now
                loop = self._sessions[session_key]
                stage = await self._current_stage(tenant_key)
                current_capabilities = self._session_capabilities.get(session_key)
                current_stage = self._session_stages.get(session_key)
                if current_capabilities != capabilities or current_stage != stage:
                    await self._replace_session_profile(
                        loop,
                        session_key=session_key,
                        tenant_key=tenant_key,
                        stage=stage,
                        capabilities=capabilities,
                    )
                return loop

        # 冷启动：读租户当前 stage
        stage = await self._current_stage(tenant_key)

        builder = await self._main_agent.make_context_builder(
            tenant_key,
            stage=stage,
            capabilities=capabilities,
        )
        session_registry = self._main_agent.make_tool_registry(
            tenant_key,
            stage=stage,
            capabilities=capabilities,
        )

        async def _on_context_compressed(event: ContextCompressionEvent) -> None:
            await self._handle_context_compressed(
                tenant_key=tenant_key,
                session_key=session_key,
                event=event,
            )

        loop = ReactLoop(
            llm=self._llm,
            tool_registry=session_registry,
            context_builder=builder,
            compressor=self._compressor,
            on_context_compressed=_on_context_compressed,
        )

        # 从数据库加载已有历史记录及 consolidated 指针
        stored, last_consolidated = await self._repo.load_messages(tenant_key, session_key)
        loaded_messages = _deserialize_messages(stored)
        history_offset = min(max(int(last_consolidated or 0), 0), len(loaded_messages))
        loop.history_offset = history_offset
        loop.messages = loaded_messages[history_offset:]
        loop.consolidated_from = 0

        self._sessions[session_key] = loop
        self._session_capabilities[session_key] = capabilities
        self._session_stages[session_key] = stage
        self._last_active[session_key] = time.monotonic()
        self._tenant_index.setdefault(tenant_key, set()).add(session_key)
        return loop

    async def _replace_session_profile(
        self,
        loop: ReactLoop,
        *,
        session_key: str,
        tenant_key: str,
        stage: str | None = None,
        capabilities: AgentCapabilities,
    ) -> None:
        """Hot-swap prompt/tool profile while preserving durable conversation state."""
        stage = stage or await self._current_stage(tenant_key)
        loop.context_builder = await self._main_agent.make_context_builder(
            tenant_key,
            stage=stage,
            capabilities=capabilities,
        )
        loop.tool_registry = self._main_agent.make_tool_registry(
            tenant_key,
            stage=stage,
            capabilities=capabilities,
        )
        self._session_capabilities[session_key] = capabilities
        self._session_stages[session_key] = stage
        logger.info(
            "session profile swapped tenant={} session={} stage={} device_enabled={}",
            tenant_key,
            session_key,
            stage,
            capabilities.device_enabled,
        )

    async def _current_stage(self, tenant_key: str) -> str:
        stage = "novice"
        if self._tenant_state_repo is not None:
            stage = await self._tenant_state_repo.get_stage(tenant_key)
        return stage

    async def maybe_compress(self, session_key: str, tenant_key: str) -> bool:
        """检查当前会话的工作窗口是否超阈值；如超过则原地压缩并触发后台记忆提取。

        同步调用，但只做本地 token 估算 + 指针推进 + DB 更新 + durable task 入队。
        LLM 记忆提取由 memory_extract worker 异步执行。

        Returns:
            True  若本次实际压缩了（推进了 consolidated_from），
            False 若未触发（loop 不在、未达阈值、未配置 compressor）。
        """
        if self._compressor is None:
            return False

        loop = self._sessions.get(session_key)
        if loop is None:
            return False

        loop.normalize_message_window()
        result = await self._compressor.compress_window(loop.messages)
        if not result.changed:
            return False

        dropped_messages = result.dropped
        old_ptr = loop.history_offset
        new_ptr = loop.history_offset + result.dropped_count

        try:
            await self._repo.update_consolidated(tenant_key, session_key, new_ptr)
        except Exception as exc:
            logger.warning("maybe_compress: update_consolidated failed: {}", exc)
            return False

        # DB 落库成功后，再推进内存指针并触发 durable memory_extract。
        loop.messages = result.current
        loop.history_offset = new_ptr
        loop.consolidated_from = 0

        logger.info(
            "maybe_compress tenant={} session={} dropped={} new_ptr={}",
            tenant_key, session_key, result.dropped_count, new_ptr,
        )

        # 触发 durable memory_extract（入队本身同步等待，真正提取由 worker 异步执行）
        if self._memory_extractor is not None and dropped_messages:
            ledger = await self._create_memory_ledger(
                tenant_key=tenant_key,
                session_key=session_key,
                source="main",
                trigger_type="context_compression",
                dropped_messages=dropped_messages,
                last_consolidated_from=old_ptr,
                last_consolidated_to=new_ptr,
                tokens_before=result.tokens_before,
                tokens_after=result.tokens_after,
            )
            try:
                await self._memory_extractor(
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
                logger.warning("maybe_compress: memory_extract enqueue failed: {}", exc)

        return True

    async def _handle_context_compressed(
        self,
        *,
        tenant_key: str,
        session_key: str,
        event: ContextCompressionEvent,
    ) -> None:
        """Persist ReactLoop in-turn compression side effects."""
        try:
            await self._repo.update_consolidated(
                tenant_key,
                session_key,
                event.history_offset_after,
            )
        except Exception as exc:
            logger.warning("context_compressed: update_consolidated failed: {}", exc)

        if self._memory_extractor is not None and event.dropped:
            ledger = await self._create_memory_ledger(
                tenant_key=tenant_key,
                session_key=session_key,
                source="main",
                trigger_type="in_turn_compression",
                dropped_messages=event.dropped,
                last_consolidated_from=event.history_offset_before,
                last_consolidated_to=event.history_offset_after,
                tokens_before=event.tokens_before,
                tokens_after=event.tokens_after,
                metadata={"react_iteration": event.iteration, "react_trigger": event.trigger},
            )
            try:
                await self._memory_extractor(
                    tenant_key,
                    event.dropped,
                    session_key=session_key,
                    ledger_id=ledger.ledger_id if ledger else None,
                    last_consolidated_from=event.history_offset_before,
                    last_consolidated_to=event.history_offset_after,
                    message_seq_start=event.history_offset_before,
                    message_seq_end=max(event.history_offset_before, event.history_offset_after - 1),
                    trigger_type="in_turn_compression",
                    tokens_before=event.tokens_before,
                    tokens_after=event.tokens_after,
                )
            except Exception as exc:
                logger.warning("context_compressed: memory_extract enqueue failed: {}", exc)

    async def _create_memory_ledger(
        self,
        *,
        tenant_key: str,
        session_key: str,
        source: str,
        trigger_type: str,
        dropped_messages: list,
        last_consolidated_from: int,
        last_consolidated_to: int,
        tokens_before: int,
        tokens_after: int,
        metadata: dict | None = None,
    ) -> MemoryLedgerRecord | None:
        if self._memory_ledger_store is None or not dropped_messages:
            return None
        source_chunk = _serialize_messages(dropped_messages)
        try:
            return await self._memory_ledger_store.create_ledger(MemoryLedgerRecord(
                tenant_key=tenant_key,
                session_key=session_key,
                source=source,
                trigger_type=trigger_type,  # type: ignore[arg-type]
                last_consolidated_from=last_consolidated_from,
                last_consolidated_to=last_consolidated_to,
                message_seq_start=last_consolidated_from,
                message_seq_end=max(last_consolidated_from, last_consolidated_to - 1),
                dropped_count=len(dropped_messages),
                tokens_before=tokens_before,
                tokens_after=tokens_after,
                source_chunk=source_chunk,
                source_chunk_hash=_source_chunk_hash(source_chunk),
                metadata=dict(metadata or {}),
            ))
        except Exception as exc:
            logger.warning(
                "memory ledger create failed tenant={} session={} source={} err={}",
                tenant_key,
                session_key,
                source,
                exc,
            )
            return None

    async def save_turn(
        self,
        session_key: str,
        tenant_key: str,
        messages_before: int,
    ) -> None:
        """将本轮新增的消息持久化到数据库。

        messages_before：调用 loop.run() 前的绝对消息序号。
        仅追加新增的尾部消息，避免重写整个历史记录。
        """
        loop = self._sessions.get(session_key)
        if loop is None:
            return

        all_openai = _serialize_messages(loop.messages)
        start_seq = max(messages_before, loop.history_offset)
        local_start = loop.local_index_for_absolute(start_seq)
        new_msgs = all_openai[local_start:]
        if new_msgs:
            await self._repo.append_messages(
                tenant_key, session_key, new_msgs,
                start_seq=start_seq,
                last_consolidated=loop.history_offset,
            )
        elif loop.history_offset:
            await self._repo.update_consolidated(
                tenant_key,
                session_key,
                loop.history_offset,
            )

    def set_turn_context(
        self,
        session_key: str,
        *,
        tenant_key: str = "",
        query: str = "",
        media: list[str] | None = None,
        message_id: str | None = None,
        device_id: int | str | None = None,
        device_code: str | None = None,
        origin_session_key: str | None = None,
        capture_photo_enabled: bool = True,
    ) -> None:
        """为支持 set_context() 的工具注入每轮上下文。

        在 ReactLoop.run() 调用前执行，使 AnalyzeImageTool、DeepResearchTool
        等工具获得本轮的 tenant_key、query、media 信息。
        """
        loop = self._sessions.get(session_key)
        if loop is None:
            return
        for tool in loop.tool_registry.tools:
            if hasattr(tool, "set_context"):
                tool.set_context(
                    tenant_key=tenant_key,
                    session_key=session_key,
                    origin_session_key=origin_session_key,
                    query=query,
                    media=list(media or []),
                    message_id=message_id,
                    device_id=device_id,
                    device_code=device_code,
                    capture_photo_enabled=capture_photo_enabled,
                    context_builder=loop.context_builder,
                )

    def get_loop(self, session_key: str) -> ReactLoop | None:
        """返回内存中的 ReactLoop 实例，若 session 已被清理则返回 None。"""
        return self._sessions.get(session_key)

    def _evict(self, session_key: str) -> None:
        """从内存中移除单个会话及其关联状态（TTL 到期或主动清理时调用）。"""
        self._sessions.pop(session_key, None)
        self._session_capabilities.pop(session_key, None)
        self._session_stages.pop(session_key, None)
        self._last_active.pop(session_key, None)
        self._locks.pop(session_key, None)

    def clear(self, session_key: str) -> None:
        self._evict(session_key)

    def clear_tenant_sessions(self, tenant_key: str) -> None:
        """从内存中移除该租户的所有会话，使下一轮冷启动时重建 ContextBuilder。

        激进做法：丢弃 prefix cache、丢弃 in-memory ReactLoop 状态。仅在
        必须冷重启的场景使用（如 admin 后台手动清空）。journey stage 跳转
        请使用 swap_tenant_overlay 做无感切换。
        """
        for sk in self._tenant_index.pop(tenant_key, set()):
            self._evict(sk)

    async def swap_tenant_overlay(self, tenant_key: str, new_stage: str) -> int:
        """阶段切换时无感替换 in-memory session 的 context_builder / tool_registry。

        保留 messages / consolidated_from 等所有运行时状态——下一轮 turn 只是
        prompt 的 stable_sections 段切到新 stage。Prefix cache 命中率不受影响。

        Returns: 实际被换的 session 数量（可能为 0，比如该 tenant 还没冷启动过）。
        """
        affected = 0
        for sk in list(self._tenant_index.get(tenant_key, set())):
            loop = self._sessions.get(sk)
            if loop is None:
                continue
            capabilities = self._session_capabilities.get(sk) or AgentCapabilities()
            loop.context_builder = await self._main_agent.make_context_builder(
                tenant_key, stage=new_stage, capabilities=capabilities
            )
            loop.tool_registry = self._main_agent.make_tool_registry(
                tenant_key, stage=new_stage, capabilities=capabilities
            )
            self._session_stages[sk] = new_stage
            affected += 1
            logger.info(
                "session journey overlay swapped tenant={} session={} stage={}",
                tenant_key,
                sk,
                new_stage,
            )
        return affected

    @property
    def active_sessions(self) -> list[str]:
        return list(self._sessions.keys())


# ------------------------------------------------------------------
# 消息序列化辅助函数
# ------------------------------------------------------------------

def _serialize_messages(messages: list) -> list[dict]:
    """将内部 Message 对象转换为 OpenAI 字典格式，用于数据库存储。"""
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


def _deserialize_messages(openai_msgs: list[dict]) -> list:
    """将 OpenAI 字典格式转换为内部 Message 对象。"""
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
