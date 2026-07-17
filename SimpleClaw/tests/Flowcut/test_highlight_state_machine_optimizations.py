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
        self.uploads: list[str] = []

    def download(self, oss_key: str, local_path: str) -> None:
        self.downloads.append(oss_key)
        with open(local_path, "wb") as f:
            f.write(b"x")

    def upload(self, local_path: str, oss_key: str) -> str:
        self.uploads.append(oss_key)
        return oss_key


@pytest.mark.asyncio
async def test_merge_decompose_reuses_prepared_normalized_video(monkeypatch):
    import Flowcut.runtime.highlight_merge_decompose as mod

    state_updates: list[dict] = []
    repo = SimpleNamespace(
        get_batch=AsyncMock(return_value={
            "batch_id": "b1",
            "orchestrator_state_json": {},
        }),
        get_stage=AsyncMock(return_value={"id": 9, "status": "PENDING"}),
        try_mark_stage_running=AsyncMock(return_value=True),
        list_stages=AsyncMock(return_value=[{
            "episode_no": 1,
            "result_json": {
                "normalized_oss_key": "normalized/t/11/norm.mp4",
                "duration": 80.0,
                "episode_no": 1,
                "asset_id": 11,
            },
        }]),
        update_orchestrator_state=AsyncMock(side_effect=lambda _bid, state: state_updates.append(dict(state))),
        set_merged_shots=AsyncMock(),
        mark_stage_ready=AsyncMock(),
        mark_stage_failed=AsyncMock(),
    )
    monkeypatch.setattr(mod, "probe_duration_seconds", lambda _path: 80.0)
    monkeypatch.setattr(mod, "detect_scene_cuts", AsyncMock(return_value=[0.0, 20.0, 80.0]))
    monkeypatch.setattr(mod, "align_timestamps", lambda shots, _cuts: shots)
    monkeypatch.setattr(mod, "detect_content_start", lambda _shots: 4.0)
    monkeypatch.setattr(mod, "analyze_video", AsyncMock(return_value=[
        {"start_time": 4.0, "end_time": 12.0, "copy": "dialogue long enough"},
    ]))

    oss = _FakeOSS()
    executor = mod.make_merge_decompose_executor(
        runtime=SimpleNamespace(submit_task=AsyncMock()),
        oss_client=oss,
        highlight_batch_repo=repo,
    )
    result = await executor(TaskEnvelope(
        task_type="merge_decompose",
        payload={"batch_id": "b1", "stage_id": 9, "tenant_key": "t"},
        stream="flowcut:highlight_merge_decompose",
        tenant_key="t",
    ))

    assert result.status == "succeeded"
    assert oss.downloads == ["normalized/t/11/norm.mp4"]
    assert state_updates[-1]["normalized_episodes"]["1"]["normalized_oss_key"] == "normalized/t/11/norm.mp4"


@pytest.mark.asyncio
async def test_batch_orchestrator_preclaims_span_plan_before_submit():
    from Flowcut.runtime.highlight_batch import make_highlight_batch_executor

    runtime = SimpleNamespace(submit_task=AsyncMock(return_value="queued"))
    repo = SimpleNamespace(
        get_batch=AsyncMock(return_value={
            "batch_id": "b2",
            "tenant_key": "t",
            "drama_name": "Drama",
            "num_candidates": 1,
            "status": "SPAN_PLANNING",
            "orchestrator_state_json": {},
        }),
        list_stages=AsyncMock(return_value=[{
            "id": 31,
            "status": "PENDING",
            "candidate_idx": 0,
            "input_json": {"episode_no": 1, "local_start": 4.0},
        }]),
        mark_stage_running=AsyncMock(),
    )
    executor = make_highlight_batch_executor(
        runtime=runtime,
        highlight_batch_repo=repo,
        highlight_asset_repo=SimpleNamespace(),
    )

    result = await executor(TaskEnvelope(
        task_type="highlight_batch",
        payload={"batch_id": "b2", "tenant_key": "t", "session_key": "s"},
        stream="flowcut:highlight_batch",
        tenant_key="t",
        session_key="s",
    ))

    assert result.status == "wait_external"
    submitted = runtime.submit_task.await_args.args[0]
    repo.mark_stage_running.assert_awaited_once_with(31, runtime_task_id=submitted.task_id)
    assert submitted.task_type == "span_plan"


