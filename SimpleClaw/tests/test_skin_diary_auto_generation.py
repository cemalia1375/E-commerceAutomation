"""Tests for automatic skin diary generation windows and dispatch gates."""

from __future__ import annotations

from datetime import date, datetime
import sys
import types
import unittest


sys.modules.setdefault("loguru", types.SimpleNamespace(logger=types.SimpleNamespace(
    info=lambda *_, **__: None,
    debug=lambda *_, **__: None,
    warning=lambda *_, **__: None,
    error=lambda *_, **__: None,
)))
try:
    import httpx  # noqa: F401
except ModuleNotFoundError:
    sys.modules.setdefault("httpx", types.SimpleNamespace(
        AsyncClient=object,
        TimeoutException=TimeoutError,
    ))

from Mojing.runtime.executors import _maybe_enqueue_skin_diary_generation
from Mojing.utils.skin_diary_time import (
    DIARY_SLOT_EVENING,
    DIARY_SLOT_MIDDAY,
    DIARY_SLOT_MORNING,
    GENERATION_REASON_AUTO_EVENING,
    GENERATION_REASON_AUTO_MIDDAY_FALLBACK,
    GENERATION_REASON_AUTO_MORNING,
    resolve_skin_diary_generation_window,
)


class SkinDiaryWindowRulesTest(unittest.TestCase):
    def test_resolves_morning_window(self) -> None:
        resolved = resolve_skin_diary_generation_window(datetime(2026, 4, 28, 4, 0))

        self.assertTrue(resolved.should_consider)
        self.assertEqual(resolved.business_date, date(2026, 4, 28))
        self.assertEqual(resolved.diary_slot, DIARY_SLOT_MORNING)
        self.assertEqual(resolved.generation_reason, GENERATION_REASON_AUTO_MORNING)

    def test_resolves_midday_fallback_window(self) -> None:
        resolved = resolve_skin_diary_generation_window(datetime(2026, 4, 28, 17, 59))

        self.assertTrue(resolved.should_consider)
        self.assertEqual(resolved.business_date, date(2026, 4, 28))
        self.assertEqual(resolved.diary_slot, DIARY_SLOT_MIDDAY)
        self.assertEqual(resolved.generation_reason, GENERATION_REASON_AUTO_MIDDAY_FALLBACK)

    def test_resolves_evening_window_same_day(self) -> None:
        resolved = resolve_skin_diary_generation_window(datetime(2026, 4, 28, 18, 0))

        self.assertTrue(resolved.should_consider)
        self.assertEqual(resolved.business_date, date(2026, 4, 28))
        self.assertEqual(resolved.diary_slot, DIARY_SLOT_EVENING)
        self.assertEqual(resolved.generation_reason, GENERATION_REASON_AUTO_EVENING)

    def test_resolves_after_midnight_evening_to_previous_day(self) -> None:
        resolved = resolve_skin_diary_generation_window(datetime(2026, 4, 29, 2, 59))

        self.assertTrue(resolved.should_consider)
        self.assertEqual(resolved.business_date, date(2026, 4, 28))
        self.assertEqual(resolved.diary_slot, DIARY_SLOT_EVENING)

    def test_skips_three_oclock_dead_zone(self) -> None:
        resolved = resolve_skin_diary_generation_window(datetime(2026, 4, 29, 3, 30))

        self.assertFalse(resolved.should_consider)
        self.assertIsNone(resolved.business_date)
        self.assertIsNone(resolved.diary_slot)


class _TenantStateRepo:
    def __init__(self, *, stage: str = "explore") -> None:
        self.stage = stage

    async def get_journey(self, _: str) -> dict:
        return {"stage": self.stage, "milestones": {}}


class _SkinDiaryResultRepo:
    def __init__(self) -> None:
        self.business_dates: set[date] = set()
        self.slots: set[tuple[date, str]] = set()

    async def has_result_for_business_date(self, _: str, business_date: date) -> bool:
        return business_date in self.business_dates

    async def has_result_for_business_date_slot(
        self,
        _: str,
        business_date: date,
        diary_slot: str,
    ) -> bool:
        return (business_date, diary_slot) in self.slots


class _ActionUsageRepo:
    def __init__(self, *, submitted_count: int = 0) -> None:
        self.submitted_count = submitted_count

    async def get_counts(self, tenant_key: str, action_key: str) -> dict[str, int]:
        del tenant_key, action_key
        return {
            "submitted_count": self.submitted_count,
            "succeeded_count": 0,
            "failed_count": 0,
        }


class _Runtime:
    def __init__(self) -> None:
        self.tasks = []

    async def submit_task(self, task) -> str:
        self.tasks.append(task)
        return "queue-1"


