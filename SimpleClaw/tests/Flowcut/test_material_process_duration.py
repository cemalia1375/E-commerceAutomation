"""MATERIAL_PROCESS executor 回填 fc_material.duration（ffprobe 拿真实秒数）。

历史问题：上传时 duration=0.0 占位，executor 只回填 transcript/description/cover，
duration 永远是 0 → 前端 MatchTab 渲染 "0.0s"。
"""
from __future__ import annotations

from typing import Any

import pytest

from simpleclaw.runtime.task_protocol import TaskEnvelope
from Flowcut.runtime import executors as exec_mod
from Flowcut.runtime.streams import FlowcutTaskStream


class _SpyMaterialRepo:
    def __init__(self) -> None:
        self.update_calls: list[dict[str, Any]] = []
        self.mark_indexed: list[int] = []
        self._record: dict[int, dict[str, Any]] = {
            1: {
                "id": 1,
                "tenant_key": "flowcut",
                "product": "口红",
                "scene_role": "演示",
            }
        }

    async def update_status(self, material_id: int, status: str, **kwargs) -> None:
        self.update_calls.append({"id": material_id, "status": status, **kwargs})

    async def get(self, material_id: int) -> dict[str, Any] | None:
        return self._record.get(material_id)

    async def mark_vector_indexed(self, material_id: int) -> None:
        self.mark_indexed.append(material_id)


class _StubEmbedding:
    async def embed(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]


class _StubVectorStore:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def upsert(self, mid, desc, transcript, payload) -> None:  # noqa: D401
        self.calls.append((mid, desc, transcript, payload))


def _make_envelope() -> TaskEnvelope:
    return TaskEnvelope(
        task_type="material_process",
        payload={
            "material_id": 1,
            "oss_key": "materials/flowcut/test.mp4",
            "oss_url": "materials/flowcut/test.mp4",
        },
        stream=FlowcutTaskStream.MATERIAL_PROCESS,
        tenant_key="flowcut",
        scope_key="material:1",
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_material_process_writes_back_duration(monkeypatch) -> None:
    repo = _SpyMaterialRepo()
    embedding = _StubEmbedding()
    vector = _StubVectorStore()

    async def fake_process(oss_key: str, oss_url: str):
        return ("HELLO", "materials/flowcut/test.jpg", "RED LIPSTICK", 12.345)

    monkeypatch.setattr(exec_mod, "_process_video", fake_process)

    executor = exec_mod.make_material_process_executor(repo, embedding, vector)  # type: ignore[arg-type]
    result = await executor(_make_envelope())

    assert result.status == "succeeded", result
    assert len(repo.update_calls) == 1
    call = repo.update_calls[0]
    assert call["status"] == "READY"
    assert call["duration"] == pytest.approx(12.345)
    assert call["transcript"] == "HELLO"
    assert call["description"] == "RED LIPSTICK"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_material_process_skips_duration_when_probe_failed(monkeypatch) -> None:
    """ffprobe 失败时 _process_video 返回 0.0；executor 不应把 0 写回（保留旧值）。"""
    repo = _SpyMaterialRepo()
    embedding = _StubEmbedding()
    vector = _StubVectorStore()

    async def fake_process(oss_key: str, oss_url: str):
        return ("hi", "cover.jpg", "desc", 0.0)

    monkeypatch.setattr(exec_mod, "_process_video", fake_process)

    executor = exec_mod.make_material_process_executor(repo, embedding, vector)  # type: ignore[arg-type]
    await executor(_make_envelope())

    assert repo.update_calls[0]["duration"] is None  # 不覆写


@pytest.mark.unit
@pytest.mark.asyncio
async def test_image_material_writes_thumbnail_and_preview() -> None:
    """图片上传后 worker 必须把 oss_key 作为 thumbnail/preview 写回，
    否则前端 MaterialCard 只能渲染空白占位。"""
    repo = _SpyMaterialRepo()
    embedding = _StubEmbedding()
    vector = _StubVectorStore()

    envelope = TaskEnvelope(
        task_type="material_process",
        payload={
            "material_id": 1,
            "oss_key": "materials/flowcut/通用/uploads/1234_demo.jpg",
            "oss_url": "materials/flowcut/通用/uploads/1234_demo.jpg",
        },
        stream=FlowcutTaskStream.MATERIAL_PROCESS,
        tenant_key="flowcut",
        scope_key="material:1",
    )
    executor = exec_mod.make_material_process_executor(repo, embedding, vector)  # type: ignore[arg-type]
    result = await executor(envelope)

    assert result.status == "succeeded", result
    assert len(repo.update_calls) == 1
    call = repo.update_calls[0]
    assert call["status"] == "READY"
    assert call["thumbnail_url"] == "materials/flowcut/通用/uploads/1234_demo.jpg"
    assert call["preview_url"] == "materials/flowcut/通用/uploads/1234_demo.jpg"


@pytest.mark.unit
def test_probe_duration_handles_missing_ffprobe(monkeypatch) -> None:
    """ffprobe 不存在时应安全返回 0.0，不抛异常。"""
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("ffprobe not installed")

    monkeypatch.setattr(exec_mod.subprocess, "run", fake_run)
    result = exec_mod._probe_duration_seconds("/tmp/no_such_video.mp4")
    assert result == 0.0
