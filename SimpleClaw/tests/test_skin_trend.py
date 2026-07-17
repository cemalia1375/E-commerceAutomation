import unittest
from datetime import datetime

from Mojing.agent.skin_trend import (
    severity_to_level,
    compute_trends,
    render_trend_facts,
    render_timeline,
    _classify_direction,
)


def _profile(created_at, signals):
    return {
        "profile_id": 1,
        "created_at": created_at,
        "signals_json": signals,
    }


class TestSeverityToLevel(unittest.TestCase):
    def test_two_tiers(self) -> None:
        # 真实数据只有 轻度/重度；中度若偶现折叠为重度(2)
        self.assertEqual(severity_to_level({"severity": "轻度"}), 1)
        self.assertEqual(severity_to_level({"severity": "重度"}), 2)
        self.assertEqual(severity_to_level({"severity": "中度"}), 2)

    def test_null_severity_returns_none(self) -> None:
        # severity 缺失/为空 → None（单独跳过，绝不当作 0）
        self.assertIsNone(severity_to_level({"signalCode": "黑头"}))
        self.assertIsNone(severity_to_level({"severity": ""}))
        self.assertIsNone(severity_to_level({"severity": None}))

    def test_fallback_to_severity_level(self) -> None:
        # severityLevel 仅作历史兼容回退；0/缺失 → None
        self.assertIsNone(severity_to_level({"severityLevel": 0}))
        self.assertEqual(severity_to_level({"severityLevel": 1}), 1)
        self.assertEqual(severity_to_level({"severityLevel": 2}), 2)
        self.assertEqual(severity_to_level({"severityLevel": 3}), 2)


class TestComputeTrends(unittest.TestCase):
    def _worsen_then_improve(self):
        # 鼻翼黑头：轻 → 重 → 轻（先加重后改善）
        return [
            _profile(datetime(2026, 5, 20, 9), [
                {"signalCode": "黑头", "severity": "轻度", "locationText": "鼻翼"}]),
            _profile(datetime(2026, 5, 26, 9), [
                {"signalCode": "黑头", "severity": "重度", "locationText": "鼻翼"}]),
            _profile(datetime(2026, 6, 2, 9), [
                {"signalCode": "黑头", "severity": "轻度", "locationText": "鼻翼"}]),
        ]

    def test_direction_worsen_then_improve(self) -> None:
        trends = compute_trends(self._worsen_then_improve())
        t = next(t for t in trends if t.concern == "黑头" and t.region == "鼻翼")
        self.assertEqual(t.direction, "先加重后改善")
        self.assertEqual(t.window_start, datetime(2026, 5, 20).date())
        self.assertEqual(t.window_end, datetime(2026, 6, 2).date())
        self.assertEqual([p.level for p in t.points], [1, 2, 1])

    def test_direction_persistent_severe(self) -> None:
        rows = [
            _profile(datetime(2026, 5, 20, 9), [
                {"signalCode": "毛孔", "severity": "重度", "locationText": "T区"}]),
            _profile(datetime(2026, 6, 2, 9), [
                {"signalCode": "毛孔", "severity": "重度", "locationText": "T区"}]),
        ]
        trends = compute_trends(rows)
        t = next(t for t in trends if t.concern == "毛孔")
        self.assertEqual(t.direction, "持续重度")

    def test_direction_persistent_mild(self) -> None:
        rows = [
            _profile(datetime(2026, 5, 20, 9), [
                {"signalCode": "毛孔", "severity": "轻度", "locationText": "脸颊"}]),
            _profile(datetime(2026, 6, 2, 9), [
                {"signalCode": "毛孔", "severity": "轻度", "locationText": "脸颊"}]),
        ]
        trends = compute_trends(rows)
        t = next(t for t in trends if t.concern == "毛孔")
        self.assertEqual(t.direction, "持续轻度")

    def test_direction_worsening(self) -> None:
        # 轻 → 重 → 加重
        rows = [
            _profile(datetime(2026, 5, 1, 9), [
                {"signalCode": "丘疹", "severity": "轻度", "locationText": "脸颊"}]),
            _profile(datetime(2026, 5, 10, 9), [
                {"signalCode": "丘疹", "severity": "重度", "locationText": "脸颊"}]),
        ]
        trends = compute_trends(rows)
        t = next(t for t in trends if t.concern == "丘疹")
        self.assertEqual(t.direction, "加重")

    def test_direction_improving(self) -> None:
        # 重 → 轻 → 改善
        rows = [
            _profile(datetime(2026, 5, 1, 9), [
                {"signalCode": "丘疹", "severity": "重度", "locationText": "脸颊"}]),
            _profile(datetime(2026, 5, 10, 9), [
                {"signalCode": "丘疹", "severity": "轻度", "locationText": "脸颊"}]),
        ]
        trends = compute_trends(rows)
        t = next(t for t in trends if t.concern == "丘疹")
        self.assertEqual(t.direction, "改善")

    def test_direction_improve_then_relapse(self) -> None:
        # 重 → 轻 → 重 → 先改善后反复
        rows = [
            _profile(datetime(2026, 5, 1, 9), [
                {"signalCode": "脓疱", "severity": "重度", "locationText": "下巴"}]),
            _profile(datetime(2026, 5, 10, 9), [
                {"signalCode": "脓疱", "severity": "轻度", "locationText": "下巴"}]),
            _profile(datetime(2026, 5, 20, 9), [
                {"signalCode": "脓疱", "severity": "重度", "locationText": "下巴"}]),
        ]
        trends = compute_trends(rows)
        t = next(t for t in trends if t.concern == "脓疱" and t.region == "下巴")
        self.assertEqual([p.level for p in t.points], [2, 1, 2])
        self.assertEqual(t.direction, "先改善后反复")

    def test_coverage_counts_distinct_days_over_window(self) -> None:
        trends = compute_trends(self._worsen_then_improve())
        t = next(t for t in trends if t.concern == "黑头")
        # 窗口 5.20–6.2 共 14 天，3 个有效天 → coverage = 3/14
        self.assertAlmostEqual(t.coverage, 3 / 14, places=4)

    def test_null_severity_signal_skipped(self) -> None:
        # severity 缺失的信号不应产生趋势点；只有 6.2 的重度才有效
        rows = [
            _profile(datetime(2026, 5, 20, 9), [
                {"signalCode": "黑头", "locationText": "鼻翼"}]),  # 无 severity
            _profile(datetime(2026, 6, 2, 9), [
                {"signalCode": "黑头", "severity": "重度", "locationText": "鼻翼"}]),
        ]
        trends = compute_trends(rows)
        t = next((t for t in trends if t.concern == "黑头" and t.region == "鼻翼"), None)
        self.assertIsNotNone(t)
        self.assertEqual(len(t.points), 1)
        self.assertEqual(t.points[0].business_date, datetime(2026, 6, 2).date())
        self.assertEqual(t.direction, "仅一次记录")

    def test_single_point_direction(self) -> None:
        rows = [
            _profile(datetime(2026, 6, 1, 9), [
                {"signalCode": "粉刺", "severity": "重度", "locationText": "额头"}]),
        ]
        trends = compute_trends(rows)
        t = next(t for t in trends if t.concern == "粉刺" and t.region == "额头")
        self.assertEqual(t.direction, "仅一次记录")

    def test_multi_region_split_location_text(self) -> None:
        rows = [
            _profile(datetime(2026, 6, 1, 9), [
                {"signalCode": "黑头", "severity": "轻度", "locationText": "鼻翼·额头"}]),
        ]
        trends = compute_trends(rows)
        regions = {t.region for t in trends if t.concern == "黑头"}
        self.assertIn("鼻翼", regions)
        self.assertIn("额头", regions)

    def test_schema_b_code_and_regions_list(self) -> None:
        # 第二种 schema：code（非 signalCode）+ regions（列表，非 locationText）
        rows = [
            _profile(datetime(2026, 6, 1, 9), [
                {"code": "黑头", "severity": "轻度", "regions": ["鼻翼", "额头"]}]),
        ]
        trends = compute_trends(rows)
        regions = {t.region for t in trends if t.concern == "黑头"}
        self.assertIn("鼻翼", regions)
        self.assertIn("额头", regions)

    def test_per_concern_coverage(self) -> None:
        # A（黑头/鼻翼）：5.1, 5.3 共 3 天窗口，2 次观测 → 2/3
        # B（色斑/脸颊）：5.1, 5.10 共 10 天窗口，2 次观测 → 2/10
        rows = [
            _profile(datetime(2026, 5, 1, 9), [
                {"signalCode": "黑头", "severity": "轻度", "locationText": "鼻翼"},
                {"signalCode": "色斑", "severity": "轻度", "locationText": "脸颊"},
            ]),
            _profile(datetime(2026, 5, 3, 9), [
                {"signalCode": "黑头", "severity": "重度", "locationText": "鼻翼"},
            ]),
            _profile(datetime(2026, 5, 10, 9), [
                {"signalCode": "色斑", "severity": "重度", "locationText": "脸颊"},
            ]),
        ]
        trends = compute_trends(rows)
        t_a = next(t for t in trends if t.concern == "黑头" and t.region == "鼻翼")
        t_b = next(t for t in trends if t.concern == "色斑" and t.region == "脸颊")
        self.assertAlmostEqual(t_a.coverage, 2 / 3, places=4)
        self.assertAlmostEqual(t_b.coverage, 2 / 10, places=4)