class SkinDiaryAutoDispatchTest(unittest.IsolatedAsyncioTestCase):
    async def test_queues_first_generation_before_handoff(self) -> None:
        runtime = _Runtime()

        result = await _maybe_enqueue_skin_diary_generation(
            tenant_key="tenant-1",
            profile_id=1,
            source_task_id="image-task-1",
            tenant_state_repo=_TenantStateRepo(stage="novice"),
            action_usage_repo=_ActionUsageRepo(submitted_count=0),
            skin_diary_result_repo=_SkinDiaryResultRepo(),
            runtime=runtime,
            now=datetime(2026, 4, 28, 8, 0),
        )

        self.assertEqual(result, "queued:2026-04-28:morning")
        self.assertEqual(runtime.tasks[0].task_type, "skin_diary_generation")
        self.assertEqual(runtime.tasks[0].stream, "skin_diary")
        self.assertEqual(runtime.tasks[0].payload["action_key"], "skin_diary.handoff")
        self.assertNotIn("handoff_contract", runtime.tasks[0].payload)
        self.assertEqual(runtime.tasks[0].payload["source"], "skin_profile_sync")
        self.assertEqual(runtime.tasks[0].payload["source_task_id"], "image-task-1")
        self.assertEqual(runtime.tasks[0].payload["query"], "[系统通知] 用户刚完成了一次新的肌肤检测，皮肤画像已更新。")
        self.assertEqual(runtime.tasks[0].payload["generation_input"]["diary_date"], "2026-04-28")
        self.assertEqual(runtime.tasks[0].payload["generation_input"]["diary_slot"], "morning")

    async def test_skips_after_first_handoff(self) -> None:
        result = await _maybe_enqueue_skin_diary_generation(
            tenant_key="tenant-1",
            profile_id=1,
            tenant_state_repo=_TenantStateRepo(stage="novice"),
            action_usage_repo=_ActionUsageRepo(submitted_count=1),
            skin_diary_result_repo=_SkinDiaryResultRepo(),
            runtime=_Runtime(),
            now=datetime(2026, 4, 28, 8, 0),
        )

        self.assertEqual(result, "skipped:not_first_skin_diary")

    async def test_skips_outside_auto_window(self) -> None:
        result = await _maybe_enqueue_skin_diary_generation(
            tenant_key="tenant-1",
            profile_id=1,
            tenant_state_repo=_TenantStateRepo(),
            action_usage_repo=_ActionUsageRepo(),
            skin_diary_result_repo=_SkinDiaryResultRepo(),
            runtime=_Runtime(),
            now=datetime(2026, 4, 28, 3, 30),
        )

        self.assertEqual(result, "skipped:outside_auto_window")

    async def test_queues_morning_when_slot_has_no_result(self) -> None:
        runtime = _Runtime()

        result = await _maybe_enqueue_skin_diary_generation(
            tenant_key="tenant-1",
            profile_id=1,
            tenant_state_repo=_TenantStateRepo(),
            action_usage_repo=_ActionUsageRepo(),
            skin_diary_result_repo=_SkinDiaryResultRepo(),
            runtime=runtime,
            now=datetime(2026, 4, 28, 10, 59),
        )

        self.assertEqual(result, "queued:2026-04-28:morning")
        self.assertEqual(runtime.tasks[0].payload["diary_date"], "2026-04-28")
        self.assertEqual(runtime.tasks[0].payload["diary_slot"], DIARY_SLOT_MORNING)
        self.assertEqual(runtime.tasks[0].payload["generation_reason"], GENERATION_REASON_AUTO_MORNING)

    async def test_skips_morning_when_slot_already_exists(self) -> None:
        repo = _SkinDiaryResultRepo()
        repo.slots.add((date(2026, 4, 28), DIARY_SLOT_MORNING))

        result = await _maybe_enqueue_skin_diary_generation(
            tenant_key="tenant-1",
            profile_id=1,
            tenant_state_repo=_TenantStateRepo(),
            action_usage_repo=_ActionUsageRepo(),
            skin_diary_result_repo=repo,
            runtime=_Runtime(),
            now=datetime(2026, 4, 28, 8, 0),
        )

        self.assertEqual(result, "skipped:already_has_business_date_slot_result")

    async def test_midday_skips_when_business_day_has_any_result(self) -> None:
        repo = _SkinDiaryResultRepo()
        repo.business_dates.add(date(2026, 4, 28))

        result = await _maybe_enqueue_skin_diary_generation(
            tenant_key="tenant-1",
            profile_id=1,
            tenant_state_repo=_TenantStateRepo(),
            action_usage_repo=_ActionUsageRepo(),
            skin_diary_result_repo=repo,
            runtime=_Runtime(),
            now=datetime(2026, 4, 28, 11, 0),
        )

        self.assertEqual(result, "skipped:already_has_business_date_result")

    async def test_midday_queues_when_business_day_has_no_result(self) -> None:
        runtime = _Runtime()

        result = await _maybe_enqueue_skin_diary_generation(
            tenant_key="tenant-1",
            profile_id=1,
            tenant_state_repo=_TenantStateRepo(),
            action_usage_repo=_ActionUsageRepo(),
            skin_diary_result_repo=_SkinDiaryResultRepo(),
            runtime=runtime,
            now=datetime(2026, 4, 28, 12, 0),
        )

        self.assertEqual(result, "queued:2026-04-28:midday")
        self.assertEqual(runtime.tasks[0].payload["diary_slot"], DIARY_SLOT_MIDDAY)

    async def test_after_midnight_evening_queues_for_previous_business_date(self) -> None:
        runtime = _Runtime()

        result = await _maybe_enqueue_skin_diary_generation(
            tenant_key="tenant-1",
            profile_id=1,
            tenant_state_repo=_TenantStateRepo(),
            action_usage_repo=_ActionUsageRepo(),
            skin_diary_result_repo=_SkinDiaryResultRepo(),
            runtime=runtime,
            now=datetime(2026, 4, 29, 0, 30),
        )

        self.assertEqual(result, "queued:2026-04-28:evening")
        self.assertEqual(runtime.tasks[0].payload["diary_date"], "2026-04-28")
        self.assertEqual(runtime.tasks[0].payload["diary_slot"], DIARY_SLOT_EVENING)


if __name__ == "__main__":
    unittest.main()
