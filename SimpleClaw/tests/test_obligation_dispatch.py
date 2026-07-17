from __future__ import annotations

import unittest

from Mojing.runtime.obligations import (
    ACTION_GENERATE_DEEP_REPORT,
    ACTION_GENERATE_SKIN_DIARY,
    DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
    dispatch_obligations_for_dependency,
)
from Mojing.runtime.task_types import MojingTaskType


class _ObligationRepo:
    def __init__(self, pending: list[dict] | None = None) -> None:
        self.pending = pending or []
        self.dispatched: list[tuple[str, str]] = []
        self.reverted: list[tuple[str, str]] = []
        self.cancelled: list[tuple[str, str, str]] = []
        self.cancelled_obligations: list[str] = []

    async def list_pending_for_dependency(self, *, tenant_key: str, dependency_type: str, action_type: str | None = None, limit: int = 20):
        del limit
        return [
            item for item in self.pending
            if item.get("tenant_key") == tenant_key
            and item.get("dependency_type") == dependency_type
            and (action_type is None or item.get("action_type") == action_type)
        ]

    async def mark_dispatched_if_pending(self, *, obligation_id: str, dispatched_task_id: str) -> bool:
        self.dispatched.append((obligation_id, dispatched_task_id))
        return True

    async def revert_dispatched_to_pending(self, *, obligation_id: str, dispatched_task_id: str) -> bool:
        self.reverted.append((obligation_id, dispatched_task_id))
        return True

    async def cancel_pending(self, *, tenant_key: str, session_key: str | None = None, action_type: str | None = None) -> int:
        self.cancelled.append((tenant_key, session_key or "", action_type or ""))
        return 0

    async def cancel_pending_obligation(self, *, obligation_id: str) -> bool:
        self.cancelled_obligations.append(obligation_id)
        return True


class _Runtime:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.submitted = []

    async def submit_task(self, task, *, summary: str | None = None):
        if self.fail:
            raise RuntimeError("queue down")
        self.submitted.append((task, summary))
        return "queue-1"


