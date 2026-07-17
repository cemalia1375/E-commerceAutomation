from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from typing import Any

from Mojing.runtime.task_types import MojingTaskType
from Mojing.runtime.triggered_monitor import WaitExternalTaskMonitor


class _RuntimeTaskRepo:
    def __init__(self, tasks: list[dict[str, Any]]) -> None:
        self.tasks = tasks
        self.succeeded: list[tuple[str, str]] = []
        self.failed: list[tuple[str, str]] = []
        self.list_wait_external_calls: list[dict[str, Any]] = []

    async def list_wait_external(
        self,
        *,
        task_types: list[str],
        limit: int,
        claimed_by_values=None,
        claimed_by_hosts=None,
    ) -> list[dict[str, Any]]:
        self.list_wait_external_calls.append({
            "task_types": list(task_types),
            "limit": limit,
            "claimed_by_values": tuple(claimed_by_values or ()),
            "claimed_by_hosts": tuple(claimed_by_hosts or ()),
        })
        del limit
        allowed = {str(task_type) for task_type in task_types}
        return [task for task in self.tasks if str(task.get("task_type")) in allowed]

    async def mark_task_succeeded(self, task_id: str, *, summary: str = "") -> None:
        self.succeeded.append((task_id, summary))

    async def mark_task_failed(self, task_id: str, *, error: str = "") -> None:
        self.failed.append((task_id, error))

    async def mark_succeeded(
        self,
        task_id: str,
        *,
        summary: str = "",
        business_ref_type: str | None = None,
        business_ref_id: str | None = None,
    ) -> None:
        del business_ref_type, business_ref_id
        self.succeeded.append((task_id, summary))

    async def mark_failed(self, task_id: str, error: str, *, summary: str | None = None) -> None:
        del summary
        self.failed.append((task_id, error))


class _DeepReportRepo:
    def __init__(self, *, done: dict[str, Any] | bool | None = None, error: dict[str, Any] | None = None) -> None:
        self.done = {"status": "done"} if done is True else done
        self.error = error
        self.find_done_calls: list[dict[str, Any]] = []
        self.find_error_calls: list[dict[str, Any]] = []

    async def find_error_since(self, **kwargs):
        self.find_error_calls.append(kwargs)
        return self.error

    async def find_done_since(self, **kwargs):
        self.find_done_calls.append(kwargs)
        return self.done

    async def has_done_since(self, **kwargs) -> bool:
        del kwargs
        return self.done is not None


class _SkinProfileRepo:
    def __init__(self, profile: dict[str, Any] | None = None) -> None:
        self.profile = profile
        self.calls: list[dict[str, Any]] = []

    async def find_profile_since(self, **kwargs):
        self.calls.append(kwargs)
        return self.profile


class _ImageRepo:
    def __init__(self) -> None:
        self.succeeded: list[tuple[str, Any]] = []
        self.failed: list[tuple[str, str]] = []

    async def mark_succeeded(self, job_id: str, *, profile_id: Any) -> None:
        self.succeeded.append((job_id, profile_id))

    async def mark_succeeded_for_profile(
        self,
        tenant_key: str,
        profile: dict[str, Any],
    ) -> None:
        self.succeeded.append((tenant_key, profile.get("profile_id")))

    async def mark_failed(self, job_id: str, *, error: str) -> None:
        self.failed.append((job_id, error))


class _RuntimeServices:
    def __init__(self) -> None:
        self.submitted: list[Any] = []

    async def submit_task(self, task, *, tool_name: str | None = None, summary: str | None = None) -> str:
        self.submitted.append((task, tool_name, summary))
        return "queue-1"


class _ActivationService:
    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def enqueue(self, request) -> str:
        self.requests.append(request)
        return "activation-1"


class _SkincareCabinetRepo:
    async def find_latest_by_name(self, **kwargs):
        del kwargs
        return None


