from __future__ import annotations

import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from simpleclaw.harness.lifecycle import ToolInvocationContext
from simpleclaw.core.messages import ToolCall

from Mojing.harness.readiness import (
    DeepReportReadiness,
    HistoricalImageReadiness,
    ImageAnalysisReadiness,
    SkinDiaryGenerationReadiness,
)
from Mojing.harness.tool_gates import DeepReportGate, HistoricalImageGate, SkinDiaryGenerationGate
from Mojing.runtime.task_types import MojingTaskType


class _DocRepo:
    def __init__(self, content: str, updated_at: str = "2026-05-03 00:00:00") -> None:
        self.content = content
        self.updated_at = updated_at

    async def get(self, tenant_key: str, doc_name: str) -> str | None:
        del tenant_key, doc_name
        return self.content

    async def get_metadata(self, tenant_key: str, doc_name: str) -> dict:
        del tenant_key, doc_name
        return {"updated_at": self.updated_at, "content_hash": "hash-1"}


class _ImageRepo:
    def __init__(self, latest_time: datetime | None, latest_record: dict | None = None) -> None:
        self.latest_time = latest_time
        self.latest_record = latest_record
        self.record_calls: list[tuple[str, list[str]]] = []

    async def get_latest_time(self, tenant_key: str) -> datetime | None:
        del tenant_key
        return self.latest_time

    async def find_latest_job(self, tenant_key: str) -> dict | None:
        del tenant_key
        if self.latest_record is not None:
            return self.latest_record
        if self.latest_time is None:
            return None
        return {
            "job_id": "image-job-1",
            "image_ref": "https://example.com/face.png",
            "status": "succeeded",
            "created_at": self.latest_time,
            "updated_at": self.latest_time,
        }

    async def get_latest_record_excluding(self, tenant_key: str, exclude_refs: list[str] | None = None) -> dict | None:
        self.record_calls.append((tenant_key, list(exclude_refs or [])))
        return self.latest_record


class _TaskRepo:
    def __init__(
        self,
        tasks: dict[str, dict] | None = None,
        *,
        succeeded_task_types: set[str] | None = None,
    ) -> None:
        self.tasks = tasks or {}
        if succeeded_task_types is None:
            succeeded_task_types = {str(MojingTaskType.IMAGE_ANALYSIS)}
        self.succeeded_task_types = succeeded_task_types

    async def find_latest_task_for(self, *, tenant_key: str, task_type: str) -> dict | None:
        del tenant_key
        return self.tasks.get(task_type)

    async def has_succeeded_task_for(self, *, tenant_key: str, task_type: str) -> bool:
        del tenant_key
        return str(task_type) in self.succeeded_task_types

    async def find_latest_by_scope_key(
        self,
        *,
        tenant_key: str,
        task_type: str,
        scope_key: str,
    ) -> dict | None:
        del tenant_key
        task = self.tasks.get(f"{task_type}:{scope_key}")
        if task is not None:
            return task
        return self.tasks.get(task_type)


class _ReportRepo:
    def __init__(self, latest: dict | None = None) -> None:
        self.latest = latest

    async def find_latest(self, tenant_key: str) -> dict | None:
        del tenant_key
        return self.latest


class _SkinProfileRepo:
    def __init__(self, profile: dict | None = None) -> None:
        self.profile = profile

    async def get_latest(self, tenant_key: str) -> dict | None:
        del tenant_key
        return self.profile


class _SkinDiaryResultRepo:
    def __init__(
        self,
        *,
        has_business_date_result: bool = False,
        has_slot_result: bool = False,
    ) -> None:
        self.has_business_date_result = has_business_date_result
        self.has_slot_result = has_slot_result

    async def has_result_for_business_date(self, tenant_key: str, business_date) -> bool:
        del tenant_key, business_date
        return self.has_business_date_result

    async def has_result_for_business_date_slot(self, tenant_key: str, business_date, diary_slot: str) -> bool:
        del tenant_key, business_date, diary_slot
        return self.has_slot_result


def _today_utc_naive() -> datetime:
    local = datetime.now(ZoneInfo("Asia/Shanghai")).replace(
        hour=10, minute=0, second=0, microsecond=0
    )
    return local.astimezone(timezone.utc).replace(tzinfo=None)


