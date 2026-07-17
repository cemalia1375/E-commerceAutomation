"""ReactLoop — Agent 的核心执行引擎。

ReAct 模式
----------
  1. 使用当前消息历史调用 LLM
  2. 将文本 token 实时流式传输给调用方（前端）
  3. 收集 LLM 响应中的工具调用
  4. 执行与循环耦合的工具（needs_followup=True），将结果注入历史记录
  5. 将与循环解耦的工具（needs_followup=False）以后台任务方式触发 — 不注入结果
  6. 如果存在耦合工具调用 → 回到步骤 1
  7. 如果没有耦合工具调用 → yield DoneEvent 并停止

循环本身不关心所使用的 LLM 是什么，也不关心任何工具的内部实现。
它只通过 Messages、Chunks 和 Events 进行通信。
"""

from __future__ import annotations

import asyncio
import inspect
from typing import TYPE_CHECKING, Any, AsyncIterator, Awaitable, Callable

from loguru import logger

from simpleclaw.context.compressor import ContextCompressionEvent
from simpleclaw.core.events import DoneEvent, ErrorEvent, Event, TextEvent, ToolResultEvent
from simpleclaw.core.messages import AssistantMessage, ToolCall, ToolResultMessage, UserMessage
from simpleclaw.llm.base import LLMProvider
from simpleclaw.llm.chunks import TextChunk, ToolCallChunk
from simpleclaw.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from simpleclaw.context.builder import ContextBuilder
    from simpleclaw.context.compressor import ContextCompressor
    from simpleclaw.context.providers import AttentionPacket, ContextSection

_DEFAULT_MAX_ITERATIONS = 20
ContextCompressionCallback = Callable[[ContextCompressionEvent], Awaitable[None] | None]


