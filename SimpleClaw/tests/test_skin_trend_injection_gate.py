"""趋势块注入门控（Option A）单元测试。

核心保证：memory_extract 不再每次压缩都注入皮肤趋势块，而是只在
"自上次已反映 profile_id 以来出现了更新的画像行"时注入；画像静态时跳过，
从而消除"同一组 severity 事实被反复重写"的冗余。

门控信号用 profile_id（单调整数，免时区），cursor 存在
nb_tenant_profile_block_meta 的 block_name='memory_skin_cursor' 行。
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime

from simpleclaw.core.messages import AssistantMessage, UserMessage
from simpleclaw.llm.chunks import TextChunk

from Mojing.agent.memory_extract import (
    _SKIN_MEMORY_CURSOR_BLOCK,
    _SKIN_TREND_SKIP_NOTE,
    _resolve_skin_injection,
    _run_extraction,
    load_compression_template,
)
from Mojing.agent.skin_trend import render_trend_facts, compute_trends


class _FakeSkinRepo:
    """最小皮肤画像 repo 桩：只实现门控用到的三个方法。"""

    def __init__(self, latest_id: int | None = None, cursor: int | None = None) -> None:
        self._latest_id = latest_id
        self._cursor = cursor
        self.upserts: list[int | None] = []

    async def get_latest(self, tenant_key: str) -> dict | None:
        return {"profile_id": self._latest_id} if self._latest_id is not None else None

    async def get_block_meta(self, tenant_key: str, block_name: str) -> dict | None:
        assert block_name == _SKIN_MEMORY_CURSOR_BLOCK
        return {"last_profile_id": self._cursor} if self._cursor is not None else None

    async def upsert_block_meta(self, *, tenant_key, block_name, last_writer, last_profile_id, content_hash) -> None:
        self.upserts.append(last_profile_id)


_TRENDS = [object()]   # 非空趋势（真值即可）
_NO_TRENDS: list = []


class TestSkinTrendInjectionGate(unittest.IsolatedAsyncioTestCase):
    async def test_no_repo_does_not_inject(self) -> None:
        inject, latest = await _resolve_skin_injection(None, "t1", _TRENDS)
        self.assertFalse(inject)
        self.assertIsNone(latest)

    async def test_empty_trends_does_not_inject(self) -> None:
        repo = _FakeSkinRepo(latest_id=10, cursor=None)
        inject, latest = await _resolve_skin_injection(repo, "t1", _NO_TRENDS)
        self.assertFalse(inject)

    async def test_no_profile_row_does_not_inject(self) -> None:
        repo = _FakeSkinRepo(latest_id=None, cursor=None)
        inject, latest = await _resolve_skin_injection(repo, "t1", _TRENDS)
        self.assertFalse(inject)
        self.assertIsNone(latest)

    async def test_bootstrap_no_cursor_injects(self) -> None:
        """首次：有画像但还没 cursor → 注入并带回 latest_id。"""
        repo = _FakeSkinRepo(latest_id=42, cursor=None)
        inject, latest = await _resolve_skin_injection(repo, "t1", _TRENDS)
        self.assertTrue(inject)
        self.assertEqual(latest, 42)

    async def test_no_new_profile_skips(self) -> None:
        """画像静态（latest == cursor）→ 跳过注入。"""
        repo = _FakeSkinRepo(latest_id=42, cursor=42)
        inject, latest = await _resolve_skin_injection(repo, "t1", _TRENDS)
        self.assertFalse(inject)
        self.assertEqual(latest, 42)

    async def test_older_or_equal_cursor_skips(self) -> None:
        repo = _FakeSkinRepo(latest_id=42, cursor=50)
        inject, _ = await _resolve_skin_injection(repo, "t1", _TRENDS)
        self.assertFalse(inject)

    async def test_new_profile_injects(self) -> None:
        """有更新画像（latest > cursor）→ 注入。"""
        repo = _FakeSkinRepo(latest_id=43, cursor=42)
        inject, latest = await _resolve_skin_injection(repo, "t1", _TRENDS)
        self.assertTrue(inject)
        self.assertEqual(latest, 43)


# ----------------------------------------------------------------------
# 端到端：经真实 _run_extraction 验证门控的注块/跳过/cursor 推进闭环
# ----------------------------------------------------------------------

class _FakeCursor:
    async def execute(self, *a, **k) -> None: ...
    async def fetchall(self) -> list: return []
    async def fetchone(self): return None
    async def __aenter__(self) -> "_FakeCursor": return self
    async def __aexit__(self, *e) -> None: ...


class _FakeConn:
    def cursor(self) -> _FakeCursor: return _FakeCursor()


class _FakeAcquire:
    async def __aenter__(self) -> _FakeConn: return _FakeConn()
    async def __aexit__(self, *e) -> None: ...


class _FakeDB:
    def acquire(self) -> _FakeAcquire: return _FakeAcquire()


class _CapturingLLM:
    """记录每次收到的 prompt，固定返回一条 skin create。"""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def stream_with_retry(self, messages, **kwargs):
        self.prompts.append(messages[0]["content"])
        payload = {
            "memory_actions": [{
                "action": "create",
                "topic": "皮肤问题",
                "memory_type": "skin",
                "description": "黑头追踪",
                "content": "鼻子黑头 6.6 轻度",
            }]
        }
        yield TextChunk(token=json.dumps(payload, ensure_ascii=False))


class _StatefulSkinRepo:
    """可变 latest_id + cursor 的皮肤画像 repo 桩，支撑 _run_extraction 全路径。"""

    def __init__(self) -> None:
        self.latest_id = 100
        self.cursor: int | None = None
        self._rows = [{
            "profile_id": 100,
            "created_at": datetime(2026, 6, 6, 12, 0, 0),
            "signals_json": [{"signalCode": "黑头", "locationText": "鼻子", "severity": "轻度"}],
        }]

    async def list_profiles_in_range(self, tenant_key, start, end) -> list:
        return list(self._rows)

    async def get_latest(self, tenant_key) -> dict:
        return {"profile_id": self.latest_id}

    async def get_block_meta(self, tenant_key, block_name) -> dict | None:
        return {"last_profile_id": self.cursor} if self.cursor is not None else None

    async def upsert_block_meta(self, *, tenant_key, block_name, last_writer, last_profile_id, content_hash) -> None:
        self.cursor = last_profile_id


_CHUNK = [
    UserMessage(content="我鼻子黑头最近反反复复，帮我看看变化"),
    AssistantMessage(content="好的，我们一起看。"),
]


class TestSkinTrendGateEndToEnd(unittest.IsolatedAsyncioTestCase):
    async def _extract(self, llm, repo):
        return await _run_extraction(
            llm=llm,
            db=_FakeDB(),
            template=load_compression_template(),
            tenant_key="t1",
            source="main",
            dropped_messages=_CHUNK,
            skin_profile_repo=repo,
        )

    async def test_gate_lifecycle_inject_skip_then_reinject(self) -> None:
        # 前置：确认这组 profile 行真能算出非空趋势（否则测的是空趋势，无意义）
        trend_header = render_trend_facts(compute_trends(_StatefulSkinRepo()._rows))
        self.assertIn("皮肤趋势事实", trend_header)

        llm = _CapturingLLM()
        repo = _StatefulSkinRepo()

        # 第 1 次：cursor 空 → 注块 + 推进 cursor
        out1 = await self._extract(llm, repo)
        self.assertTrue(out1.skin_trends_injected)
        self.assertIn("皮肤趋势事实由系统", llm.prompts[0])
        self.assertNotIn(_SKIN_TREND_SKIP_NOTE, llm.prompts[0])
        self.assertEqual(repo.cursor, 100)

        # 第 2 次：画像静态（latest==cursor）→ 跳过注块，注入 skip note，cursor 不动
        out2 = await self._extract(llm, repo)
        self.assertFalse(out2.skin_trends_injected)
        self.assertIn(_SKIN_TREND_SKIP_NOTE, llm.prompts[1])
        self.assertNotIn("皮肤趋势事实由系统", llm.prompts[1])
        self.assertEqual(repo.cursor, 100)

        # 第 3 次：来了新画像（latest→101）→ 恢复注块 + cursor 推进到 101
        repo.latest_id = 101
        out3 = await self._extract(llm, repo)
        self.assertTrue(out3.skin_trends_injected)
        self.assertIn("皮肤趋势事实由系统", llm.prompts[2])
        self.assertEqual(repo.cursor, 101)


if __name__ == "__main__":
    unittest.main()