class ImageAnalysisReadinessTest(unittest.IsolatedAsyncioTestCase):
    async def test_succeeded_image_job_is_ready_without_profile_sync(self) -> None:
        now = _today_utc_naive()
        service = ImageAnalysisReadiness(
            document_repo=_DocRepo("## Learned Skin Profile\n- acne: mild"),
            image_repo=_ImageRepo(
                now,
                latest_record={
                    "job_id": "image-job-1",
                    "image_id": "image-1",
                    "image_ref": "https://example.com/face.png",
                    "status": "succeeded",
                    "created_at": now,
                    "updated_at": now,
                },
            ),
            runtime_task_repo=_TaskRepo({"image_analysis": {"status": "wait_external", "task_id": "task-1"}}),
            skin_profile_repo=_SkinProfileRepo({"profile_id": 1, "sync_status": "pending"}),
        )

        status = await service.get_latest_status("tenant-1")

        self.assertEqual(status.phase, "ready")
        self.assertEqual(status.facts["latest_image_status"], "ready")
        self.assertEqual(status.facts["latest_image_status_raw"], "succeeded")
        self.assertEqual(status.facts["image_analysis_task_status"], "wait_external")
        self.assertEqual(status.facts["latest_profile_sync_status"], "pending")

    async def test_runtime_active_status_overrides_uploaded_job(self) -> None:
        now = _today_utc_naive()
        service = ImageAnalysisReadiness(
            image_repo=_ImageRepo(
                now,
                latest_record={
                    "job_id": "image-job-1",
                    "image_id": "image-1",
                    "image_ref": "https://example.com/face.png",
                    "status": "uploaded",
                    "created_at": now,
                    "updated_at": now,
                },
            ),
            runtime_task_repo=_TaskRepo({
                "image_analysis": {
                    "status": "wait_external",
                    "task_id": "task-1",
                    "payload": {"job_id": "image-job-1"},
                },
            }),
        )

        status = await service.get_latest_status("tenant-1")

        self.assertEqual(status.phase, "wait_external")
        self.assertEqual(status.facts["image_analysis_task_status"], "wait_external")
        self.assertTrue(status.facts["image_analysis_task_matches_latest_job"])