class ReactLoop:
    """核心 ReAct 执行循环。"""

    def __init__(
        self,
        llm: LLMProvider,
        tool_registry: ToolRegistry,
        *,
        max_iterations: int = _DEFAULT_MAX_ITERATIONS,
        system_prompt: str | None = None,
        context_builder: "ContextBuilder | None" = None,
        compressor: "ContextCompressor | None" = None,
        on_context_compressed: ContextCompressionCallback | None = None,
    ) -> None:
        self.llm = llm
        self.tool_registry = tool_registry
        self.max_iterations = max_iterations
        self.system_prompt = system_prompt
        self.context_builder = context_builder
        self.compressor = compressor
        self.on_context_compressed = on_context_compressed
        self.messages: list = []   # list[Message]，宽松类型以避免循环导入
        # history_offset 是 messages[0] 对应的全局消息序号。
        # messages 只保留当前热态窗口；被裁剪掉的前缀由 history_offset 记录。
        self.history_offset: int = 0
        # consolidated_from 作为旧调用方兼容字段保留。若外部仍设置它，
        # ReactLoop 会在下一次模型调用前把该前缀真实裁剪到 history_offset。
        self.consolidated_from: int = 0
        # 每轮在 run() 中设置的状态 — 单线程异步下安全
        self._current_query: str = ""
        self._current_user_input: str = ""
        self._current_model_media: list[str] | None = None
        self._current_dynamic_context_sections: list["ContextSection"] | None = None
        self._current_attention_packets: list["AttentionPacket"] | None = None
        self._current_context_metadata: dict | None = None

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    async def run(
        self,
        user_input: str,
        *,
        query: str | None = None,
        media: list[str] | None = None,
        model_media: list[str] | None = None,
        persist_user_input: bool = True,
        dynamic_context_sections: list["ContextSection"] | None = None,
        attention_packets: list["AttentionPacket"] | None = None,
        context_metadata: dict | None = None,
    ) -> AsyncIterator[Event]:
        """执行一轮用户对话，持续 yield 事件直到 DoneEvent 或 ErrorEvent。

        Args:
            user_input:       本轮用户输入的消息。
            query:            本轮检索 query；不传时默认等于 user_input。
            media:            本轮用户真实上传的图片 URL / data URI，会持久化到消息历史。
            model_media:      仅本轮模型可见的图片列表；用于系统附加历史复核图。
                              若为 None，则默认等于 media。该字段不会写入持久化历史。
            persist_user_input: 是否把 user_input 追加到可持久化历史中。
            dynamic_context_sections:
                              本轮结构化动态上下文，供 ContextBuilder 渲染。
            attention_packets:      本轮结构化注意力包，供 ContextBuilder 渲染。
            context_metadata:       provider 可读的元数据，框架不解释业务含义。
        """
        if persist_user_input:
            self.messages.append(UserMessage(_build_user_content(user_input, media)))
        self._current_user_input = user_input
        self._current_query = query if query is not None else user_input
        self._current_model_media = list(model_media) if model_media is not None else None
        self._current_dynamic_context_sections = list(dynamic_context_sections or [])
        self._current_attention_packets = list(attention_packets or [])
        self._current_context_metadata = dict(context_metadata or {})
        try:
            async for event in self._react():
                yield event
        except Exception as exc:
            yield ErrorEvent(message=str(exc))

    @property
    def absolute_message_count(self) -> int:
        """Return the absolute sequence after the current in-memory window."""
        return self.history_offset + len(self.messages)

    def local_index_for_absolute(self, absolute_index: int) -> int:
        """Translate an absolute message index to the current local window."""
        return max(0, int(absolute_index or 0) - self.history_offset)

    def messages_since_absolute(self, absolute_index: int) -> list:
        """Return current in-memory messages whose absolute seq >= absolute_index."""
        return self.messages[self.local_index_for_absolute(absolute_index):]

    def normalize_message_window(self) -> None:
        """Apply any legacy consolidated_from pointer to the hot message window."""
        self._normalize_message_window()

    # ------------------------------------------------------------------
    # 内部 ReAct 循环
    # ------------------------------------------------------------------

    async def _get_messages_async(self) -> list[dict]:
        """构建发送给 LLM 的消息列表（纯函数，无副作用）。

        只发送当前热态消息窗口。
        """
        self._normalize_message_window()
        active = self.messages
        if self.context_builder is not None:
            result = await self.context_builder.build(
                active,
                dynamic_context_sections=self._current_dynamic_context_sections,
                attention_packets=self._current_attention_packets,
                metadata=self._current_context_metadata,
                query=self._current_query,
            )
        else:
            result = self._build_messages_from(active)

        # `model_media` is an ephemeral override for the current model call only.
        # It lets a caller attach a historical review image without serializing
        # that image into the durable UserMessage stored in self.messages.
        if self._current_model_media is not None:
            for msg in reversed(result):
                if msg.get("role") == "user":
                    msg["content"] = _build_user_content(
                        self._current_user_input,
                        self._current_model_media,
                    )
                    break
        return result

    async def _react(self) -> AsyncIterator[Event]:
        for iteration in range(self.max_iterations):
            text_buffer = ""
            tool_calls: list[ToolCall] = []
            # 本轮 text Part 上携带的 thought_signature（来自 TextChunk.thought_signature）
            # 不属于任何具体 tool_call，回放时作为前置 thought Parts 发给 Gemini
            text_signatures: list[bytes] = []

            await self._compress_before_llm_call(iteration)
            async for chunk in self.llm.stream_with_retry(
                await self._get_messages_async(),
                tools=self.tool_registry.schemas() or None,
            ):
                if isinstance(chunk, TextChunk):
                    if chunk.token:
                        yield TextEvent(chunk.token)      # 实时流出 → 前端看到
                    text_buffer += chunk.token
                    # 累积 text Part 上的 thought_signature（可能为 None）
                    if chunk.thought_signature is not None:
                        text_signatures.append(chunk.thought_signature)
                elif isinstance(chunk, ToolCallChunk):
                    tool_calls.append(ToolCall(
                        id=chunk.id,
                        name=chunk.name,
                        arguments=chunk.arguments,
                        thought_signature=chunk.thought_signature,
                    ))
            # --------------------------------------------------------------

            # 保存本次迭代的 assistant 消息，附带本轮 text Part 上的 signatures
            self.messages.append(AssistantMessage(
                text_buffer,
                tool_calls,
                pending_signatures=text_signatures,
            ))

            # 没有工具调用 → LLM 已完成，结束。
            if not tool_calls:
                yield DoneEvent()
                return

            # 按循环耦合性拆分工具调用。
            coupled = [c for c in tool_calls if self.tool_registry.needs_followup(c)]
            decoupled = [c for c in tool_calls if not self.tool_registry.needs_followup(c)]

            # 解耦工具：触发后不等待，不阻塞主循环。
            for call in decoupled:
                asyncio.create_task(
                    self._fire_decoupled(call),
                    name=f"decoupled-tool:{call.name}",
                )

            if not coupled:
                self.messages[-1] = AssistantMessage(text_buffer, [])
                yield DoneEvent()
                return

            # 并行执行耦合工具，等待全部结果返回。
            results = await asyncio.gather(*[
                self.tool_registry.execute(call) for call in coupled
            ])
            for call, result in zip(coupled, results):
                yield ToolResultEvent(tool_name=call.name, result=result.content)
                if result.persist_to_history:
                    self.messages.append(ToolResultMessage(
                        call_id=call.id,
                        content=result.content,
                    ))

            # 回到循环顶部 → 下次 LLM 调用时工具结果已在历史中。

        yield ErrorEvent(message=f"ReactLoop exceeded max_iterations={self.max_iterations}")

    async def _compress_before_llm_call(self, iteration: int) -> None:
        """Physically prune the hot message window before each LLM call."""
        self._normalize_message_window()
        if self.compressor is None:
            return

        result = await self.compressor.compress_window(self.messages)
        if not result.changed:
            return

        offset_before = self.history_offset
        self.messages = result.current
        self.history_offset += result.dropped_count
        self.consolidated_from = 0

        event = ContextCompressionEvent(
            result=result,
            history_offset_before=offset_before,
            history_offset_after=self.history_offset,
            iteration=iteration,
        )
        if self.on_context_compressed is not None:
            try:
                await _maybe_await(self.on_context_compressed(event))
            except Exception as exc:
                logger.warning("context compression callback failed: {}", exc)

    def _normalize_message_window(self) -> None:
        """Apply legacy consolidated_from by pruning the hidden local prefix."""
        if self.consolidated_from <= 0:
            return
        cut = min(self.consolidated_from, len(self.messages))
        if cut > 0:
            self.messages = self.messages[cut:]
            self.history_offset += cut
        self.consolidated_from = 0

    async def _fire_decoupled(self, call: ToolCall) -> None:
        """Run a fire-and-forget tool and make failures observable."""
        try:
            result = await self.tool_registry.execute(call)
        except Exception as exc:
            logger.exception("decoupled tool failed: tool={} err={}", call.name, exc)
            return
        if not result.ok:
            logger.warning(
                "decoupled tool returned not ok: tool={} result={}",
                call.name,
                result.content,
            )

    # ------------------------------------------------------------------
    # 消息组装
    # ------------------------------------------------------------------

    def _build_messages_from(self, messages: list) -> list[dict]:
        """将内部 Message 对象切片转换为 OpenAI dict 格式。"""
        result: list[dict] = []

        if self.system_prompt:
            result.append({"role": "system", "content": self.system_prompt})

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
                                "function": {
                                    "name": tc.name,
                                    "arguments": tc.arguments,
                                },
                                # Gemini-only：opaque bytes，对其它 provider 透明
                                "thought_signature": tc.thought_signature,
                            }
                            for tc in msg.tool_calls
                        ],
                        # text Part 上的 signatures，回放时转为前置 thought Parts
                        "pending_signatures": msg.pending_signatures,
                    })
                else:
                    result.append({
                        "role": "assistant",
                        "content": msg.content,
                        "pending_signatures": msg.pending_signatures,
                    })

            elif isinstance(msg, ToolResultMessage):
                result.append({
                    "role": "tool",
                    "tool_call_id": msg.call_id,
                    "content": msg.content,
                })

        return result


def _build_user_content(text: str, media: list[str] | None) -> str | list[dict]:
    """构造 OpenAI 风格的多模态 user content。"""
    refs = [ref.strip() for ref in (media or []) if isinstance(ref, str) and ref.strip()]
    if not refs:
        return text

    parts: list[dict] = []
    stripped = text.strip()
    parts.append({
        "type": "text",
        "text": stripped or "用户上传了一张图片，请结合当前上下文处理这张图片。",
    })
    for ref in refs:
        parts.append({"type": "image_url", "image_url": {"url": ref}})
    return parts


async def _maybe_await(value: Any) -> None:
    if inspect.isawaitable(value):
        await value
