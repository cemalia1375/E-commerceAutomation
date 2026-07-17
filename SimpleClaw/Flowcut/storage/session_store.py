# TODO: agent 层就绪后恢复 TYPE_CHECKING import
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
import base64
import time
from typing import TYPE_CHECKING

from loguru import logger

from simpleclaw.context.compressor import ContextCompressor
from simpleclaw.core.loop import ReactLoop
from simpleclaw.core.messages import AssistantMessage, ToolCall, ToolResultMessage, UserMessage
from simpleclaw.llm.base import LLMProvider
from Flowcut.storage.session_repo import SessionRepository

# TenantStateRepository 尚未实现，暂用 None 占位
TenantStateRepository = None  # TODO: 实现后替换

if TYPE_CHECKING:
    # TODO: agent 层就绪后取消注释
    # from Flowcut.agent.capabilities import AgentCapabilities
    # from Flowcut.agent.main_agent import MainAgent
    # from Flowcut.agent.memory_extract import ExtractCallback
    pass

# 运行时占位，避免 NameError
try:
    from Flowcut.agent.capabilities import AgentCapabilities  # type: ignore[import]
except ImportError:
    class AgentCapabilities:  # type: ignore[no-redef]
        """占位实现，agent 层就绪后替换。"""
        def __eq__(self, other: object) -> bool:
            return isinstance(other, AgentCapabilities)

        def __hash__(self) -> int:
            return hash(())


class SessionStore:
    def __init__(
        self,
        llm: LLMProvider,
        main_agent: "object",  # TODO: 替换为 MainAgent
        session_repo: SessionRepository,
        tenant_state_repo: object | None = None,
        compressor: ContextCompressor | None = None,
        memory_extractor: "object | None" = None,  # TODO: 替换为 ExtractCallback | None
        session_ttl: int = 1800,  # 不活跃超过此秒数时淘汰内存会话（默认 30 分钟）
    ) -> None:
        self._llm = llm
        self._main_agent = main_agent
        self._repo = session_repo
        self._tenant_state_repo = tenant_state_repo
        self._compressor = compressor
        self._memory_extractor = memory_extractor
        self._session_ttl = session_ttl
        self._sessions: dict[str, ReactLoop] = {}
        # session_key → 当前热装配能力。用于同一会话在 app / device 上下文间切换时
        # 重建 tool_registry/context_builder，但保留消息历史。
        self._session_capabilities: dict[str, AgentCapabilities] = {}
        # session_key → 最后活跃时间戳（用于 TTL 淘汰）
        self._last_active: dict[str, float] = {}
        # tenant_key → set of session_keys，用于 clear_tenant_sessions
        self._tenant_index: dict[str, set[str]] = {}
        # session_key → asyncio.Lock，防止同一会话并发写
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def main_agent(self) -> "object":
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
                if self._session_capabilities.get(session_key) != capabilities:
                    await self._replace_session_profile(
                        loop,
                        session_key=session_key,
                        tenant_key=tenant_key,
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

        loop = ReactLoop(
            llm=self._llm,
            tool_registry=session_registry,
            context_builder=builder,
        )

        # 从数据库加载已有历史记录及 consolidated 指针
        stored, last_consolidated = await self._repo.load_messages(tenant_key, session_key)
        loop.messages = _deserialize_messages(stored)
        loop.consolidated_from = last_consolidated

        self._sessions[session_key] = loop
        self._session_capabilities[session_key] = capabilities
        self._last_active[session_key] = time.monotonic()
        self._tenant_index.setdefault(tenant_key, set()).add(session_key)
        return loop

    async def _replace_session_profile(
        self,
        loop: ReactLoop,
        *,
        session_key: str,
        tenant_key: str,
        capabilities: AgentCapabilities,
    ) -> None:
        """Hot-swap prompt/tool profile while preserving durable conversation state."""
        stage = await self._current_stage(tenant_key)
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
        logger.info(
            "session profile swapped tenant={} session={} capabilities={}",
            tenant_key,
            session_key,
            capabilities,
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

        active = loop.messages[loop.consolidated_from:]
        compressed = await self._compressor.maybe_compress(active)
        if compressed is active:
            return False

        dropped_count = len(active) - len(compressed)
        dropped_messages = active[:dropped_count]
        new_ptr = loop.consolidated_from + dropped_count

        try:
            await self._repo.update_consolidated(tenant_key, session_key, new_ptr)
        except Exception as exc:
            logger.warning("maybe_compress: update_consolidated failed: {}", exc)
            return False

        # DB 落库成功后，再推进内存指针并触发 durable memory_extract。
        loop.consolidated_from = new_ptr

        logger.info(
            "maybe_compress tenant={} session={} dropped={} new_ptr={}",
            tenant_key, session_key, dropped_count, new_ptr,
        )

        # 触发 durable memory_extract（入队本身同步等待，真正提取由 worker 异步执行）
        if self._memory_extractor is not None and dropped_messages:
            try:
                await self._memory_extractor(tenant_key, dropped_messages)
            except Exception as exc:
                logger.warning("maybe_compress: memory_extract enqueue failed: {}", exc)

        return True

    async def save_turn(
        self,
        session_key: str,
        tenant_key: str,
        messages_before: int,
    ) -> None:
        """将本轮新增的消息持久化到数据库。

        messages_before：调用 loop.run() 前 loop.messages 的长度。
        仅追加新增的尾部消息，避免重写整个历史记录。
        """
        loop = self._sessions.get(session_key)
        if loop is None:
            return

        all_openai = _serialize_messages(loop.messages)
        new_msgs = all_openai[messages_before:]
        if new_msgs:
            await self._repo.append_messages(
                tenant_key, session_key, new_msgs,
                start_seq=messages_before,
                last_consolidated=loop.consolidated_from,
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
        ui_context: dict | None = None,
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
                    context_builder=loop.context_builder,
                    tenant_key=tenant_key,
                    session_key=session_key,
                    origin_session_key=origin_session_key,
                    query=query,
                    media=list(media or []),
                    message_id=message_id,
                    device_id=device_id,
                    device_code=device_code,
                )
        if ui_context is not None and loop.context_builder is not None:
            for provider in loop.context_builder._attention_providers:
                if hasattr(provider, "set_ui_context"):
                    provider.set_ui_context(ui_context)

    def get_loop(self, session_key: str) -> ReactLoop | None:
        """返回内存中的 ReactLoop 实例，若 session 已被清理则返回 None。"""
        return self._sessions.get(session_key)

    def _evict(self, session_key: str) -> None:
        """从内存中移除单个会话及其关联状态（TTL 到期或主动清理时调用）。"""
        self._sessions.pop(session_key, None)
        self._session_capabilities.pop(session_key, None)
        self._last_active.pop(session_key, None)
        self._locks.pop(session_key, None)

    def evict(self, session_key: str) -> None:
        """公开的驱逐方法（供 API 层在删除会话时清理内存）。"""
        self._evict(session_key)

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
            affected += 1
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
                assistant_dict: dict = {
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": tc.arguments},
                            # Gemini thought_signature 是 bytes，落库前 base64 编码
                            **(
                                {"thought_signature_b64": base64.b64encode(tc.thought_signature).decode("ascii")}
                                if tc.thought_signature
                                else {}
                            ),
                        }
                        for tc in msg.tool_calls
                    ],
                }
            else:
                assistant_dict = {"role": "assistant", "content": msg.content}
            # text Part 上的 signatures 落库（非空才写，避免字段膨胀）
            if msg.pending_signatures:
                assistant_dict["pending_signatures_b64"] = [
                    base64.b64encode(s).decode("ascii") for s in msg.pending_signatures
                ]
            result.append(assistant_dict)
        elif isinstance(msg, ToolResultMessage):
            result.append({
                "role": "tool",
                "tool_call_id": msg.call_id,
                "content": msg.content,
            })
    return result