class TestClassifyDirection(unittest.TestCase):
    def test_empty_no_record(self) -> None:
        self.assertEqual(_classify_direction([]), "无有效记录")

    def test_single_point(self) -> None:
        self.assertEqual(_classify_direction([2]), "仅一次记录")

    def test_persistent_mild(self) -> None:
        self.assertEqual(_classify_direction([1, 1, 1]), "持续轻度")

    def test_persistent_severe(self) -> None:
        self.assertEqual(_classify_direction([2, 2]), "持续重度")

    def test_worsen_then_improve(self) -> None:
        self.assertEqual(_classify_direction([1, 2, 1]), "先加重后改善")

    def test_worsening(self) -> None:
        self.assertEqual(_classify_direction([1, 2]), "加重")

    def test_improving(self) -> None:
        self.assertEqual(_classify_direction([2, 1]), "改善")

    def test_improve_then_relapse(self) -> None:
        self.assertEqual(_classify_direction([2, 1, 2]), "先改善后反复")


class TestRender(unittest.TestCase):
    def test_trend_facts_block_carries_numbers(self) -> None:
        rows = TestComputeTrends()._worsen_then_improve()
        block = render_trend_facts(compute_trends(rows))
        self.assertIn("黑头", block)
        self.assertIn("鼻翼", block)
        self.assertIn("先加重后改善", block)
        self.assertIn("5.20", block)

    def test_timeline_lists_each_day(self) -> None:
        rows = TestComputeTrends()._worsen_then_improve()
        timeline = render_timeline(compute_trends(rows))
        self.assertIn("5.20", timeline)
        self.assertIn("5.26", timeline)
        self.assertIn("6.2", timeline)


if __name__ == "__main__":
    unittest.main()