class ObligationDispatchTest(unittest.IsolatedAsyncioTestCase):
    async def test_dispatches_skin_diary_generation_after_image_analysis(self) -> None:
        repo = _ObligationRepo([
            {
                "obligation_id": "obl-1",
                "tenant_key": "tenant-1",
                "session_key": "main:tenant-1",
                "action_type": ACTION_GENERATE_SKIN_DIARY,
                "dependency_type": DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
                "payload": {
                    "generation_input": {
                        "evidence": "用户要求分析完成后同步今日护肤计划",
                    }
                },
                "evidence": {"user_request": "分析完也同步今日护肤计划"},
            }
        ])
        runtime = _Runtime()

        dispatched = await dispatch_obligations_for_dependency(
            obligation_repo=repo,
            runtime=runtime,
            tenant_key="tenant-1",
            dependency_type=DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
            source_session_key="main:tenant-1",
            profile_id=123,
            source_task_id="image-task-1",
        )

        self.assertEqual(len(dispatched), 1)
        self.assertEqual(repo.dispatched, [("obl-1", "obl_obl-1")])
        task, summary = runtime.submitted[0]
        self.assertEqual(task.task_type, MojingTaskType.SKIN_DIARY_GENERATION)
        self.assertEqual(task.stream, "skin_diary")
        self.assertEqual(task.tenant_key, "tenant-1")
        self.assertEqual(task.session_key, "skin_diary:tenant-1")
        self.assertEqual(task.payload["source"], "obligation")
        self.assertEqual(task.payload["profile_id"], 123)
        self.assertEqual(task.payload["source_task_id"], "image-task-1")
        self.assertEqual(summary, "obligation dispatched skin diary generation")

    async def test_dispatches_deep_report_after_image_analysis(self) -> None:
        repo = _ObligationRepo([
            {
                "obligation_id": "obl-deep-1",
                "tenant_key": "tenant-1",
                "session_key": "main:tenant-1",
                "action_type": ACTION_GENERATE_DEEP_REPORT,
                "dependency_type": DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
                "payload": {
                    "user_query": "用户要求图片分析完成后生成深度分析报告",
                },
                "evidence": {"user_request": "分析完也帮我生成深度分析报告"},
            }
        ])
        runtime = _Runtime()

        dispatched = await dispatch_obligations_for_dependency(
            obligation_repo=repo,
            runtime=runtime,
            tenant_key="tenant-1",
            dependency_type=DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
            source_session_key="main:tenant-1",
            profile_id=123,
            source_task_id="image-task-1",
        )

        self.assertEqual(len(dispatched), 1)
        self.assertEqual(repo.dispatched, [("obl-deep-1", "obl_obl-deep-1")])
        task, summary = runtime.submitted[0]
        self.assertEqual(task.task_type, MojingTaskType.DEEP_RESEARCH)
        self.assertEqual(task.stream, "deep_research")
        self.assertEqual(task.tenant_key, "tenant-1")
        self.assertEqual(task.session_key, "deep_report:tenant-1")
        self.assertEqual(task.payload["source"], "obligation")
        self.assertEqual(task.payload["action_key"], "deep_report.handoff")
        self.assertEqual(task.payload["user_id"], "tenant-1")
        self.assertEqual(task.payload["session_id"], "main:tenant-1")
        self.assertEqual(task.payload["origin_session_key"], "main:tenant-1")
        self.assertEqual(task.payload["profile_id"], 123)
        self.assertEqual(task.payload["source_task_id"], "image-task-1")
        self.assertEqual(task.payload["obligation_id"], "obl-deep-1")
        self.assertIn("深度分析报告", task.payload["user_query"])
        self.assertEqual(summary, "obligation dispatched deep report generation")

    async def test_reverts_dispatched_state_when_submit_fails(self) -> None:
        repo = _ObligationRepo([
            {
                "obligation_id": "obl-2",
                "tenant_key": "tenant-1",
                "action_type": ACTION_GENERATE_SKIN_DIARY,
                "dependency_type": DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
                "payload": {},
                "evidence": {},
            }
        ])
        runtime = _Runtime(fail=True)

        dispatched = await dispatch_obligations_for_dependency(
            obligation_repo=repo,
            runtime=runtime,
            tenant_key="tenant-1",
            dependency_type=DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
        )

        self.assertEqual(dispatched, [])
        self.assertEqual(repo.dispatched, [("obl-2", "obl_obl-2")])
        self.assertEqual(repo.reverted, [("obl-2", "obl_obl-2")])

    async def test_skips_bound_obligation_when_source_task_differs(self) -> None:
        repo = _ObligationRepo([
            {
                "obligation_id": "obl-bound",
                "tenant_key": "tenant-1",
                "session_key": "main:tenant-1",
                "action_type": ACTION_GENERATE_SKIN_DIARY,
                "dependency_type": DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
                "payload": {"dependency_ref_id": "image-task-current"},
                "evidence": {},
            }
        ])
        runtime = _Runtime()

        dispatched = await dispatch_obligations_for_dependency(
            obligation_repo=repo,
            runtime=runtime,
            tenant_key="tenant-1",
            dependency_type=DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
            source_task_id="image-task-old",
        )

        self.assertEqual(dispatched, [])
        self.assertEqual(repo.dispatched, [])
        self.assertEqual(runtime.submitted, [])

    async def test_dispatches_bound_obligation_when_source_task_matches(self) -> None:
        repo = _ObligationRepo([
            {
                "obligation_id": "obl-bound",
                "tenant_key": "tenant-1",
                "session_key": "main:tenant-1",
                "action_type": ACTION_GENERATE_SKIN_DIARY,
                "dependency_type": DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
                "payload": {"dependency_ref_id": "image-task-current"},
                "evidence": {},
            }
        ])
        runtime = _Runtime()

        dispatched = await dispatch_obligations_for_dependency(
            obligation_repo=repo,
            runtime=runtime,
            tenant_key="tenant-1",
            dependency_type=DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
            source_task_id="image-task-current",
        )

        self.assertEqual(len(dispatched), 1)
        self.assertEqual(repo.dispatched, [("obl-bound", "obl_obl-bound")])
        task, _ = runtime.submitted[0]
        self.assertEqual(task.payload["source_task_id"], "image-task-current")

    async def test_prefers_bound_obligation_over_legacy_for_same_action(self) -> None:
        repo = _ObligationRepo([
            {
                "obligation_id": "obl-legacy",
                "tenant_key": "tenant-1",
                "session_key": "main:tenant-1",
                "action_type": ACTION_GENERATE_SKIN_DIARY,
                "dependency_type": DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
                "payload": {},
                "evidence": {},
            },
            {
                "obligation_id": "obl-bound",
                "tenant_key": "tenant-1",
                "session_key": "main:tenant-1",
                "action_type": ACTION_GENERATE_SKIN_DIARY,
                "dependency_type": DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
                "payload": {"dependency_ref_id": "image-task-current"},
                "evidence": {},
            },
        ])
        runtime = _Runtime()

        dispatched = await dispatch_obligations_for_dependency(
            obligation_repo=repo,
            runtime=runtime,
            tenant_key="tenant-1",
            dependency_type=DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
            source_task_id="image-task-current",
        )

        self.assertEqual(len(dispatched), 1)
        self.assertEqual(dispatched[0]["obligation_id"], "obl-bound")
        self.assertEqual(repo.dispatched, [("obl-bound", "obl_obl-bound")])
        self.assertEqual(repo.cancelled_obligations, ["obl-legacy"])

    async def test_dependency_ref_required_without_id_is_not_dispatched(self) -> None:
        repo = _ObligationRepo([
            {
                "obligation_id": "obl-required",
                "tenant_key": "tenant-1",
                "session_key": "main:tenant-1",
                "action_type": ACTION_GENERATE_SKIN_DIARY,
                "dependency_type": DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
                "payload": {"dependency_ref_required": True},
                "evidence": {},
            }
        ])
        runtime = _Runtime()

        dispatched = await dispatch_obligations_for_dependency(
            obligation_repo=repo,
            runtime=runtime,
            tenant_key="tenant-1",
            dependency_type=DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
            source_task_id="image-task-old",
        )

        self.assertEqual(dispatched, [])
        self.assertEqual(repo.dispatched, [])
        self.assertEqual(runtime.submitted, [])


if __name__ == "__main__":
    unittest.main()
