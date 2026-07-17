"""/admin/lab 回填的日期映射纯函数测试。"""
import unittest
from datetime import date, timedelta

from admin.lab.backfill import backfill_dates, job_timestamp, profile_timestamp
from Mojing.agent.skin_trend import business_date_of


class BackfillDatesTest(unittest.TestCase):
    def test_single_photo_maps_to_today(self) -> None:
        today = date(2026, 6, 10)
        self.assertEqual(backfill_dates(1, today=today), [today])

    def test_three_photos_cover_last_three_days_ascending(self) -> None:
        today = date(2026, 6, 10)
        self.assertEqual(
            backfill_dates(3, today=today),
            [date(2026, 6, 8), date(2026, 6, 9), today],
        )

    def test_sixty_photos_are_consecutive_days(self) -> None:
        today = date(2026, 6, 10)
        days = backfill_dates(60, today=today)
        self.assertEqual(len(days), 60)
        self.assertEqual(days[-1], today)
        for earlier, later in zip(days, days[1:]):
            self.assertEqual(later - earlier, timedelta(days=1))

    def test_non_positive_count_rejected(self) -> None:
        with self.assertRaises(ValueError):
            backfill_dates(0)


class TimestampConventionTest(unittest.TestCase):
    """两表时区约定不同：job 与 utcnow 比（UTC naive），profile 被
    business_date_of 当北京时间解释。两个时间戳必须是同一瞬间的两种表示。"""

    def test_profile_timestamp_round_trips_business_date(self) -> None:
        day = date(2026, 6, 8)
        self.assertEqual(business_date_of(profile_timestamp(day)), day)

    def test_job_and_profile_timestamps_are_same_instant(self) -> None:
        day = date(2026, 6, 8)
        # 北京 = UTC+8：北京 12:00 == UTC 04:00
        self.assertEqual(profile_timestamp(day) - job_timestamp(day), timedelta(hours=8))

    def test_profile_timestamp_far_from_4am_boundary(self) -> None:
        day = date(2026, 6, 8)
        self.assertEqual(profile_timestamp(day).hour, 12)