@pytest.mark.asyncio
async def test_cross_episode_compose_appends_connector(monkeypatch):
    import Flowcut.runtime.executors as ex
    from Flowcut.runtime.executors import make_video_compose_executor

    ffmpeg_calls: list[tuple] = []
    monkeypatch.setattr(ex, "_ffmpeg_cut_clip", lambda src, dst, a, b: (ffmpeg_calls.append(("cut", src, dst, a, b)), open(dst, "wb").close()))
    monkeypatch.setattr(ex, "_ffmpeg_normalize_clip", lambda src, dst: (ffmpeg_calls.append(("normalize", src, dst)), open(dst, "wb").close()))
    monkeypatch.setattr(ex, "_ffmpeg_concat", lambda lst, dst: (ffmpeg_calls.append(("concat", lst, dst)), open(dst, "wb").close()))
    monkeypatch.setattr(ex, "_write_concat_list", lambda path, files: open(path, "w").write("\n".join(files)))

    class CreativeRepo:
        def __init__(self) -> None:
            self.statuses: list[tuple] = []

        async def get(self, _creative_id: int) -> dict:
            return {
                "id": 7,
                "tenant_key": "t",
                "status": "PENDING",
                "connector_asset_id": 20,
                "clip_plan_json": json.dumps({
                    "entries": [
                        {"oss_key": "episodes/1.mp4", "cut_start": 1.0, "cut_end": 10.0},
                    ],
                    "boundary_type": "sentence",
                }),
            }

        async def update_status(self, creative_id: int, status: str, **kw) -> None:
            self.statuses.append((creative_id, status, kw))

    asset_repo = SimpleNamespace(
        get=AsyncMock(return_value={"id": 20, "oss_key": "connectors/dh.mp4"}),
    )
    oss = _FakeOSS()
    executor = make_video_compose_executor(
        creative_repo=CreativeRepo(),
        script_repo=SimpleNamespace(),
        ref_video_repo=SimpleNamespace(),
        highlight_asset_repo=asset_repo,
        oss_client=oss,
    )

    result = await executor(TaskEnvelope(
        task_type="highlight_compose",
        payload={"creative_id": 7},
        stream="flowcut:video_compose",
        tenant_key="t",
    ))

    assert result.status == "succeeded"
    assert result.details["connector_appended"] is True
    assert oss.downloads == ["episodes/1.mp4", "connectors/dh.mp4"]
    assert any(call[0] == "normalize" and "connector" in call[1] for call in ffmpeg_calls)


@pytest.mark.asyncio
async def test_span_plan_falls_back_to_episode_number_from_asset_name(monkeypatch):
    import Flowcut.runtime.highlight_span_plan as mod

    monkeypatch.setattr(mod, "cut_clip", lambda _src, dst, _a, _b: open(dst, "wb").close())
    monkeypatch.setattr(mod, "concat_clips", lambda _lst, dst: open(dst, "wb").close())
    monkeypatch.setattr(mod, "detect_scene_cuts", AsyncMock(return_value=[]))
    monkeypatch.setattr(mod, "analyze_video", AsyncMock(return_value=[]))

    runtime = SimpleNamespace(submit_task=AsyncMock())
    repo = SimpleNamespace(
        get_stage=AsyncMock(return_value={
            "id": 41,
            "status": "PENDING",
            "input_json": {
                "episode_no": 1,
                "local_start": 5.0,
                "global_start": 5.0,
                "hook_strength": 9,
                "reason": "hook",
            },
        }),
        get_batch=AsyncMock(return_value={
            "batch_id": "b3",
            "drama_name": "Drama",
            "orchestrator_state_json": {
                "head_episode_nos": [1, 2, 3],
                "normalized_episodes": {
                    "1": {"normalized_oss_key": "norm/1.mp4", "duration": 80.0},
                    "2": {"normalized_oss_key": "norm/2.mp4", "duration": 80.0},
                    "3": {"normalized_oss_key": "norm/3.mp4", "duration": 80.0},
                },
            },
        }),
        try_mark_stage_running=AsyncMock(return_value=True),
        mark_stage_skipped=AsyncMock(),
        mark_stage_ready=AsyncMock(),
        mark_stage_failed=AsyncMock(),
    )
    asset_repo = SimpleNamespace(
        list_by_tenant=AsyncMock(return_value=[
            {"id": 11, "episode_no": None, "name": "第1集.mp4", "oss_key": "raw/1.mp4"},
            {"id": 12, "episode_no": None, "name": "第2集.mp4", "oss_key": "raw/2.mp4"},
            {"id": 13, "episode_no": None, "name": "第3集.mp4", "oss_key": "raw/3.mp4"},
        ]),
    )

    class CreativeRepo:
        async def create_cross_episode_job(self, **_kwargs):
            return {"id": 77}

        async def update_status(self, *_args, **_kwargs):
            return None

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
            "batch_id": "b3",
            "stage_id": 41,
            "candidate_idx": 0,
            "tenant_key": "t",
            "session_key": "s",
        },
        stream="flowcut:highlight_span_plan",
        tenant_key="t",
    ))

    assert result.status == "succeeded"
    repo.mark_stage_skipped.assert_not_awaited()
    repo.mark_stage_ready.assert_awaited_once()
    assert result.details["creative_id"] == 77