_DUMMY_SIGNATURE = b"skip_thought_signature_validator"


def _deserialize_messages(openai_msgs: list[dict]) -> list:
    """将 OpenAI 字典格式转换为内部 Message 对象。

    兜底：旧版本写入 DB 的 assistant 消息没有 thought_signature_b64。
    对此类历史条目，使用官方推荐的 dummy signature 兜底，而非截断历史，
    以避免对话上下文丢失。
    """
    result = []
    for msg in openai_msgs:
        role = msg.get("role", "")
        if role == "user":
            result.append(UserMessage(content=msg.get("content") or ""))
        elif role == "assistant":
            raw_tcs = msg.get("tool_calls") or []
            # 统计缺 signature 的旧 tool_call 条目数
            missing_count = sum(
                1 for tc in raw_tcs
                if not isinstance(tc.get("thought_signature_b64"), str)
                or not tc.get("thought_signature_b64")
            )
            if missing_count:
                logger.info(
                    "检测到 {} 条旧 tool_call 无 thought_signature，使用 dummy 签名兜底",
                    missing_count,
                )
            tool_calls = []
            for tc in raw_tcs:
                fn = tc.get("function") or {}
                sig_b64 = tc.get("thought_signature_b64")
                if isinstance(sig_b64, str) and sig_b64:
                    try:
                        signature: bytes = base64.b64decode(sig_b64)
                    except Exception:
                        signature = _DUMMY_SIGNATURE
                else:
                    # 旧数据无 signature，使用 dummy 跳过 Gemini 校验
                    signature = _DUMMY_SIGNATURE
                tool_calls.append(ToolCall(
                    id=tc.get("id") or "",
                    name=fn.get("name") or "",
                    arguments=fn.get("arguments") or {},
                    thought_signature=signature,
                ))
            # 反序列化 text Part 上的 pending_signatures
            pending_sigs_b64: list[str] = msg.get("pending_signatures_b64") or []
            pending_signatures: list[bytes] = []
            for s in pending_sigs_b64:
                try:
                    pending_signatures.append(base64.b64decode(s))
                except Exception:
                    pass
            result.append(AssistantMessage(
                content=msg.get("content") or "",
                tool_calls=tool_calls,
                pending_signatures=pending_signatures,
            ))
        elif role == "tool":
            result.append(ToolResultMessage(
                call_id=msg.get("tool_call_id") or "",
                content=msg.get("content") or "",
            ))
    return result
