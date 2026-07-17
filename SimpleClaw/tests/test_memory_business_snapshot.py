"""Tests for Mojing memory business snapshot builder."""

from __future__ import annotations

import json

import pytest

from simpleclaw.runtime.task_protocol import RuntimeTaskRecord

from Mojing.memory.business_snapshot import MojingMemoryBusinessSnapshotBuilder


class _RuntimeTaskRepo:
    async def get(self, task_id: str):
        if task_id != "task-image-1":
            return None
        return RuntimeTaskRecord(
            task_id="task-image-1",
            task_type="image_analysis",
            status="succeeded",
            tenant_key="tenant-1",
            session_key="main:tenant-1",
            trace_id="trace-1",
            business_ref_type="skin_profile",
            business_ref_id="profile-1",
            summary="image analysis completed",
            input_json={"image_id": "img-1"},
            output_json={"profile_id": "profile-1"},
        )


class _DocumentRepo:
    async def list_versions_by_source_tasks(self, *, tenant_key, source_task_ids, limit=20):
        assert tenant_key == "tenant-1"
        assert source_task_ids == ["task-image-1"]
        return [{
            "version_id": 7,
            "doc_name": "USER.md",
            "doc_type": "user",
            "version_no": 3,
            "content_hash": "hash-1",
            "change_source": "postprocess",
            "source_task_id": "task-image-1",
            "session_key": "main:tenant-1",
            "trace_id": "trace-1",
            "change_summary": "updated skin profile",
            "created_at": "2026-06-06 12:00:00",
        }]

    async def list_recent_versions_for_session(self, *, tenant_key, session_key, limit=10):
        return []


@pytest.mark.asyncio
async def test_builder_collects_runtime_tasks_and_document_versions() -> None:
    builder = MojingMemoryBusinessSnapshotBuilder(
        runtime_task_repo=_RuntimeTaskRepo(),
        document_repo=_DocumentRepo(),
    )
    source_chunk = [{
        "role": "tool",
        "call_id": "call-1",
        "content": json.dumps({
            "runtime_task_id": "task-image-1",
            "trace_id": "trace-1",
            "business_ref_type": "skin_profile",
            "business_ref_id": "profile-1",
        }),
    }]

    snapshot = await builder.build(
        tenant_key="tenant-1",
        session_key="main:tenant-1",
        source="main",
        source_chunk=source_chunk,
        base_snapshot={"message_count": 1},
    )

    assert snapshot["message_count"] == 1
    assert snapshot["business_refs"]["task_ids"] == ["task-image-1"]
    assert snapshot["business_refs"]["trace_ids"] == ["trace-1"]
    assert snapshot["runtime_tasks"][0]["task_type"] == "image_analysis"
    assert snapshot["runtime_tasks"][0]["business_ref_id"] == "profile-1"
    assert snapshot["document_versions"][0]["relation"] == "source_task_id"
    assert snapshot["document_versions"][0]["doc_name"] == "USER.md"