@pytest.mark.asyncio
async def test_start_select_retryable_500_resets_stage_and_records_request_id(monkeypatch):
    import Flowcut.runtime.highlight_start_select as mod

    monkeypatch.setattr(mod, "_START_SELECT_RETRY_BACKOFF_S", (0.0,))
    monkeypatch.setattr(
        mod,
        "select_start_shots",
        AsyncMock(side_effect=RuntimeError(
            "ServerError: 500 None. {'error': {'message': "
            "'服务暂时不可用，请稍后重试 (request id: req-123)', "
            "'type': 'moyu_api_error', 'code': 'do_request_failed'}}"
        )),
    )

    state_updates: list[dict] = []
    repo = SimpleNamespace(
        get_batch=AsyncMock(return_value={
            "batch_id": "b7",
            "merged_shots_json": [
                {
                    "start_time": 3.0,
                    "end_time": 8.0,
                    "visual": "conflict",
                    "copy": "这段对白足够长",
                }
            ],
            "orchestrator_state_json": {
                "offsets": [(1, 0.0)],
                "durations": {"1": 30.0},
                "merge_decompose_progress": {"completed": 1},
            },
        }),
        get_stage=AsyncMock(return_value={"id": 71, "status": "PENDING"}),
        try_mark_stage_running=AsyncMock(return_value=True),
        update_orchestrator_state=AsyncMock(
            side_effect=lambda _bid, state: state_updates.append(dict(state))
        ),
        mark_stage_retry_pending=AsyncMock(),
        mark_stage_failed=AsyncMock(),
    )
    runtime = SimpleNamespace(submit_task=AsyncMock())
    executor = mod.make_start_select_executor(
        runtime=runtime,
        highlight_batch_repo=repo,
    )

    result = await executor(TaskEnvelope(
        task_type="start_select",
        payload={
            "batch_id": "b7",
            "stage_id": 71,
            "num_candidates": 1,
            "tenant_key": "t",
            "session_key": "s",
        },
        stream="flowcut:highlight_start_select",
        tenant_key="t",
        attempt=0,
        max_attempts=3,
    ))

    assert result.status == "failed"
    assert result.details["retryable"] is True
    assert result.details["request_id"] == "req-123"
    repo.mark_stage_retry_pending.assert_awaited_once()
    repo.mark_stage_failed.assert_not_awaited()
    assert state_updates[-1]["merge_decompose_progress"]["completed"] == 1
    assert state_updates[-1]["start_select_retry"]["request_id"] == "req-123"


@pytest.mark.asyncio
async def test_start_select_marks_failed_after_retry_exhausted(monkeypatch):
    import Flowcut.runtime.highlight_start_select as mod

    monkeypatch.setattr(mod, "_START_SELECT_RETRY_BACKOFF_S", (0.0,))
    monkeypatch.setattr(
        mod,
        "select_start_shots",
        AsyncMock(side_effect=RuntimeError(
            "ServerError: 500 None. {'error': {'message': "
            "'服务暂时不可用，请稍后重试 (request id: req-final)', "
            "'type': 'moyu_api_error', 'code': 'do_request_failed'}}"
        )),
    )

    repo = SimpleNamespace(
        get_batch=AsyncMock(return_value={
            "batch_id": "b8",
            "merged_shots_json": [
                {
                    "start_time": 3.0,
                    "end_time": 8.0,
                    "visual": "conflict",
                    "copy": "这段对白足够长",
                }
            ],
            "orchestrator_state_json": {
                "offsets": [(1, 0.0)],
                "durations": {"1": 30.0},
            },
        }),
        get_stage=AsyncMock(return_value={"id": 81, "status": "PENDING"}),
        try_mark_stage_running=AsyncMock(return_value=True),
        update_orchestrator_state=AsyncMock(),
        mark_stage_retry_pending=AsyncMock(),
        mark_stage_failed=AsyncMock(),
    )
    runtime = SimpleNamespace(submit_task=AsyncMock())
    executor = mod.make_start_select_executor(
        runtime=runtime,
        highlight_batch_repo=repo,
    )

    result = await executor(TaskEnvelope(
        task_type="start_select",
        payload={
            "batch_id": "b8",
            "stage_id": 81,
            "num_candidates": 1,
            "tenant_key": "t",
            "session_key": "s",
        },
        stream="flowcut:highlight_start_select",
        tenant_key="t",
        attempt=2,
        max_attempts=3,
    ))

    assert result.status == "failed"
    repo.mark_stage_retry_pending.assert_not_awaited()
    repo.mark_stage_failed.assert_awaited_once()


