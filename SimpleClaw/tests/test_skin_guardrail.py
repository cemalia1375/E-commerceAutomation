import unittest
from datetime import datetime

from Mojing.agent.skin_trend import compute_trends
from Mojing.agent.skin_guardrail import verify_skin_memory


def _worsen_then_improve_trends():
    # 方向 = 先加重后改善
    rows = [
        {"created_at": datetime(2026, 5, 20, 9), "signals_json": [
            {"signalCode": "黑头", "severity": "轻度", "locationText": "鼻翼"}]},
        {"created_at": datetime(2026, 5, 26, 9), "signals_json": [
            {"signalCode": "黑头", "severity": "重度", "locationText": "鼻翼"}]},
        {"created_at": datetime(2026, 6, 2, 9), "signals_json": [
            {"signalCode": "黑头", "severity": "轻度", "locationText": "鼻翼"}]},
    ]
    return compute_trends(rows)


def _persistent_mild_trends():
    # 方向 = 持续轻度
    rows = [
        {"created_at": datetime(2026, 5, 20, 9), "signals_json": [
            {"signalCode": "黑头", "severity": "轻度", "locationText": "鼻翼"}]},
        {"created_at": datetime(2026, 6, 2, 9), "signals_json": [
            {"signalCode": "黑头", "severity": "轻度", "locationText": "鼻翼"}]},
    ]
    return compute_trends(rows)


def _worsening_trends():
    # 方向 = 加重
    rows = [
        {"created_at": datetime(2026, 5, 20, 9), "signals_json": [
            {"signalCode": "黑头", "severity": "轻度", "locationText": "鼻翼"}]},
        {"created_at": datetime(2026, 6, 2, 9), "signals_json": [
            {"signalCode": "黑头", "severity": "重度", "locationText": "鼻翼"}]},
    ]
    return compute_trends(rows)


class TestVerifySkinMemory(unittest.TestCase):
    def test_matching_direction_passes(self) -> None:
        # 先加重后改善 既有加重又有改善，叙述含两词都不算矛盾
        result = verify_skin_memory(
            description="鼻翼黑头先加重后改善，与水杨酸洁面有关。",
            content="黑头（鼻翼）：\n  5.20 轻度\n  5.26 重度\n  6.2 轻度",
            trends=_worsen_then_improve_trends(),
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.violations, [])

    def test_worsening_claim_against_persistent_mild_rejected(self) -> None:
        # 实际持续轻度，却叙述"越来越严重/持续加重" → 矛盾
        result = verify_skin_memory(
            description="鼻翼黑头越来越严重，持续加重。",
            content="...",
            trends=_persistent_mild_trends(),
        )
        self.assertFalse(result.ok)
        # 同家族多个 token（越来越严重 / 持续加重）只记一次
        self.assertEqual(len(result.violations), 1)
        self.assertTrue(any("加重" in v for v in result.violations))
        self.assertIn("持续轻度", result.skeleton_description)
        self.assertTrue(result.skeleton_timeline)

    def test_improving_claim_against_worsening_rejected(self) -> None:
        # 实际加重，却叙述"好转/改善" → 矛盾
        result = verify_skin_memory(
            description="鼻翼黑头明显好转，已经改善。",
            content="...",
            trends=_worsening_trends(),
        )
        self.assertFalse(result.ok)
        self.assertTrue(any("改善" in v for v in result.violations))

    def test_no_trends_passes_noop(self) -> None:
        result = verify_skin_memory(description="任意", content="任意", trends=[])
        self.assertTrue(result.ok)


if __name__ == "__main__":
    unittest.main()
