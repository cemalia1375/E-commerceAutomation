"""Unit tests for make_scene_decompose_executor — mock Gemini + scenedetect + repo."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from simpleclaw.runtime.task_protocol import TaskEnvelope
from Flowcut.runtime.executors import make_scene_decompose_executor
from Flowcut.runtime.streams import FlowcutTaskStream


def _make_envelope(material_id: int, oss_key: str = "fc/test.mp4", oss_url: str = "") -> TaskEnvelope:
    return TaskEnvelope(
        task_type="scene_decompose",
        payload={"material_id": material_id, "oss_key": oss_key, "oss_url": oss_url},
        stream=FlowcutTaskStream.SCENE_DECOMPOSE,
    )


@pytest.mark.asyncio
async def test_executor_success_writes_scene_data():
    """Happy path: Gemini returns 3 segments, scene detect returns cuts → aligned data written."""
    mock_repo = MagicMock()
    mock_repo.update_status = AsyncMock()

    fake_segments = [
        {"start_time": 0.0, "end_time": 4.2, "content": "开场"},
        {"start_time": 4.2, "end_time": 8.1, "content": "产品"},
        {"start_time": 8.1, "end_time": 12.0, "content": "结尾"},
    ]
    fake_cuts = [0.0, 3.96, 7.92, 12.03]

    with (
        patch("Flowcut.runtime.executors._download_file", new=AsyncMock()),
        patch("Flowcut.runtime.executors.analyze_video", new=AsyncMock(return_value=fake_segments)),
        patch("Flowcut.runtime.executors.detect_scene_cuts", new=AsyncMock(return_value=fake_cuts)),
        patch("Flowcut.runtime.executors._resolve_download_url", return_value="http://example.com/video.mp4"),
    ):
        executor = make_scene_decompose_executor(material_repo=mock_repo)
        result = await executor(_make_envelope(42))

    assert result.status == "succeeded"
    mock_repo.update_status.assert_awaited_once()
    call_kwargs = mock_repo.update_status.call_args.kwargs
    assert call_kwargs["status"] == "READY"
    assert isinstance(call_kwargs["scene_data"], list)
    assert len(call_kwargs["scene_data"]) == 3


@pytest.mark.asyncio
async def test_executor_gemini_returns_empty_marks_failed():
    """Gemini returns empty segments → mark material FAILED."""
    mock_repo = MagicMock()
    mock_repo.update_status = AsyncMock()

    with (
        patch("Flowcut.runtime.executors._download_file", new=AsyncMock()),
        patch("Flowcut.runtime.executors.analyze_video", new=AsyncMock(return_value=[])),
        patch("Flowcut.runtime.executors.detect_scene_cuts", new=AsyncMock(return_value=[0.0])),
        patch("Flowcut.runtime.executors._resolve_download_url", return_value="http://example.com/video.mp4"),
    ):
        executor = make_scene_decompose_executor(material_repo=mock_repo)
        result = await executor(_make_envelope(99))

    assert result.status == "failed"


@pytest.mark.asyncio
async def test_executor_download_failure_marks_failed():
    """Download exception → executor returns failed without crashing."""
    mock_repo = MagicMock()
    mock_repo.update_status = AsyncMock()

    with (
        patch("Flowcut.runtime.executors._download_file", new=AsyncMock(side_effect=RuntimeError("network error"))),
        patch("Flowcut.runtime.executors._resolve_download_url", return_value="http://example.com/video.mp4"),
    ):
        executor = make_scene_decompose_executor(material_repo=mock_repo)
        result = await executor(_make_envelope(7))

    assert result.status == "failed"
    assert "network error" in (result.error or "")