@pytest.mark.asyncio
async def test_batch_stays_composing_until_creatives_are_ready(monkeypatch):
    import Flowcut.runtime.highlight_batch as mod

    monkeypatch.setattr(mod, "_POLL_INTERVAL_S", 0.0)
    runtime = SimpleNamespace(submit_task=AsyncMock(return_value="queued"))
    repo = SimpleNamespace(
        get_batch=AsyncMock(return_value={
            "batch_id": "b5",
            "tenant_key": "t",
            "drama_name": "Drama",
            "num_candidates": 2,
            "status": "COMPOSING",
            "orchestrator_state_json": {},
        }),
        list_stages=AsyncMock(return_value=[
            {"id": 51, "stage": "span_plan", "status": "READY", "creative_id": 101},
            {"id": 52, "stage": "span_plan", "status": "READY", "creative_id": 102},
        ]),
        set_summary=AsyncMock(),
        update_status=AsyncMock(),
    )
    creative_repo = SimpleNamespace(
        get=AsyncMock(side_effect=[
            {"id": 101, "status": "READY", "oss_url": "ok.mp4"},
            {"id": 102, "status": "PROCESSING", "oss_url": None},
        ]),
    )
    executor = mod.make_highlight_batch_executor(
        runtime=runtime,
        highlight_batch_repo=repo,
        highlight_asset_repo=SimpleNamespace(),
        creative_repo=creative_repo,
    )

    result = await executor(TaskEnvelope(
        task_type="highlight_batch",
        payload={"batch_id": "b5", "tenant_key": "t", "session_key": "s"},
        stream="flowcut:highlight_batch",
        tenant_key="t",
        session_key="s",
    ))

    assert result.status == "wait_external"
    repo.update_status.assert_not_awaited()
    summary = repo.set_summary.await_args.args[1]
    assert summary["compose_ready"] == 1
    assert summary["compose_pending"] == 1
    submitted = runtime.submit_task.await_args.args[0]
    assert submitted.task_type == "highlight_batch"


@pytest.mark.asyncio
async def test_batch_ready_only_after_all_creatives_are_ready():
    from Flowcut.runtime.highlight_batch import make_highlight_batch_executor

    runtime = SimpleNamespace(submit_task=AsyncMock(return_value="queued"))
    repo = SimpleNamespace(
        get_batch=AsyncMock(return_value={
            "batch_id": "b6",
            "tenant_key": "t",
            "drama_name": "Drama",
            "num_candidates": 2,
            "status": "COMPOSING",
            "orchestrator_state_json": {},
        }),
        list_stages=AsyncMock(return_value=[
            {"id": 61, "stage": "span_plan", "status": "READY", "creative_id": 201},
            {"id": 62, "stage": "span_plan", "status": "READY", "creative_id": 202},
        ]),
        set_summary=AsyncMock(),
        update_status=AsyncMock(),
    )
    creative_repo = SimpleNamespace(
        get=AsyncMock(side_effect=[
            {"id": 201, "status": "READY", "oss_url": "a.mp4"},
            {"id": 202, "status": "READY", "oss_url": "b.mp4"},
        ]),
    )
    executor = make_highlight_batch_executor(
        runtime=runtime,
        highlight_batch_repo=repo,
        highlight_asset_repo=SimpleNamespace(),
        creative_repo=creative_repo,
    )

    result = await executor(TaskEnvelope(
        task_type="highlight_batch",
        payload={"batch_id": "b6", "tenant_key": "t", "session_key": "s"},
        stream="flowcut:highlight_batch",
        tenant_key="t",
        session_key="s",
    ))

    assert result.status == "succeeded"
    repo.update_status.assert_awaited_once_with("b6", "READY")
    assert result.details["compose_ready"] == 2
    runtime.submit_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_batch_resets_span_plan_stages():
    from Flowcut.api.routes.highlight_batches import retry_batch

    repo = SimpleNamespace(
        get_batch=AsyncMock(return_value={"batch_id": "b4", "status": "FAILED"}),
        reset_stages_for_retry=AsyncMock(return_value=3),
        update_status=AsyncMock(),
    )
    runtime = SimpleNamespace(submit_task=AsyncMock())
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                container=SimpleNamespace(repo=repo, highlight_batch_repo=repo, runtime=runtime)
            )
        )
    )

    result = await retry_batch("b4", request, tenant_key="t")

    repo.reset_stages_for_retry.assert_awaited_once_with("b4", stages=("span_plan",))
    repo.update_status.assert_awaited_once_with("b4", "EPISODE_PREP")
    assert result["reset_stages"] == 3
