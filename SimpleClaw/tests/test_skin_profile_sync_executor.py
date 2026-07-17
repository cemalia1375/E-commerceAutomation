from __future__ import annotations

import unittest

from simpleclaw.runtime.task_protocol import TaskEnvelope
from Mojing.runtime.executors import make_skin_profile_sync_executor


class _SkinRepo:
    def __init__(self, row: dict | None) -> None:
        self.row = row
        self.synced: list[tuple[int, str]] = []
        self.skipped: list[tuple[int, str]] = []
        self.failed: list[tuple[int, str]] = []
        self.block_meta = None

    async def find_pending(self, tenant_key: str):
        del tenant_key
        return self.row

    @staticmethod
    def parse_json_field(raw):
        return raw

    async def mark_synced(self, profile_id: int, *, sync_reason: str) -> None:
        self.synced.append((profile_id, sync_reason))

    async def mark_skipped(self, profile_id: int, *, sync_reason: str) -> None:
        self.skipped.append((profile_id, sync_reason))

    async def mark_failed(self, profile_id: int, *, error: str) -> None:
        self.failed.append((profile_id, error))

    async def get_block_meta(self, tenant_key: str, block_name: str):
        del tenant_key, block_name
        return self.block_meta

    async def upsert_block_meta(self, **kwargs) -> None:
        self.block_meta = kwargs


class _DocumentRepo:
    def __init__(self, initial: str = "") -> None:
        self.content = initial

    async def get(self, tenant_key: str, name: str):
        del tenant_key, name
        return self.content

    async def set(self, tenant_key: str, name: str, content: str) -> None:
        del tenant_key, name
        self.content = content


class _RuntimeTaskRepo:
    def __init__(self) -> None:
        self.succeeded: list[dict] = []
        self.failed: list[dict] = []

    async def mark_succeeded(
        self,
        task_id: str,
        *,
        summary: str | None = None,
        business_ref_type: str | None = None,
        business_ref_id: str | None = None,
        output_json: dict | None = None,
    ) -> None:
        self.succeeded.append(
            {
                "task_id": task_id,
                "summary": summary,
                "business_ref_type": business_ref_type,
                "business_ref_id": business_ref_id,
                "output_json": output_json,
            }
        )

    async def mark_task_failed(self, task_id: str, *, error: str) -> None:
        self.failed.append({"task_id": task_id, "error": error})


class _ActivationService:
    def __init__(self) -> None:
        self.requests: list[object] = []

    async def enqueue(self, request):
        self.requests.append(request)
        return "activation-1"


def _task() -> TaskEnvelope:
    return TaskEnvelope(
        task_type="skin_profile_sync",
        payload={
            "tenant_key": "tenant-1",
            "session_key": "main:tenant-1",
            "source_task_id": "image-task-1",
        },
        stream="postprocess",
        tenant_key="tenant-1",
        session_key="main:tenant-1",
    )


class SkinProfileSyncExecutorTest(unittest.IsolatedAsyncioTestCase):
    async def test_success_finalizes_source_image_analysis(self) -> None:
        row = {
            "profile_id": 123,
            "skin_attribute_json": {
                "stage": {"name": "年轻肌（1级）"},
                "toneType": {"name": "暖调二白"},
                "oilType": {"name": "混合性皮肤"},
            },
            "signals_json": [],
            "advantages_json": [],
            "overall_state": "状态稳定",
            "created_at": "2026-05-18 10:00:00",
        }
        runtime_task_repo = _RuntimeTaskRepo()
        executor = make_skin_profile_sync_executor(
            skin_repo=_SkinRepo(row),  # type: ignore[arg-type]
            document_repo=_DocumentRepo(),  # type: ignore[arg-type]
            runtime_task_repo=runtime_task_repo,  # type: ignore[arg-type]
        )

        result = await executor(_task())

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(len(runtime_task_repo.succeeded), 1)
        finalized = runtime_task_repo.succeeded[0]
        self.assertEqual(finalized["task_id"], "image-task-1")
        self.assertEqual(finalized["business_ref_type"], "tenant_skin_profile")
        self.assertEqual(finalized["business_ref_id"], "123")
        self.assertEqual(finalized["output_json"]["profile_id"], 123)

    async def test_success_enqueues_image_analysis_completion_activation(self) -> None:
        row = {
            "profile_id": 123,
            "skin_attribute_json": {
                "stage": {"name": "年轻肌（1级）"},
                "toneType": {"name": "暖调二白"},
                "oilType": {"name": "混合性皮肤"},
            },
            "signals_json": [],
            "advantages_json": [],
            "overall_state": "状态稳定",
            "created_at": "2026-05-18 10:00:00",
        }
        activation_service = _ActivationService()
        executor = make_skin_profile_sync_executor(
            skin_repo=_SkinRepo(row),  # type: ignore[arg-type]
            document_repo=_DocumentRepo(),  # type: ignore[arg-type]
            runtime_task_repo=_RuntimeTaskRepo(),  # type: ignore[arg-type]
            activation_service=activation_service,  # type: ignore[arg-type]
        )

        result = await executor(_task())

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(len(activation_service.requests), 1)
        request = activation_service.requests[0]
        self.assertEqual(request.activation_kind, "image_analysis_completion")
        self.assertEqual(request.task_id, "image-task-1")
        self.assertEqual(request.business_ref_type, "tenant_skin_profile")
        self.assertEqual(request.business_ref_id, "123")

    async def test_no_pending_still_finalizes_source_image_analysis(self) -> None:
        runtime_task_repo = _RuntimeTaskRepo()
        executor = make_skin_profile_sync_executor(
            skin_repo=_SkinRepo(None),  # type: ignore[arg-type]
            document_repo=_DocumentRepo(),  # type: ignore[arg-type]
            runtime_task_repo=runtime_task_repo,  # type: ignore[arg-type]
        )

        result = await executor(_task())

        self.assertEqual(result.status, "noop")
        self.assertEqual(len(runtime_task_repo.succeeded), 1)
        self.assertEqual(runtime_task_repo.succeeded[0]["task_id"], "image-task-1")


if __name__ == "__main__":
    unittest.main()
