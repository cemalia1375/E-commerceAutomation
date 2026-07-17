"""Tests for reusable tool dedupe helpers."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta

from Mojing.runtime.tool_policies import time_window_dedupe


class _Repo:
    def __init__(self, latest):
        self.latest = latest

    async def find_latest_task_for(self, *, tenant_key: str, task_type: str):
        self.last_query = (tenant_key, task_type)
        return self.latest


class TimeWindowDedupeTest(unittest.IsolatedAsyncioTestCase):
    async def test_active_task_dedupes_with_runtime_status_source(self) -> None:
        latest = {
            "status": "wait_external",
            "created_at": (datetime.utcnow() - timedelta(minutes=3)).strftime("%Y-%m-%d %H:%M:%S"),
        }

        result = await time_window_dedupe(
            runtime_task_repo=_Repo(latest),  # type: ignore[arg-type]
            tenant_key="tenant-1",
            task_type="deep_research",
            dedupe_window_s=1800,
            estimated_total_min=10,
            in_progress_focus=lambda elapsed, remaining: f"wait {remaining}",
        )

        self.assertIsNotNone(result)
        payload = json.loads(result.content)  # type: ignore[union-attr]
        self.assertEqual(payload["action"], "deduped")
        self.assertEqual(payload["invocation_status"], "deduped")
        self.assertFalse(payload["runtime_task_created"])
        self.assertEqual(payload["reason"], "active_runtime_task")
        self.assertEqual(payload["phase"], "in_progress")
        self.assertEqual(payload["source"], "runtime_task_status")
        self.assertEqual(payload["runtime_task_status"], "wait_external")
        self.assertEqual(payload["estimated_remaining_minutes"], 7)
        self.assertIn("model_guidance", payload)

    async def test_active_task_after_window_does_not_dedupe(self) -> None:
        latest = {
            "status": "wait_external",
            "created_at": (datetime.utcnow() - timedelta(minutes=31)).strftime("%Y-%m-%d %H:%M:%S"),
        }

        result = await time_window_dedupe(
            runtime_task_repo=_Repo(latest),  # type: ignore[arg-type]
            tenant_key="tenant-1",
            task_type="deep_research",
            dedupe_window_s=1800,
            estimated_total_min=10,
            in_progress_focus=lambda elapsed, remaining: "wait",
        )

        self.assertIsNone(result)

    async def test_zero_window_disables_dedupe(self) -> None:
        latest = {
            "status": "running",
            "created_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        }

        result = await time_window_dedupe(
            runtime_task_repo=_Repo(latest),  # type: ignore[arg-type]
            tenant_key="tenant-1",
            task_type="deep_research",
            dedupe_window_s=0,
            estimated_total_min=10,
            in_progress_focus=lambda elapsed, remaining: "wait",
        )

        self.assertIsNone(result)

    async def test_succeeded_recent_task_does_not_dedupe(self) -> None:
        latest = {
            "status": "succeeded",
            "created_at": (datetime.utcnow() - timedelta(minutes=12)).strftime("%Y-%m-%d %H:%M:%S"),
        }

        result = await time_window_dedupe(
            runtime_task_repo=_Repo(latest),  # type: ignore[arg-type]
            tenant_key="tenant-1",
            task_type="deep_research",
            dedupe_window_s=1800,
            estimated_total_min=10,
            in_progress_focus=lambda elapsed, remaining: "wait",
        )

        self.assertIsNone(result)

    async def test_failed_or_expired_succeeded_does_not_dedupe(self) -> None:
        failed = {
            "status": "failed",
            "created_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        }
        expired = {
            "status": "succeeded",
            "created_at": (datetime.utcnow() - timedelta(minutes=40)).strftime("%Y-%m-%d %H:%M:%S"),
        }

        for latest in (failed, expired):
            result = await time_window_dedupe(
                runtime_task_repo=_Repo(latest),  # type: ignore[arg-type]
                tenant_key="tenant-1",
                task_type="deep_research",
                dedupe_window_s=1800,
                estimated_total_min=10,
                in_progress_focus=lambda elapsed, remaining: "wait",
            )
            self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