class WaitExternalTaskMonitorTest(unittest.IsolatedAsyncioTestCase):
    async def test_image_profile_finalizes_runtime_task_and_image_job(self) -> None:
        task = {
            "task_id": "task-1",
            "task_type": MojingTaskType.IMAGE_ANALYSIS,
            "tenant_key": "tenant-1",
            "created_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "payload": {
                "job_id": "job-1",
                "image_id": "image-1",
                "image": "https://example.com/a.jpg",
                "message_id": "msg-1",
            },
        }
        runtime_repo = _RuntimeTaskRepo([task])
        skin_repo = _SkinProfileRepo({"profile_id": 123, "sync_status": "synced"})
        image_repo = _ImageRepo()
        activation_service = _ActivationService()
        monitor = WaitExternalTaskMonitor(
            runtime_task_repo=runtime_repo,  # type: ignore[arg-type]
            deep_report_repo=_DeepReportRepo(),  # type: ignore[arg-type]
            skin_profile_repo=skin_repo,  # type: ignore[arg-type]
            skincare_cabinet_repo=_SkincareCabinetRepo(),  # type: ignore[arg-type]
            image_repo=image_repo,  # type: ignore[arg-type]
            activation_service=activation_service,  # type: ignore[arg-type]
        )

        await monitor.check_once()

        self.assertTrue(runtime_repo.list_wait_external_calls[0]["claimed_by_hosts"])
        self.assertEqual(runtime_repo.list_wait_external_calls[0]["claimed_by_values"], ())
        self.assertEqual(runtime_repo.succeeded, [("task-1", "image analysis profile synced to USER.md")])
        self.assertEqual(image_repo.succeeded, [("job-1", 123)])
        self.assertEqual(skin_repo.calls[0]["message_id"], "msg-1")
        self.assertEqual(skin_repo.calls[0]["image_ref"], "https://example.com/a.jpg")
        self.assertEqual(len(activation_service.requests), 1)
        self.assertEqual(activation_service.requests[0].activation_kind, "image_analysis_completion")
        self.assertEqual(activation_service.requests[0].business_ref_id, "123")

    async def test_monitor_passes_exact_claimed_by_values_when_configured(self) -> None:
        runtime_repo = _RuntimeTaskRepo([])
        monitor = WaitExternalTaskMonitor(
            runtime_task_repo=runtime_repo,  # type: ignore[arg-type]
            deep_report_repo=_DeepReportRepo(),  # type: ignore[arg-type]
            skin_profile_repo=_SkinProfileRepo(),  # type: ignore[arg-type]
            skincare_cabinet_repo=_SkincareCabinetRepo(),  # type: ignore[arg-type]
            claimed_by_values=("image_analysis:host:abc", "deep_research:host:abc"),
            claimed_by_hosts=("host",),
        )

        await monitor.check_once()

        self.assertEqual(
            runtime_repo.list_wait_external_calls[0]["claimed_by_values"],
            ("image_analysis:host:abc", "deep_research:host:abc"),
        )

    async def test_image_profile_enqueues_skin_profile_sync_when_runtime_is_available(self) -> None:
        task = {
            "task_id": "task-1",
            "task_type": MojingTaskType.IMAGE_ANALYSIS,
            "tenant_key": "tenant-1",
            "session_key": "main:tenant-1",
            "trace_id": "trace-1",
            "created_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "payload": {
                "job_id": "job-1",
                "image_id": "image-1",
                "image": "https://example.com/a.jpg",
                "message_id": "msg-1",
            },
        }
        runtime_repo = _RuntimeTaskRepo([task])
        runtime = _RuntimeServices()
        monitor = WaitExternalTaskMonitor(
            runtime_task_repo=runtime_repo,  # type: ignore[arg-type]
            deep_report_repo=_DeepReportRepo(),  # type: ignore[arg-type]
            skin_profile_repo=_SkinProfileRepo({"profile_id": 123, "sync_status": "pending"}),  # type: ignore[arg-type]
            skincare_cabinet_repo=_SkincareCabinetRepo(),  # type: ignore[arg-type]
            image_repo=_ImageRepo(),  # type: ignore[arg-type]
            runtime=runtime,  # type: ignore[arg-type]
        )

        await monitor.check_once()

        self.assertEqual(runtime_repo.succeeded, [])
        self.assertEqual(runtime_repo.failed, [])
        self.assertEqual(len(runtime.submitted), 1)
        submitted, tool_name, summary = runtime.submitted[0]
        self.assertEqual(submitted.task_type, MojingTaskType.SKIN_PROFILE_SYNC)
        self.assertEqual(submitted.stream, "postprocess")
        self.assertEqual(submitted.tenant_key, "tenant-1")
        self.assertEqual(submitted.session_key, "main:tenant-1")
        self.assertEqual(submitted.scope_key, "postprocess:tenant-1:USER.md")
        self.assertEqual(submitted.trace_id, "trace-1")
        self.assertEqual(submitted.payload["profile_id"], 123)
        self.assertEqual(submitted.payload["source"], "image_analysis_monitor")
        self.assertIsNone(tool_name)
        self.assertEqual(summary, "auto sync image analysis profile to USER.md")

    async def test_image_profile_sync_failed_marks_runtime_task_failed(self) -> None:
        task = {
            "task_id": "task-1",
            "task_type": MojingTaskType.IMAGE_ANALYSIS,
            "tenant_key": "tenant-1",
            "created_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "payload": {
                "job_id": "job-1",
                "image_id": "image-1",
                "image": "https://example.com/a.jpg",
                "message_id": "msg-1",
            },
        }
        runtime_repo = _RuntimeTaskRepo([task])
        image_repo = _ImageRepo()
        monitor = WaitExternalTaskMonitor(
            runtime_task_repo=runtime_repo,  # type: ignore[arg-type]
            deep_report_repo=_DeepReportRepo(),  # type: ignore[arg-type]
            skin_profile_repo=_SkinProfileRepo({"profile_id": 123, "sync_status": "failed"}),  # type: ignore[arg-type]
            skincare_cabinet_repo=_SkincareCabinetRepo(),  # type: ignore[arg-type]
            image_repo=image_repo,  # type: ignore[arg-type]
        )

        await monitor.check_once()

        self.assertEqual(runtime_repo.succeeded, [])
        self.assertEqual(runtime_repo.failed, [("task-1", "skin profile sync failed")])
        self.assertEqual(image_repo.succeeded, [("job-1", 123)])

    async def test_image_timeout_marks_runtime_task_and_image_job_failed(self) -> None:
        task = {
            "task_id": "task-1",
            "task_type": MojingTaskType.IMAGE_ANALYSIS,
            "tenant_key": "tenant-1",
            "created_at": (datetime.utcnow() - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S"),
            "payload": {"job_id": "job-1"},
        }
        runtime_repo = _RuntimeTaskRepo([task])
        image_repo = _ImageRepo()
        monitor = WaitExternalTaskMonitor(
            runtime_task_repo=runtime_repo,  # type: ignore[arg-type]
            deep_report_repo=_DeepReportRepo(),  # type: ignore[arg-type]
            skin_profile_repo=_SkinProfileRepo(),  # type: ignore[arg-type]
            skincare_cabinet_repo=_SkincareCabinetRepo(),  # type: ignore[arg-type]
            image_repo=image_repo,  # type: ignore[arg-type]
            image_timeout_min=5,
        )

        await monitor.check_once()

        self.assertEqual(runtime_repo.failed, [("task-1", "image analysis completion timeout")])
        self.assertEqual(image_repo.failed, [("job-1", "image analysis completion timeout")])

    async def test_deep_report_done_finalizes_runtime_task(self) -> None:
        task = {
            "task_id": "task-2",
            "task_type": MojingTaskType.DEEP_RESEARCH,
            "tenant_key": "tenant-1",
            "created_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "payload": {"trace_id": "trace-1", "session_id": "main:tenant-1"},
        }
        runtime_repo = _RuntimeTaskRepo([task])
        deep_report_repo = _DeepReportRepo(done={"status": "done", "report_id": "report-2"})
        activation_service = _ActivationService()
        monitor = WaitExternalTaskMonitor(
            runtime_task_repo=runtime_repo,  # type: ignore[arg-type]
            deep_report_repo=deep_report_repo,  # type: ignore[arg-type]
            skin_profile_repo=_SkinProfileRepo(),  # type: ignore[arg-type]
            skincare_cabinet_repo=_SkincareCabinetRepo(),  # type: ignore[arg-type]
            image_repo=None,
            activation_service=activation_service,  # type: ignore[arg-type]
        )

        await monitor.check_once()

        self.assertEqual(runtime_repo.succeeded, [("task-2", "deep research report is available")])
        self.assertEqual(len(activation_service.requests), 1)
        self.assertEqual(activation_service.requests[0].activation_kind, "deep_report_completion")
        self.assertEqual(activation_service.requests[0].business_ref_id, "report-2")
        self.assertEqual(deep_report_repo.find_done_calls[0]["session_id"], "main:tenant-1")

    async def test_deep_report_error_marks_failed_and_enqueues_activation(self) -> None:
        task = {
            "task_id": "task-3",
            "task_type": MojingTaskType.DEEP_RESEARCH,
            "tenant_key": "tenant-1",
            "created_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "payload": {"trace_id": "trace-1", "report_id": "report-1"},
        }
        runtime_repo = _RuntimeTaskRepo([task])
        activation_service = _ActivationService()
        monitor = WaitExternalTaskMonitor(
            runtime_task_repo=runtime_repo,  # type: ignore[arg-type]
            deep_report_repo=_DeepReportRepo(error={"status": "error"}),  # type: ignore[arg-type]
            skin_profile_repo=_SkinProfileRepo(),  # type: ignore[arg-type]
            skincare_cabinet_repo=_SkincareCabinetRepo(),  # type: ignore[arg-type]
            image_repo=None,
            activation_service=activation_service,  # type: ignore[arg-type]
        )

        await monitor.check_once()

        self.assertEqual(runtime_repo.failed, [("task-3", "deep research business status=error")])
        self.assertEqual(len(activation_service.requests), 1)
        self.assertEqual(activation_service.requests[0].activation_kind, "deep_research_failure")


if __name__ == "__main__":
    unittest.main()
