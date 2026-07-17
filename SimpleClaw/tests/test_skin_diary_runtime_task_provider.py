from __future__ import annotations

import unittest

from simpleclaw.context.providers import ContextBuildContext

from Mojing.runtime.task_types import MojingTaskType
from Mojing.context.runtime_tasks.skin_diary import (
    SkinDiaryHandoffRuntimeTaskAttentionProvider,
)


class _RuntimeTaskRepo:
    def __init__(
        self,
        latest: dict | None = None,
        *,
        tasks: dict[str, dict | None] | None = None,
    ) -> None:
        self.latest = latest
        self.tasks = tasks or {}

    async def find_latest_task_for(self, *, tenant_key: str, task_type: str):
        del tenant_key
        if self.tasks:
            return self.tasks.get(task_type)
        if task_type == MojingTaskType.IMAGE_ANALYSIS:
            return self.latest
        return None


class _ActionUsageRepo:
    def __init__(self, counts: dict[str, int]) -> None:
        self.counts = counts

    async def get_counts(self, tenant_key: str, action_key: str) -> dict[str, int]:
        del tenant_key, action_key
        return dict(self.counts)


class SkinDiaryRuntimeTaskProviderTest(unittest.IsolatedAsyncioTestCase):
    async def test_emits_auto_generation_pending_fact_when_count_is_zero(self) -> None:
        provider = SkinDiaryHandoffRuntimeTaskAttentionProvider(
            runtime_task_repo=_RuntimeTaskRepo({"task_id": "t1", "status": "succeeded", "updated_at": "2026-05-19 10:00:00"}),
            action_usage_repo=_ActionUsageRepo({"submitted_count": 0, "succeeded_count": 0, "failed_count": 0}),
            emission_state={},
        )
        ctx = ContextBuildContext(
            tenant_key="tenant-1",
            history=[],
            query="基础分析完毕了吗？",
            metadata={},
        )

        packets = await provider.collect_attention(ctx)

        self.assertEqual(len(packets), 1)
        self.assertIn("图片分析工具已完成", packets[0].content)
        self.assertIn("系统在 USER.md 同步完成后自动生成", packets[0].content)
        self.assertIn("不要自行触发肌肤日记", packets[0].content)
        self.assertIn("check_runtime_status", packets[0].content)

    async def test_emits_refresh_fact_when_count_is_positive(self) -> None:
        provider = SkinDiaryHandoffRuntimeTaskAttentionProvider(
            runtime_task_repo=_RuntimeTaskRepo({"task_id": "t1", "status": "succeeded", "updated_at": "2026-05-19 10:00:00"}),
            action_usage_repo=_ActionUsageRepo({"submitted_count": 2, "succeeded_count": 1, "failed_count": 0}),
            emission_state={},
        )
        ctx = ContextBuildContext(
            tenant_key="tenant-1",
            history=[],
            query="那今天的护理方向呢？",
            metadata={},
        )

        packets = await provider.collect_attention(ctx)

        self.assertEqual(len(packets), 1)
        self.assertIn("已使用过肌肤日记", packets[0].content)
        self.assertIn("skin_diary.offer_refresh", packets[0].content)
        self.assertIn("必须先回应用户当前问题", packets[0].content)

    async def test_does_not_emit_refresh_when_diary_task_already_covers_image_analysis(self) -> None:
        provider = SkinDiaryHandoffRuntimeTaskAttentionProvider(
            runtime_task_repo=_RuntimeTaskRepo(tasks={
                MojingTaskType.IMAGE_ANALYSIS: {
                    "task_id": "img-1",
                    "status": "succeeded",
                    "completed_at": "2026-05-19 10:00:00",
                },
                MojingTaskType.SKIN_DIARY_GENERATION: {
                    "task_id": "diary-1",
                    "status": "running",
                    "created_at": "2026-05-19 10:00:05",
                },
            }),
            action_usage_repo=_ActionUsageRepo({"submitted_count": 1, "succeeded_count": 0, "failed_count": 0}),
            emission_state={},
        )
        ctx = ContextBuildContext(
            tenant_key="tenant-1",
            history=[],
            query="那今天的护理方向呢？",
            metadata={},
        )

        packets = await provider.collect_attention(ctx)

        self.assertEqual(packets, [])

    async def test_does_not_emit_refresh_when_auto_dispatch_is_queued_for_image_analysis(self) -> None:
        provider = SkinDiaryHandoffRuntimeTaskAttentionProvider(
            runtime_task_repo=_RuntimeTaskRepo(tasks={
                MojingTaskType.IMAGE_ANALYSIS: {
                    "task_id": "img-1",
                    "status": "succeeded",
                    "completed_at": "2026-05-19 10:00:10",
                },
                MojingTaskType.SUBAGENT_DISPATCH: {
                    "task_id": "dispatch-1",
                    "status": "queued",
                    "created_at": "2026-05-19 10:00:00",
                    "payload": {
                        "source": "skin_profile_sync",
                        "action_key": "skin_diary.handoff",
                    },
                },
            }),
            action_usage_repo=_ActionUsageRepo({"submitted_count": 1, "succeeded_count": 0, "failed_count": 0}),
            emission_state={},
        )
        ctx = ContextBuildContext(
            tenant_key="tenant-1",
            history=[],
            query="那今天的护理方向呢？",
            metadata={},
        )

        packets = await provider.collect_attention(ctx)

        self.assertEqual(packets, [])

    async def test_does_not_emit_before_image_analysis_succeeds(self) -> None:
        provider = SkinDiaryHandoffRuntimeTaskAttentionProvider(
            runtime_task_repo=_RuntimeTaskRepo({"task_id": "t1", "status": "wait_external", "updated_at": "2026-05-19 10:00:00"}),
            action_usage_repo=_ActionUsageRepo({"submitted_count": 0, "succeeded_count": 0, "failed_count": 0}),
            emission_state={},
        )
        ctx = ContextBuildContext(
            tenant_key="tenant-1",
            history=[],
            query="现在呢？",
            metadata={},
        )

        packets = await provider.collect_attention(ctx)

        self.assertEqual(packets, [])
