import types
import unittest
from datetime import datetime

from Mojing.agent import memory_extract as me
from Mojing.agent.skin_trend import compute_trends


class _Profile:
    def __init__(self, rows):
        self._rows = rows

    async def list_profiles_in_range(self, tenant_key, start, end):
        return self._rows


class _StubMemory:
    def __init__(self):
        self.stored = []  # [(key, content, description, memory_type)]
        self._items = []

    async def retrieve(self, top_k=20):
        return list(self._items)

    async def store(self, key, content, *, description="", metadata=None, memory_type="chitchat"):
        self.stored.append((key, content, description, memory_type))

    async def delete(self, key):
        pass


class TestExtractTrendInjection(unittest.IsolatedAsyncioTestCase):
    async def test_trend_facts_rendered_into_prompt(self) -> None:
        rows = [
            {"created_at": datetime(2026, 5, 20, 9), "signals_json": [
                {"signalCode": "黑头", "severity": "轻度", "locationText": "鼻翼"}]},
            {"created_at": datetime(2026, 6, 2, 9), "signals_json": [
                {"signalCode": "黑头", "severity": "重度", "locationText": "鼻翼"}]},
        ]
        block = me._build_skin_trend_block(_Profile(rows), tenant_key="t1")
        self.assertIn("黑头", await block)

    async def test_skin_action_persists_memory_type(self) -> None:
        mem = _StubMemory()
        action = {
            "action": "create",
            "topic": "皮肤问题",
            "memory_type": "skin",
            "description": "鼻翼黑头先加重后改善",
            "content": "黑头（鼻翼）：\n  5.20 轻度\n  6.2 重度",
        }
        await me._execute_memory_action(action, [], mem, skin_trends=[])
        self.assertEqual(mem.stored[-1][3], "skin")  # memory_type 落库


def _trends():
    # 全程轻度 → 方向 = 持续轻度；叙述"持续加重/越来越严重"即与之明显矛盾
    rows = [
        {"created_at": datetime(2026, 5, 20, 9), "signals_json": [
            {"signalCode": "黑头", "severity": "轻度", "locationText": "鼻翼"}]},
        {"created_at": datetime(2026, 5, 26, 9), "signals_json": [
            {"signalCode": "黑头", "severity": "轻度", "locationText": "鼻翼"}]},
        {"created_at": datetime(2026, 6, 2, 9), "signals_json": [
            {"signalCode": "黑头", "severity": "轻度", "locationText": "鼻翼"}]},
    ]
    return compute_trends(rows)  # 方向 = 持续轻度


class TestSkinGuardrailHardening(unittest.IsolatedAsyncioTestCase):
    async def test_update_rejection_persists_skeleton(self) -> None:
        """guardrail 拒绝 skin update 时，落库内容应为代码骨架，而非伪造描述。"""
        mem = _StubMemory()
        stub_item = types.SimpleNamespace(key="皮肤问题", content="", description="")
        action = {
            "action": "update",
            "topic_id": 1,
            "memory_type": "skin",
            "description": "鼻翼黑头持续加重",  # 与趋势矛盾
            "content": "bogus content",
        }
        record = await me._execute_memory_action(
            action, [stub_item], mem, skin_trends=_trends()
        )
        self.assertEqual(record["status"], "guardrail_rejected")
        # 落库内容是骨架（含日期标记），不是伪造内容
        stored_content = mem.stored[-1][1]
        self.assertNotEqual(stored_content, "bogus content")
        # 骨架应包含时间线中的某个日期
        self.assertTrue(
            any(marker in stored_content for marker in ("5.20", "5.26", "6.2", "轻度", "重度")),
            f"骨架内容不含预期时间线标记: {stored_content!r}",
        )

    async def test_skin_append_contradiction_not_persisted(self) -> None:
        """skin append 含矛盾 new_fact 时，guardrail 拒绝后不应写入任何内容。"""
        mem = _StubMemory()
        stub_item = types.SimpleNamespace(key="皮肤问题", content="", description="")
        action = {
            "action": "append",
            "topic_id": 1,
            "memory_type": "skin",
            "new_fact": "鼻翼黑头持续加重",  # 与趋势矛盾
        }
        record = await me._execute_memory_action(
            action, [stub_item], mem, skin_trends=_trends()
        )
        self.assertEqual(record["status"], "guardrail_rejected")
        self.assertEqual(len(mem.stored), 0, "矛盾 skin append 不应落库")

    async def test_non_contradicting_skin_update_passes(self) -> None:
        """与趋势一致的 skin update 应正常落库，memory_type='skin'。"""
        mem = _StubMemory()
        stub_item = types.SimpleNamespace(key="皮肤问题", content="", description="")
        action = {
            "action": "update",
            "topic_id": 1,
            "memory_type": "skin",
            "description": "鼻翼黑头一直维持轻度，状态平稳",  # 与"持续轻度"一致，无矛盾词
            "content": "黑头（鼻翼）：\n 5.20 轻度",
        }
        record = await me._execute_memory_action(
            action, [stub_item], mem, skin_trends=_trends()
        )
        self.assertEqual(record["status"], "applied")
        self.assertEqual(mem.stored[-1][3], "skin")


