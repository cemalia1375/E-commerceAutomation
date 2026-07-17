from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from simpleclaw.runtime.task_protocol import TaskEnvelope


pytestmark = pytest.mark.unit


class _FakeOSS:
    def __init__(self) -> None:
        self.downloads: list[str] = []

    def download(self, oss_key: str, local_path: str) -> None:
        self.downloads.append(oss_key)
        with open(local_path, "wb") as f:
            f.write(b"fake-video")


@pytest.mark.asyncio
async def test_span_plan_replay_uses_asr_sentence_boundaries(monkeypatch):
    import Flowcut.runtime.highlight_span_plan as mod

    monkeypatch.setattr(
        mod,
        "cut_clip",
        lambda _src, dst, _a, _b: open(dst, "wb").close(),
    )
    monkeypatch.setattr(mod, "concat_clips", lambda _lst, dst: open(dst, "wb").close())
    monkeypatch.setattr(mod, "detect_scene_cuts", AsyncMock(return_value=[]))
    monkeypatch.setattr(mod, "analyze_video", AsyncMock(return_value=[]))

    captured: dict = {}

    class CreativeRepo:
        async def create_cross_episode_job(self, **kwargs):
            captured.update(kwargs)
            return {"id": 901}

        async def update_status(self, *_args, **_kwargs):
            return None

    runtime = SimpleNamespace(submit_task=AsyncMock())
    repo = SimpleNamespace(
        get_stage=AsyncMock(return_value={
            "id": 501,
            "status": "PENDING",
            "input_json": {
                "episode_no": 1,
                "local_start": 10.5,
                "global_start": 10.5,
                "hook_strength": 9.0,
                "reason": "historical replay hook",
            },
        }),
        get_batch=AsyncMock(return_value={
            "batch_id": "replay-batch",
            "drama_name": "Replay Drama",
            "orchestrator_state_json": {
                "content_start": 0.0,
                "head_episode_nos": [1],
                "normalized_episodes": {
                    "1": {
                        "asset_id": 11,
                        "normalized_oss_key": "normalized/t/11/norm.mp4",
                        "duration": 100.0,
                        "asr_sentences": [
                            {
                                "text": "这是一句很长的开场台词，需要从句首开始。",
                                "start_time": 5.0,
                                "end_time": 12.0,
                                "source": "asr",
                            },
                            {
                                "text": "这里是最适合收尾的一整句。",
                                "start_time": 61.0,
                                "end_time": 65.0,
                                "source": "asr",
                            },
                        ],
                    },
                },
            },
        }),
        try_mark_stage_running=AsyncMock(return_value=True),
        mark_stage_skipped=AsyncMock(),
        mark_stage_ready=AsyncMock(),
        mark_stage_failed=AsyncMock(),
    )
    asset_repo = SimpleNamespace(
        list_by_tenant=AsyncMock(return_value=[{
            "id": 11,
            "episode_no": 1,
            "name": "ep1.mp4",
            "oss_key": "raw/1.mp4",
        }]),
    )

    executor = mod.make_span_plan_executor(
        runtime=runtime,
        oss_client=_FakeOSS(),
        highlight_batch_repo=repo,
        highlight_asset_repo=asset_repo,
        creative_repo=CreativeRepo(),
    )

    result = await executor(TaskEnvelope(
        task_type="span_plan",
        payload={
            "batch_id": "replay-batch",
            "stage_id": 501,
            "candidate_idx": 0,
            "tenant_key": "t",
            "session_key": "s",
        },
        stream="flowcut:highlight_span_plan",
        tenant_key="t",
        session_key="s",
    ))

    assert result.status == "succeeded"
    plan = json.loads(captured["clip_plan_json"])

    assert plan["boundary_type"] == "asr_sentence"
    assert plan["start_local"] == pytest.approx(5.0)
    assert plan["asr_start_correction"]["to"] == pytest.approx(5.0)
    assert plan["asr_start_correction"]["sentence"]["text"].startswith("这是一句很长")
    assert plan["asr_end_correction"]["source"] == "asr_sentence"
    assert plan["asr_end_correction"]["text"] == "这里是最适合收尾的一整句。"
    assert plan["entries"][0]["cut_start"] == pytest.approx(5.0)
    assert plan["entries"][0]["cut_end"] == pytest.approx(65.0)
    submitted_tasks = [call.args[0].task_type for call in runtime.submit_task.await_args_list]
    assert "highlight_compose" in submitted_tasks
    assert "highlight_batch" in submitted_tasks
