from __future__ import annotations

import unittest

from simpleclaw.context.providers import ContextBuildContext

from Mojing.context.providers import (
    DeepReportOutcomeAttentionProvider,
    ImageAnalysisFailureAttentionProvider,
    SkinDiaryCompletionAttentionProvider,
)


class _RuntimeTaskRepo:
    def __init__(self, latest: dict | None) -> None:
        self.latest = latest

    async def find_latest_task_for(self, *, tenant_key: str, task_type: str):
        del tenant_key, task_type
        return self.latest


class _DeepReportRepo:
    async def find_latest(self, tenant_key: str):
        del tenant_key
        return {"create_time": "2026-05-19 16:00:00"}


class SkinDiaryCompletionAttentionProviderTest(unittest.IsolatedAsyncioTestCase):
    async def test_emits_once_when_generation_succeeds(self) -> None:
        provider = SkinDiaryCompletionAttentionProvider(
            runtime_task_repo=_RuntimeTaskRepo({
                "task_id": "g1",
                "status": "succeeded",
                "updated_at": "2026-05-19 15:00:00",
            }),
            emission_state={},
        )
        ctx = ContextBuildContext(
            tenant_key="tenant-1",
            history=[],
            query="",
            metadata={},
        )

        first = await provider.collect_attention(ctx)
        second = await provider.collect_attention(ctx)

        self.assertEqual(len(first), 1)
        self.assertIn("肌肤日记已经完成", first[0].content)
        self.assertEqual(second, [])

    async def test_does_not_emit_before_generation_succeeds(self) -> None:
        provider = SkinDiaryCompletionAttentionProvider(
            runtime_task_repo=_RuntimeTaskRepo({
                "task_id": "g1",
                "status": "running",
                "updated_at": "2026-05-19 15:00:00",
            }),
            emission_state={},
        )
        ctx = ContextBuildContext(
            tenant_key="tenant-1",
            history=[],
            query="",
            metadata={},
        )

        packets = await provider.collect_attention(ctx)

        self.assertEqual(packets, [])

    async def test_emits_once_when_generation_fails(self) -> None:
        provider = SkinDiaryCompletionAttentionProvider(
            runtime_task_repo=_RuntimeTaskRepo({
                "task_id": "g1",
                "status": "failed",
                "summary": "final_failure",
                "updated_at": "2026-05-19 15:00:00",
                "last_error": "model timeout",
            }),
            emission_state={},
        )
        ctx = ContextBuildContext(
            tenant_key="tenant-1",
            history=[],
            query="",
            metadata={},
        )

        first = await provider.collect_attention(ctx)
        second = await provider.collect_attention(ctx)

        self.assertEqual(len(first), 1)
        self.assertIn("肌肤日记没有生成成功", first[0].content)
        self.assertIn("重新生成", first[0].content)
        self.assertEqual(second, [])

    async def test_does_not_emit_for_retryable_generation_failure(self) -> None:
        provider = SkinDiaryCompletionAttentionProvider(
            runtime_task_repo=_RuntimeTaskRepo({
                "task_id": "g1",
                "status": "failed",
                "attempt": 0,
                "max_attempts": 3,
                "updated_at": "2026-05-19 15:00:00",
                "last_error": "temporary timeout",
            }),
            emission_state={},
        )
        ctx = ContextBuildContext(
            tenant_key="tenant-1",
            history=[],
            query="",
            metadata={},
        )

        packets = await provider.collect_attention(ctx)

        self.assertEqual(packets, [])

    async def test_emits_once_when_image_analysis_fails(self) -> None:
        provider = ImageAnalysisFailureAttentionProvider(
            runtime_task_repo=_RuntimeTaskRepo({
                "task_id": "img1",
                "status": "failed",
                "summary": "final_failure",
                "updated_at": "2026-05-19 15:00:00",
                "last_error": "completion timeout",
            }),
            emission_state={},
        )
        ctx = ContextBuildContext(
            tenant_key="tenant-1",
            history=[],
            query="",
            metadata={},
        )

        first = await provider.collect_attention(ctx)
        second = await provider.collect_attention(ctx)

        self.assertEqual(len(first), 1)
        self.assertIn("图片分析断掉了", first[0].content)
        self.assertIn("用刚才那张照片重新分析一次", first[0].content)
        self.assertEqual(second, [])

    async def test_emits_once_when_deep_report_succeeds(self) -> None:
        provider = DeepReportOutcomeAttentionProvider(
            runtime_task_repo=_RuntimeTaskRepo({
                "task_id": "deep1",
                "status": "succeeded",
                "updated_at": "2026-05-19 16:00:00",
            }),
            report_repo=_DeepReportRepo(),  # type: ignore[arg-type]
            emission_state={},
        )
        ctx = ContextBuildContext(
            tenant_key="tenant-1",
            history=[],
            query="",
            metadata={},
        )

        first = await provider.collect_attention(ctx)
        second = await provider.collect_attention(ctx)

        self.assertEqual(len(first), 1)
        self.assertIn("深度分析报告已经生成完成", first[0].content)
        self.assertIn("我的报告", first[0].content)
        self.assertEqual(second, [])

    async def test_emits_once_when_deep_report_fails(self) -> None:
        provider = DeepReportOutcomeAttentionProvider(
            runtime_task_repo=_RuntimeTaskRepo({
                "task_id": "deep1",
                "status": "failed",
                "summary": "final_failure",
                "updated_at": "2026-05-19 16:00:00",
                "last_error": "business error",
            }),
            emission_state={},
        )
        ctx = ContextBuildContext(
            tenant_key="tenant-1",
            history=[],
            query="",
            metadata={},
        )

        first = await provider.collect_attention(ctx)
        second = await provider.collect_attention(ctx)

        self.assertEqual(len(first), 1)
        self.assertIn("深度分析报告没有生成成功", first[0].content)
        self.assertIn("重新生成", first[0].content)
        self.assertEqual(second, [])
