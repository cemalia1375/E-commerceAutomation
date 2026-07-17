"""Tests for context token estimation."""

from __future__ import annotations

import unittest

from simpleclaw.context.compressor import ContextCompressor, estimate_content_tokens
from simpleclaw.core.events import DoneEvent
from simpleclaw.core.loop import ReactLoop
from simpleclaw.core.messages import AssistantMessage, UserMessage
from simpleclaw.context.compressor import _estimate_tokens
from simpleclaw.llm.chunks import TextChunk
from simpleclaw.tools.registry import ToolRegistry


class _TextOnlyLLM:
    def __init__(self) -> None:
        self.seen_messages: list[list[dict]] = []

    async def stream_with_retry(self, messages, tools=None, **_):
        self.seen_messages.append(messages)
        yield TextChunk("ok")


class ContextCompressorEstimateTest(unittest.IsolatedAsyncioTestCase):
    def test_chinese_estimate_is_cjk_aware(self) -> None:
        text = "这是一个中文上下文估算测试" * 10

        self.assertGreater(estimate_content_tokens(text), len(text) // 2)

    def test_multimodal_content_counts_text_and_url(self) -> None:
        content = [
            {"type": "text", "text": "帮我看看这张脸最近状态"},
            {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
        ]

        self.assertGreater(estimate_content_tokens(content), 1)

    def test_message_estimator_handles_multimodal_user_message(self) -> None:
        msg = UserMessage(content=[
            {"type": "text", "text": "这张照片看起来怎么样"},
            {"type": "image_url", "image_url": {"url": "https://example.com/a.png"}},
        ])

        self.assertGreater(_estimate_tokens([msg]), 1)

    async def test_compress_window_returns_dropped_and_current_messages(self) -> None:
        messages = [
            UserMessage("旧问题" * 20),
            AssistantMessage("旧回复" * 20),
            UserMessage("新问题" * 20),
            AssistantMessage("新回复" * 20),
        ]
        compressor = ContextCompressor(
            max_tokens=20,
            target_tokens=15,
            min_keep_tokens=5,
        )

        result = await compressor.compress_window(messages)

        self.assertTrue(result.changed)
        self.assertEqual(result.dropped, messages[:2])
        self.assertEqual(result.current, messages[2:])
        self.assertEqual(result.dropped_count, 2)
        self.assertGreater(result.tokens_before, result.tokens_after)

    async def test_maybe_compress_keeps_backward_compatible_return_shape(self) -> None:
        messages = [
            UserMessage("旧问题" * 20),
            AssistantMessage("旧回复" * 20),
            UserMessage("新问题" * 20),
            AssistantMessage("新回复" * 20),
        ]
        compressor = ContextCompressor(
            max_tokens=20,
            target_tokens=15,
            min_keep_tokens=5,
        )

        current = await compressor.maybe_compress(messages)

        self.assertEqual(current, messages[2:])

    async def test_react_loop_prunes_before_llm_call(self) -> None:
        llm = _TextOnlyLLM()
        events = []

        async def _on_compressed(event) -> None:
            events.append(event)

        loop = ReactLoop(
            llm=llm,  # type: ignore[arg-type]
            tool_registry=ToolRegistry(),
            compressor=ContextCompressor(
                max_tokens=30,
                target_tokens=20,
                min_keep_tokens=5,
            ),
            on_context_compressed=_on_compressed,
        )
        loop.messages = [
            UserMessage("旧问题" * 20),
            AssistantMessage("旧回复" * 20),
            UserMessage("近期问题" * 2),
            AssistantMessage("近期回复" * 2),
        ]

        output_events = [event async for event in loop.run("当前问题")]

        self.assertTrue(any(isinstance(event, DoneEvent) for event in output_events))
        self.assertEqual(loop.history_offset, 2)
        self.assertEqual(events[0].dropped_count, 2)
        self.assertNotIn("旧问题", str(llm.seen_messages[0]))


if __name__ == "__main__":
    unittest.main()