_SENTINEL_TRENDS = [object()]  # 非空即可，_summarize_guardrail 只判断 truthiness


class TestSummarizeGuardrail(unittest.TestCase):
    def test_all_applied_accept(self) -> None:
        """所有 skin action 通过护栏 → verdict accept, checked_lines = 数量。"""
        actions = [
            {"memory_type": "skin", "status": "applied", "topic": "黑头"},
            {"memory_type": "skin", "status": "applied", "topic": "痘印"},
        ]
        result = me._summarize_guardrail(actions, _SENTINEL_TRENDS)
        self.assertEqual(result["verdict"], "accept")
        self.assertEqual(result["rejected"], [])
        self.assertEqual(result["checked_lines"], 2)

    def test_backfill_verdict(self) -> None:
        """create/update 被护栏拒绝（guardrail_outcome=backfill） → verdict backfill, rejected 含'回填'。"""
        actions = [
            {
                "memory_type": "skin",
                "status": "guardrail_rejected",
                "guardrail_outcome": "backfill",
                "topic": "黑头",
                "violations": ["数字矛盾"],
            }
        ]
        result = me._summarize_guardrail(actions, _SENTINEL_TRENDS)
        self.assertEqual(result["verdict"], "backfill")
        self.assertEqual(len(result["rejected"]), 1)
        self.assertIn("回填", result["rejected"][0])

    def test_reject_line_verdict(self) -> None:
        """append 被护栏拒绝（guardrail_outcome=reject_line） → verdict reject_line, rejected 含'丢弃'。"""
        actions = [
            {
                "memory_type": "skin",
                "status": "guardrail_rejected",
                "guardrail_outcome": "reject_line",
                "topic": "痘印",
                "violations": ["与趋势矛盾"],
            }
        ]
        result = me._summarize_guardrail(actions, _SENTINEL_TRENDS)
        self.assertEqual(result["verdict"], "reject_line")
        self.assertEqual(len(result["rejected"]), 1)
        self.assertIn("丢弃", result["rejected"][0])

    def test_no_trends_checked_lines_zero(self) -> None:
        """skin_trends 为空/None → checked_lines == 0，即使有 skin actions。"""
        actions = [{"memory_type": "skin", "status": "applied", "topic": "黑头"}]
        for trends in (None, []):
            result = me._summarize_guardrail(actions, trends)
            self.assertEqual(result["checked_lines"], 0)
            self.assertEqual(result["verdict"], "accept")

    def test_non_skin_actions_ignored(self) -> None:
        """非 skin 类型（如 chitchat）不计入 checked_lines 和 rejected。"""
        actions = [
            {"memory_type": "chitchat", "status": "guardrail_rejected", "topic": "闲聊"},
            {"memory_type": "skin", "status": "applied", "topic": "黑头"},
        ]
        result = me._summarize_guardrail(actions, _SENTINEL_TRENDS)
        self.assertEqual(result["checked_lines"], 1)
        self.assertEqual(result["rejected"], [])
        self.assertEqual(result["verdict"], "accept")

    def test_outcome_default_guardrail(self) -> None:
        """MemoryExtractionOutcome 默认 guardrail 字段应是合法的 noop 结构。"""
        from simpleclaw.memory.ledger import MemorySnapshot
        outcome = me.MemoryExtractionOutcome(
            memory_before=MemorySnapshot(items=[], metadata={}),
            memory_actions=[],
            memory_after=MemorySnapshot(items=[], metadata={}),
            business_snapshot={},
        )
        g = outcome.guardrail
        self.assertEqual(g["verdict"], "accept")
        self.assertEqual(g["rejected"], [])
        self.assertEqual(g["checked_lines"], 0)


if __name__ == "__main__":
    unittest.main()
