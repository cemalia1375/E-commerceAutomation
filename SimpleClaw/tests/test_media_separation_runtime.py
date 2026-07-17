"""Regression tests for model-only review media separation."""

from __future__ import annotations

import sys
import types
import unittest

try:
    import loguru  # noqa: F401
except ModuleNotFoundError:
    sys.modules.setdefault("loguru", types.SimpleNamespace(logger=types.SimpleNamespace(
        info=lambda *_, **__: None,
        debug=lambda *_, **__: None,
        warning=lambda *_, **__: None,
        error=lambda *_, **__: None,
    )))

from simpleclaw.core.events import DoneEvent
from simpleclaw.core.loop import ReactLoop
from simpleclaw.llm.chunks import TextChunk
from simpleclaw.tools.registry import ToolRegistry


class _RecordingLLM:
    def __init__(self) -> None:
        self.calls: list[list[dict]] = []

    async def stream_with_retry(self, messages, **_):
        self.calls.append(messages)
        yield TextChunk("ok")


class MediaSeparationRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def test_model_media_is_visible_but_not_persisted(self) -> None:
        llm = _RecordingLLM()
        loop = ReactLoop(llm=llm, tool_registry=ToolRegistry())

        events = [event async for event in loop.run(
            "帮我复核原图",
            media=[],
            model_media=["history-image-url"],
        )]

        self.assertTrue(any(isinstance(event, DoneEvent) for event in events))
        self.assertEqual(loop.messages[0].content, "帮我复核原图")
        sent_user = [m for m in llm.calls[0] if m["role"] == "user"][-1]
        self.assertIsInstance(sent_user["content"], list)
        self.assertEqual(sent_user["content"][1]["image_url"]["url"], "history-image-url")


if __name__ == "__main__":
    unittest.main()