class DeepReportReadinessTest(unittest.IsolatedAsyncioTestCase):
    async def test_no_photo_does_not_use_removed_need_photo_reason(self) -> None:
        service = DeepReportReadiness(
            document_repo=_DocRepo("## Learned Skin Profile\n- acne: mild"),
            image_repo=_ImageRepo(None),
            runtime_task_repo=_TaskRepo(),
            deep_report_repo=_ReportRepo(),
        )

        decision = await service.check_deep_report("tenant-1")

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "need_fresh_photo")
        self.assertEqual(decision.phase, "no_photo")

    async def test_allows_when_fresh_image_and_profile_are_ready(self) -> None:
        service = DeepReportReadiness(
            document_repo=_DocRepo("## Learned Skin Profile\n- acne: mild"),
            image_repo=_ImageRepo(_today_utc_naive()),
            skin_profile_repo=_SkinProfileRepo({"profile_id": 1, "sync_status": "synced"}),
            runtime_task_repo=_TaskRepo({"image_analysis": {"status": "succeeded"}}),
            deep_report_repo=_ReportRepo(),
        )

        decision = await service.check_deep_report("tenant-1")

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "ready")

    async def test_allows_when_latest_image_analysis_succeeded_even_if_profile_sync_pending(self) -> None:
        now = _today_utc_naive()
        service = DeepReportReadiness(
            document_repo=_DocRepo("## Learned Skin Profile\n- acne: mild"),
            skin_profile_repo=_SkinProfileRepo({"profile_id": 1, "sync_status": "pending"}),
            image_repo=_ImageRepo(
                now,
                latest_record={
                    "job_id": "image-job-1",
                    "status": "succeeded",
                    "created_at": now,
                    "updated_at": now,
                },
            ),
            runtime_task_repo=_TaskRepo({"image_analysis": {"status": "succeeded"}}),
            deep_report_repo=_ReportRepo(),
        )

        decision = await service.check_deep_report("tenant-1")

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "ready")
        self.assertEqual(decision.facts["image_analysis_phase"], "ready")
        self.assertEqual(decision.facts["latest_image_status"], "ready")
        self.assertEqual(decision.facts["latest_image_status_raw"], "succeeded")
        self.assertEqual(decision.facts["latest_profile_sync_status"], "pending")

    async def test_allows_when_user_md_changed_after_latest_report_without_learned_profile(self) -> None:
        service = DeepReportReadiness(
            document_repo=_DocRepo(
                "## Basic Profile\n- user says dark circles feel worse lately",
                updated_at="2026-05-03 10:00:00",
            ),
            image_repo=_ImageRepo(datetime(2026, 5, 2, 10, 0, 0)),
            skin_profile_repo=_SkinProfileRepo({"profile_id": 1, "sync_status": "synced"}),
            runtime_task_repo=_TaskRepo({"image_analysis": {"status": "succeeded"}}),
            deep_report_repo=_ReportRepo({"report_id": "r1", "create_time": "2026-05-02 10:00:00"}),
        )

        decision = await service.check_deep_report("tenant-1")

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "ready")

    async def test_gate_dedupes_when_deep_report_dispatch_is_running(self) -> None:
        now = _today_utc_naive()
        service = DeepReportReadiness(
            document_repo=_DocRepo("## Learned Skin Profile\n- acne: mild"),
            image_repo=_ImageRepo(
                now,
                latest_record={
                    "job_id": "image-job-1",
                    "status": "succeeded",
                    "created_at": now,
                    "updated_at": now,
                },
            ),
            skin_profile_repo=_SkinProfileRepo({"profile_id": 1, "sync_status": "pending"}),
            runtime_task_repo=_TaskRepo({
                "subagent_dispatch:subagent_dispatch:deep_report:tenant-1": {
                    "status": "queued",
                    "task_id": "dispatch-1",
                }
            }),
            deep_report_repo=_ReportRepo(),
        )
        gate = DeepReportGate(service)
        ctx = ToolInvocationContext(
            call=ToolCall(id="1", name="deep_report_chat", arguments={}),
            tool=None,
            tool_name="deep_report_chat",
            params={},
            tenant_key="tenant-1",
        )

        decision = await gate.before_tool(ctx)

        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.action, "deduped")
        self.assertEqual(decision.reason, "deep_report_running")
        self.assertEqual(decision.phase, "queued")

    async def test_gate_dedupes_when_report_is_running(self) -> None:
        service = DeepReportReadiness(
            document_repo=_DocRepo("## Learned Skin Profile\n- acne: mild"),
            image_repo=_ImageRepo(_today_utc_naive()),
            skin_profile_repo=_SkinProfileRepo({"profile_id": 1, "sync_status": "synced"}),
            runtime_task_repo=_TaskRepo({
                "image_analysis": {"status": "succeeded"},
                "deep_research": {"status": "wait_external", "task_id": "task-1"},
            }),
            deep_report_repo=_ReportRepo(),
        )
        gate = DeepReportGate(service)
        ctx = ToolInvocationContext(
            call=ToolCall(id="1", name="deep_report_chat", arguments={}),
            tool=None,
            tool_name="deep_report_chat",
            params={},
            tenant_key="tenant-1",
        )

        decision = await gate.before_tool(ctx)

        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.action, "deduped")
        self.assertEqual(decision.reason, "deep_report_running")
        self.assertEqual(decision.phase, "wait_external")

    async def test_gate_defers_when_image_analysis_is_running(self) -> None:
        now = _today_utc_naive()
        service = DeepReportReadiness(
            document_repo=_DocRepo("## Learned Skin Profile\n- acne: mild"),
            image_repo=_ImageRepo(
                now,
                latest_record={
                    "job_id": "image-job-1",
                    "status": "wait_external",
                    "created_at": now,
                    "updated_at": now,
                },
            ),
            runtime_task_repo=_TaskRepo({"image_analysis": {"status": "wait_external"}}),
            deep_report_repo=_ReportRepo(),
        )
        gate = DeepReportGate(service)
        ctx = ToolInvocationContext(
            call=ToolCall(id="1", name="deep_report_chat", arguments={}),
            tool=None,
            tool_name="deep_report_chat",
            params={},
            tenant_key="tenant-1",
        )

        decision = await gate.before_tool(ctx)

        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.action, "deferred")
        self.assertEqual(decision.reason, "image_analysis_running")
        self.assertEqual(decision.phase, "wait_external")

    async def test_historical_image_readiness_denies_when_no_history_image_exists(self) -> None:
        image_repo = _ImageRepo(None)
        service = HistoricalImageReadiness(image_repo=image_repo)

        decision = await service.check_historical_image(
            "tenant-1",
            exclude_refs=["https://example.com/current.png"],
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "no_previous_image")
        self.assertEqual(image_repo.record_calls, [("tenant-1", ["https://example.com/current.png"])])

    async def test_historical_image_gate_allows_when_history_image_exists(self) -> None:
        service = HistoricalImageReadiness(
            image_repo=_ImageRepo(None, latest_record={"image_ref": "https://example.com/history.png"})
        )
        gate = HistoricalImageGate(service)
        ctx = ToolInvocationContext(
            call=ToolCall(id="1", name="retrieve_evidence", arguments={"route": "historical_image"}),
            tool=None,
            tool_name="retrieve_evidence",
            params={"route": "historical_image"},
            tenant_key="tenant-1",
        )

        decision = await gate.before_tool(ctx)

        self.assertIsNone(decision)

    async def test_historical_image_gate_returns_deferred_when_no_history_image_exists(self) -> None:
        service = HistoricalImageReadiness(image_repo=_ImageRepo(None))
        gate = HistoricalImageGate(service)
        ctx = ToolInvocationContext(
            call=ToolCall(id="1", name="retrieve_evidence", arguments={"route": "historical_image"}),
            tool=None,
            tool_name="retrieve_evidence",
            params={"route": "historical_image"},
            tenant_key="tenant-1",
        )

        decision = await gate.before_tool(ctx)

        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "no_history_image")
        self.assertEqual(decision.action, "deferred")

    async def test_skin_diary_generation_does_not_gate_on_missing_profile(self) -> None:
        service = SkinDiaryGenerationReadiness(
            skin_profile_repo=_SkinProfileRepo(None),
            skin_diary_result_repo=_SkinDiaryResultRepo(),
            runtime_task_repo=_TaskRepo(),
            now_fn=lambda: datetime(2026, 4, 28, 8, 0),
        )

        decision = await service.check_generate_skin_diary("tenant-1")

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "ready")

    async def test_skin_diary_generation_ignores_user_md_text(self) -> None:
        service = SkinDiaryGenerationReadiness(
            document_repo=_DocRepo("## Basic Profile\n- image analysis has not synced yet"),
            skin_profile_repo=_SkinProfileRepo({"profile_id": 1, "sync_status": "synced"}),
            skin_diary_result_repo=_SkinDiaryResultRepo(),
            runtime_task_repo=_TaskRepo(),
            now_fn=lambda: datetime(2026, 4, 28, 8, 0),
        )

        decision = await service.check_generate_skin_diary("tenant-1")

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "ready")

    async def test_skin_diary_generation_does_not_gate_on_profile_sync_status(self) -> None:
        service = SkinDiaryGenerationReadiness(
            document_repo=_DocRepo("## Learned Skin Profile\n- old profile"),
            skin_profile_repo=_SkinProfileRepo({"profile_id": 1, "sync_status": "pending"}),
            skin_diary_result_repo=_SkinDiaryResultRepo(),
            runtime_task_repo=_TaskRepo(),
            now_fn=lambda: datetime(2026, 4, 28, 8, 0),
        )

        decision = await service.check_generate_skin_diary("tenant-1")

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "ready")

    async def test_skin_diary_generation_dedupes_running_task(self) -> None:
        service = SkinDiaryGenerationReadiness(
            skin_profile_repo=_SkinProfileRepo({"profile_id": 1, "sync_status": "synced"}),
            skin_diary_result_repo=_SkinDiaryResultRepo(),
            runtime_task_repo=_TaskRepo({"skin_diary_generation": {"status": "wait_external", "task_id": "task-1"}}),
            now_fn=lambda: datetime(2026, 4, 28, 8, 0),
        )
        gate = SkinDiaryGenerationGate(service)
        ctx = ToolInvocationContext(
            call=ToolCall(id="1", name="generate_skin_diary", arguments={}),
            tool=None,
            tool_name="generate_skin_diary",
            params={},
            tenant_key="tenant-1",
        )

        decision = await gate.before_tool(ctx)

        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "skin_diary_generation_running")
        self.assertEqual(decision.action, "deduped")

    async def test_skin_diary_generation_requires_successful_image_analysis_history(self) -> None:
        service = SkinDiaryGenerationReadiness(
            skin_diary_result_repo=_SkinDiaryResultRepo(),
            runtime_task_repo=_TaskRepo(succeeded_task_types=set()),
            now_fn=lambda: datetime(2026, 4, 28, 8, 0),
        )
        gate = SkinDiaryGenerationGate(service)
        ctx = ToolInvocationContext(
            call=ToolCall(id="1", name="generate_skin_diary", arguments={}),
            tool=None,
            tool_name="generate_skin_diary",
            params={},
            tenant_key="tenant-1",
        )

        decision = await gate.before_tool(ctx)

        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "missing_image_analysis")
        self.assertEqual(decision.action, "deferred")
        self.assertIn("清晰正脸照", decision.message_focus)

    async def test_skin_diary_handoff_requires_successful_image_analysis_history(self) -> None:
        service = SkinDiaryGenerationReadiness(
            skin_diary_result_repo=_SkinDiaryResultRepo(),
            runtime_task_repo=_TaskRepo(succeeded_task_types=set()),
            now_fn=lambda: datetime(2026, 4, 28, 8, 0),
        )
        gate = SkinDiaryGenerationGate(service)
        ctx = ToolInvocationContext(
            call=ToolCall(id="1", name="notify_skin_diary_chat", arguments={"intent": "handoff"}),
            tool=None,
            tool_name="notify_skin_diary_chat",
            params={"intent": "handoff"},
            tenant_key="tenant-1",
        )

        decision = await gate.before_tool(ctx)

        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "missing_image_analysis")
        self.assertEqual(decision.action, "deferred")
        self.assertIn("清晰正脸照", decision.message_focus)

    async def test_skin_diary_chat_handoff_gate_ignores_chat_intent(self) -> None:
        service = SkinDiaryGenerationReadiness(
            skin_diary_result_repo=_SkinDiaryResultRepo(),
            runtime_task_repo=_TaskRepo(succeeded_task_types=set()),
            now_fn=lambda: datetime(2026, 4, 28, 8, 0),
        )
        gate = SkinDiaryGenerationGate(service)
        ctx = ToolInvocationContext(
            call=ToolCall(id="1", name="notify_skin_diary_chat", arguments={"intent": "chat"}),
            tool=None,
            tool_name="notify_skin_diary_chat",
            params={"intent": "chat"},
            tenant_key="tenant-1",
        )

        decision = await gate.before_tool(ctx)

        self.assertIsNone(decision)

    async def test_skin_diary_handoff_does_not_apply_generation_refresh_rules(self) -> None:
        service = SkinDiaryGenerationReadiness(
            skin_diary_result_repo=_SkinDiaryResultRepo(has_slot_result=True),
            runtime_task_repo=_TaskRepo(),
            now_fn=lambda: datetime(2026, 4, 28, 8, 0),
        )
        gate = SkinDiaryGenerationGate(service)
        ctx = ToolInvocationContext(
            call=ToolCall(id="1", name="notify_skin_diary_chat", arguments={"intent": "handoff"}),
            tool=None,
            tool_name="notify_skin_diary_chat",
            params={"intent": "handoff"},
            tenant_key="tenant-1",
        )

        decision = await gate.before_tool(ctx)

        self.assertIsNone(decision)

    async def test_skin_diary_generation_requires_refresh_reason_when_result_exists(self) -> None:
        service = SkinDiaryGenerationReadiness(
            skin_profile_repo=_SkinProfileRepo({"profile_id": 1, "sync_status": "synced"}),
            skin_diary_result_repo=_SkinDiaryResultRepo(has_slot_result=True),
            runtime_task_repo=_TaskRepo(),
            now_fn=lambda: datetime(2026, 4, 28, 8, 0),
        )

        decision = await service.check_generate_skin_diary("tenant-1")

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "existing_diary_requires_refresh_intent")

    async def test_skin_diary_generation_allows_first_generation(self) -> None:
        service = SkinDiaryGenerationReadiness(
            skin_profile_repo=_SkinProfileRepo({"profile_id": 1, "sync_status": "synced"}),
            skin_diary_result_repo=_SkinDiaryResultRepo(),
            runtime_task_repo=_TaskRepo(),
            now_fn=lambda: datetime(2026, 4, 28, 8, 0),
        )

        decision = await service.check_generate_skin_diary("tenant-1")

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "ready")

    async def test_skin_diary_generation_allows_refresh_when_reason_is_present(self) -> None:
        service = SkinDiaryGenerationReadiness(
            skin_profile_repo=_SkinProfileRepo({"profile_id": 1, "sync_status": "synced"}),
            skin_diary_result_repo=_SkinDiaryResultRepo(has_slot_result=True),
            runtime_task_repo=_TaskRepo(),
            now_fn=lambda: datetime(2026, 4, 28, 8, 0),
        )

        decision = await service.check_generate_skin_diary(
            "tenant-1",
            generation_input={"regeneration_reason": "用户明确要求重新生成"},
        )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "ready")


if __name__ == "__main__":
    unittest.main()
