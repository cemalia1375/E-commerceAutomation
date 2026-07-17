from __future__ import annotations

import json
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from simpleclaw.runtime.task_protocol import TaskEnvelope


pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_batch_tool_submits_and_returns_highlight_navigation():
    from Flowcut.tools.create_cross_episode_highlights import (
        CreateCrossEpisodeHighlightsTool,
    )

    runtime = SimpleNamespace(submit_task=AsyncMock(return_value="queue-1"))
    batch_repo = SimpleNamespace(
        create_batch=AsyncMock(),
        update_orchestrator_state=AsyncMock(),
    )
    asset_repo = SimpleNamespace(
        list_by_tenant=AsyncMock(return_value=[{"id": 11, "episode_no": 1}]),
    )
    tool = CreateCrossEpisodeHighlightsTool(
        runtime=runtime,
        highlight_batch_repo=batch_repo,
        highlight_asset_repo=asset_repo,
    )
    tool.set_context(tenant_key="tenant-a", session_key="session-a")

    result = await tool.prepare_task(
        drama_name="Demo Drama",
        start_episode=1,
        end_episode=5,
    )

    content = json.loads(result.content)
    assert result.ok is True
    assert content["navigate"]["route"] == "/creative?tab=highlight"
    assert content["submitted"] == 1
    runtime.submit_task.assert_awaited_once()
    submitted = runtime.submit_task.await_args.args[0]
    assert submitted.task_type == "highlight_batch"
    assert submitted.tenant_key == "tenant-a"
    assert submitted.session_key == "session-a"
    batch_repo.update_orchestrator_state.assert_awaited_once()
    saved_state = batch_repo.update_orchestrator_state.await_args.args[1]
    assert saved_state["start_episode"] == 1
    assert saved_state["end_episode"] == 5


@pytest.mark.asyncio
async def test_active_highlight_batches_are_returned_as_generating_cards():
    from Flowcut.api.routes.creatives import list_highlight_plan_tasks

    task_repo = SimpleNamespace(list_active=AsyncMock(return_value=[]))
    batch_repo = SimpleNamespace(
        list_active=AsyncMock(return_value=[{
            "batch_id": "batch-1",
            "status": "EPISODE_PREP",
            "drama_name": "Demo Drama",
            "num_candidates": 3,
            "created_at": "2026-07-09 10:00:00",
        }]),
        get_stage_progress=AsyncMock(return_value={
            "episode_prepare": {"done": 1, "failed": 0, "total": 3},
        }),
        list_stages=AsyncMock(return_value=[]),
    )
    container = SimpleNamespace(
        task_repo=task_repo,
        highlight_batch_repo=batch_repo,
    )
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(container=container)),
    )

    response = await list_highlight_plan_tasks(
        request=request,
        tenant_key="tenant-a",
    )

    assert response["data"] == [{
        "task_id": "batch:batch-1",
        "status": "running",
        "drama_name": "Demo Drama",
        "num_candidates": 3,
        "batch_id": "batch-1",
        "stage": "EPISODE_PREP",
        "progress": {
            "stage": "episode_prep",
            "stage_label": "正在准备原片",
            "progress_pct": 12,
            "drama": "Demo Drama",
            "candidate_count": 0,
            "created_count": 0,
        },
        "created_at": "2026-07-09 10:00:00",
    }]
    task_repo.list_active.assert_awaited_once_with(
        tenant_key="tenant-a",
        task_types=("highlight_plan",),
    )


@pytest.mark.asyncio
async def test_composing_batch_snapshot_includes_compose_counts():
    from Flowcut.services.highlight_progress import build_highlight_batch_snapshot

    batch_repo = SimpleNamespace(
        get_stage_progress=AsyncMock(return_value={
            "span_plan": {"done": 3, "failed": 0, "total": 3},
        }),
        list_stages=AsyncMock(return_value=[]),
    )
    batch = {
        "batch_id": "batch-1",
        "status": "COMPOSING",
        "drama_name": "Demo Drama",
        "summary_json": {
            "total_created": 3,
            "compose_total": 3,
            "compose_ready": 1,
            "compose_failed": 0,
            "compose_pending": 2,
        },
    }

    snapshot = await build_highlight_batch_snapshot(batch_repo, batch)

    assert snapshot["business_status"] == "COMPOSING"
    assert snapshot["status"] == "running"
    assert snapshot["progress"]["progress_pct"] == 93
    assert snapshot["progress"]["compose_total"] == 3
    assert snapshot["progress"]["compose_ready"] == 1
    assert snapshot["progress"]["compose_pending"] == 2


@pytest.mark.asyncio
async def test_failed_episode_prepare_wakes_batch_orchestrator():
    from Flowcut.runtime.highlight_episode_prepare import (
        make_episode_prepare_executor,
    )

    runtime = SimpleNamespace(submit_task=AsyncMock(return_value="queue-2"))
    batch_repo = SimpleNamespace(mark_stage_failed=AsyncMock())
    executor = make_episode_prepare_executor(
        runtime=runtime,
        oss_client=SimpleNamespace(),
        highlight_batch_repo=batch_repo,
    )
    task = TaskEnvelope(
        task_type="episode_prepare",
        payload={
            "batch_id": "batch-2",
            "stage_id": 7,
            "asset_id": 12,
            "episode_no": 4,
            "oss_key": "",
            "tenant_key": "tenant-a",
        },
        stream="flowcut:highlight_episode_prepare",
        tenant_key="tenant-a",
    )

    result = await executor(task)

    assert result.status == "failed"
    runtime.submit_task.assert_awaited_once()
    continuation = runtime.submit_task.await_args.args[0]
    assert continuation.task_type == "highlight_batch"
    assert continuation.payload["batch_id"] == "batch-2"


