"""memory_extract executor 的失败可见性测试。

核心保证：LLM 输出无法解析为 JSON 时，executor 必须返回 failed，
而不是静默吞掉后标记 succeeded（曾导致压测中 0 写入却看不出原因）。
"""

from __future__ import annotations

import unittest

from simpleclaw.llm.chunks import TextChunk
from simpleclaw.runtime.task_protocol import TaskEnvelope

from Mojing.agent.memory_extract import make_memory_extract_executor
from Mojing.runtime.streams import MojingTaskStream


class _FakeCursor:
    async def execute(self, *args, **kwargs) -> None:
        pass

    async def fetchall(self) -> list:
        return []

    async def __aenter__(self) -> "_FakeCursor":
        return self

    async def __aexit__(self, *exc) -> None:
        pass


class _FakeConn:
    def cursor(self) -> _FakeCursor:
        return _FakeCursor()


class _FakeAcquire:
    async def __aenter__(self) -> _FakeConn:
        return _FakeConn()

    async def __aexit__(self, *exc) -> None:
        pass


class _FakeDB:
    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire()


class _FakeLLM:
    """stream_with_retry 返回固定文本的最小 LLM 桩。"""

    def __init__(self, raw: str) -> None:
        self._raw = raw

    async def stream_with_retry(self, messages, **kwargs):
        yield TextChunk(token=self._raw)


def _make_task() -> TaskEnvelope:
    return TaskEnvelope(
        task_type="memory_extract",
        payload={
            "tenant_key": "tenant-1",
            "source": "main",
            "dropped_messages": [{"role": "user", "content": "我额头长痘了"}],
        },
        stream=MojingTaskStream.MEMORY_EXTRACT,
        tenant_key="tenant-1",
        scope_key="memory_extract:tenant-1:main",
        service_role="mojing:memory-extract",
    )


class TestMemoryExtractExecutor(unittest.IsolatedAsyncioTestCase):
    async def test_json_parse_failure_returns_failed(self) -> None:
        executor = make_memory_extract_executor(
            llm=_FakeLLM("抱歉，这段对话没有值得记忆的内容。"),
            db=_FakeDB(),
        )
        result = await executor(_make_task())
        self.assertEqual(result.status, "failed")
        self.assertIn("parse failed", result.error or "")

    async def test_empty_json_returns_succeeded(self) -> None:
        """raw == "{}" 表示"无可记忆内容"，是合法结果，不算失败。"""
        executor = make_memory_extract_executor(
            llm=_FakeLLM("{}"),
            db=_FakeDB(),
        )
        result = await executor(_make_task())
        self.assertEqual(result.status, "succeeded")


if __name__ == "__main__":
    unittest.main()
