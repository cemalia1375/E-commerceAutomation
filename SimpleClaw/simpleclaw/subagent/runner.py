"""SubagentRunner — 子 Agent 的无状态执行器。

职责：
  接收预加载的消息历史 + ContextBuilder + ToolRegistry，
  运行一轮 ReactLoop，返回（更新后的消息历史，回复文本）。

不负责：
  - DB 持久化（由调用方 SubagentStore 负责）
  - 会话生命周期管理
  - 路由判断

设计原则：
  保持无状态。SubagentStore（Mojing 层）持有 LLM 并包装持久化逻辑；
  SubagentRunner 只做"一次 ReactLoop 执行"这一件事。
"""

from __future__ import annotations

from typing import Awaitable, Callable

from loguru import logger

from simpleclaw.context.builder import ContextBuilder
from simpleclaw.context.providers import AttentionPacket, ContextSection
from simpleclaw.core.events import DoneEvent, ErrorEvent, TextEvent
from simpleclaw.core.loop import ReactLoop
from simpleclaw.core.messages import Message
from simpleclaw.llm.base import LLMProvider
from simpleclaw.tools.registry import ToolRegistry


class SubagentRunner:
    """执行单轮子 Agent 对话，不管理状态，不做持久化。"""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def run_turn(
        self,
        message: str,
        history: list[Message],
        context_builder: ContextBuilder,
        tool_registry: ToolRegistry,
        *,
        dynamic_context_sections: list[ContextSection] | None = None,
        media: list[str] | None = None,
        model_media: list[str] | None = None,
        persist_user_input: bool = True,
        on_token: Callable[[str], Awaitable[None]] | None = None,
        consolidated_from: int = 0,
        history_offset: int = 0,
        attention_packets: list[AttentionPacket] | None = None,
        context_metadata: dict | None = None,
        on_prompt_messages: Callable[[list[dict]], None] | None = None,
        on_attention_packets: Callable[[list[AttentionPacket]], None] | None = None,
    ) -> tuple[list[Message], str]:
        """执行一轮子 Agent 对话。

        Args:
            message:          本轮用户输入。
            history:          从 DB 预加载的 Message 对象列表（调用方反序列化）。
            context_builder:  子 Agent 专用的 ContextBuilder 实例。
            tool_registry:    子 Agent 专用的 ToolRegistry 实例。
            dynamic_context_sections:
                              本轮结构化动态上下文。
            media:            本轮用户真实上传的图片 URL / data URI，会持久化到历史。
            model_media:      仅本轮模型可见的图片列表，可包含系统附加的历史复核图。
            on_token:         每个文本 token 的流式回调（可选，用于直连 SSE 推送）。
            consolidated_from: 兼容旧调用方的本地工作窗口起始指针。
            history_offset:    当前 history[0] 对应的全局消息序号。
            attention_packets:
                              本轮结构化注意力包。
            context_metadata:
                              provider 可读的元数据，SimpleClaw 不解释业务含义。
            on_prompt_messages:
                              可选调试观察回调。每次调用模型前，收到最终
                              OpenAI-compatible messages；不影响执行路径。
            on_attention_packets:
                              可选调试观察回调。每次 ContextBuilder 完成
                              packet 生命周期过滤后，收到本次实际注入的
                              AttentionPacket 列表。

        Returns:
            (updated_history, reply_text)
            updated_history 包含本轮新增的 UserMessage + AssistantMessage。
            调用方负责将新增部分持久化到 DB。
        """
        loop = ReactLoop(
            llm=self._llm,
            tool_registry=tool_registry,
            context_builder=context_builder,
        )
        loop.messages = list(history)
        loop.history_offset = history_offset
        loop.consolidated_from = consolidated_from
        restore_attention_capture = lambda: None
        if on_attention_packets is not None and hasattr(context_builder, "_collect_attention_packets"):
            original_collect_attention = context_builder._collect_attention_packets

            async def _observed_collect_attention_packets(ctx, *, attention_packets):
                packets = await original_collect_attention(ctx, attention_packets=attention_packets)
                on_attention_packets(packets)
                return packets

            context_builder._collect_attention_packets = _observed_collect_attention_packets

            def restore_attention_capture() -> None:
                context_builder._collect_attention_packets = original_collect_attention

        if on_prompt_messages is not None:
            original_get_messages = loop._get_messages_async

            async def _observed_get_messages_async() -> list[dict]:
                messages = await original_get_messages()
                on_prompt_messages(messages)
                return messages

            loop._get_messages_async = _observed_get_messages_async

        reply_parts: list[str] = []

        try:
            async for event in loop.run(
                message,
                persist_user_input=persist_user_input,
                dynamic_context_sections=dynamic_context_sections,
                media=media,
                model_media=model_media,
                attention_packets=attention_packets,
                context_metadata=context_metadata,
            ):
                if isinstance(event, TextEvent):
                    reply_parts.append(event.token)
                    if on_token is not None:
                        await on_token(event.token)
                elif isinstance(event, ErrorEvent):
                    logger.warning("SubagentRunner error event: {}", event.message)
        except Exception as exc:
            logger.error("SubagentRunner unexpected error: {}", exc)
        finally:
            restore_attention_capture()

        return loop.messages, "".join(reply_parts)