@pytest.mark.asyncio
async def test_batch_orchestrator_honors_persisted_episode_range():
    from Flowcut.runtime.highlight_batch import make_highlight_batch_executor

    runtime = SimpleNamespace(submit_task=AsyncMock(return_value="queued"))
    batch_repo = SimpleNamespace(
        get_batch=AsyncMock(return_value={
            "batch_id": "batch-3",
            "tenant_key": "tenant-a",
            "drama_name": "Demo Drama",
            "num_candidates": 3,
            "status": "EPISODE_PREP",
            "orchestrator_state_json": {
                "start_episode": 4,
                "end_episode": 5,
                "session_key": "session-a",
            },
        }),
        list_stages=AsyncMock(side_effect=[
            [],
            [{"status": "PENDING"}, {"status": "PENDING"}],
        ]),
        create_stage=AsyncMock(side_effect=[{"id": 41}, {"id": 42}]),
        update_status=AsyncMock(),
    )
    asset_repo = SimpleNamespace(
        list_by_tenant=AsyncMock(return_value=[
            {"id": episode, "episode_no": episode, "oss_key": f"ep{episode}.mp4"}
            for episode in range(1, 6)
        ]),
    )
    executor = make_highlight_batch_executor(
        runtime=runtime,
        highlight_batch_repo=batch_repo,
        highlight_asset_repo=asset_repo,
    )
    task = TaskEnvelope(
        task_type="highlight_batch",
        payload={
            "batch_id": "batch-3",
            "tenant_key": "tenant-a",
            "session_key": "session-a",
        },
        stream="flowcut:highlight_batch",
        tenant_key="tenant-a",
        session_key="session-a",
    )

    result = await executor(task)

    assert result.status == "wait_external"
    submitted_episodes = [
        call.args[0].payload["episode_no"]
        for call in runtime.submit_task.await_args_list
    ]
    assert submitted_episodes == [4, 5]


@pytest.mark.asyncio
async def test_startup_recovery_wakes_existing_active_batch():
    from Flowcut.runtime.highlight_continuation import (
        recover_active_highlight_batches,
    )

    runtime = SimpleNamespace(submit_task=AsyncMock(return_value="queued"))
    batch_repo = SimpleNamespace(
        list_all_active=AsyncMock(return_value=[{
            "batch_id": "batch-old",
            "tenant_key": "tenant-a",
            "orchestrator_state_json": json.dumps({"session_key": "session-a"}),
        }]),
    )

    recovered = await recover_active_highlight_batches(
        runtime=runtime,
        highlight_batch_repo=batch_repo,
    )

    assert recovered == 1
    continuation = runtime.submit_task.await_args.args[0]
    assert continuation.payload["batch_id"] == "batch-old"
    assert continuation.session_key == "session-a"


@pytest.mark.asyncio
async def test_check_task_status_supports_highlight_batch_id():
    from Flowcut.tools.check_task_status import CheckTaskStatusTool

    task_repo = SimpleNamespace(find_by_task_id=AsyncMock(return_value=None))
    batch_repo = SimpleNamespace(
        get_batch=AsyncMock(return_value={
            "batch_id": "batch-progress",
            "tenant_key": "tenant-a",
            "drama_name": "Demo Drama",
            "status": "MERGE_DECOMPOSE",
            "orchestrator_state_json": {},
            "summary_json": {},
        }),
        get_stage_progress=AsyncMock(return_value={
            "episode_prepare": {"done": 3, "failed": 0, "total": 3},
            "merge_decompose": {"done": 0, "failed": 0, "total": 1},
        }),
        list_stages=AsyncMock(return_value=[]),
    )
    tool = CheckTaskStatusTool(
        task_repo=task_repo,
        highlight_batch_repo=batch_repo,
    )

    result = await tool.execute("batch-progress")
    content = json.loads(result.content)

    assert result.ok is True
    assert content["task_id"] == "batch:batch-progress"
    assert content["status"] == "running"
    assert content["data"]["details"]["progress_pct"] == 30
    assert "正在并行拆镜" in content["message"]


def test_scene_detect_uses_opencv_when_pyav_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
):
    from Flowcut.services.scene_align import _run_scene_detect

    selected: dict[str, str] = {}

    def open_video(_path, *, backend):
        selected["backend"] = backend
        return object()

    class SceneManager:
        def add_detector(self, _detector):
            return None

        def detect_scenes(self, _video, *, show_progress):
            return None

        def get_scene_list(self):
            return []

    fake_scenedetect = types.ModuleType("scenedetect")
    fake_scenedetect.open_video = open_video
    fake_scenedetect.SceneManager = SceneManager
    fake_backends = types.ModuleType("scenedetect.backends")
    fake_backends.AVAILABLE_BACKENDS = {"opencv": object()}
    fake_detectors = types.ModuleType("scenedetect.detectors")
    fake_detectors.ContentDetector = lambda **_kwargs: object()
    monkeypatch.setitem(sys.modules, "scenedetect", fake_scenedetect)
    monkeypatch.setitem(sys.modules, "scenedetect.backends", fake_backends)
    monkeypatch.setitem(sys.modules, "scenedetect.detectors", fake_detectors)

    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"not-empty")

    assert _run_scene_detect(str(video_path)) == [0.0]
    assert selected["backend"] == "opencv"
