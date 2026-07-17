import json

import pytest

import Flowcut.runtime.executors as ex
from Flowcut.runtime.executors import make_highlight_plan_executor
from Flowcut.runtime.streams import FlowcutTaskStream
from simpleclaw.runtime.task_protocol import TaskEnvelope


class _FakeAssetRepo:
    def __init__(self, episodes):
        self._eps = episodes

    async def list_by_tenant(self, tenant_key, *, asset_type=None,
                             drama_name=None, limit=200, offset=0):
        return list(self._eps)


class _FakeCreativeRepo:
    def __init__(self):
        self.created = []
        self._id = 0

    async def create_cross_episode_job(self, **kw):
        self._id += 1
        row = {"id": self._id, **kw}
        self.created.append(row)
        return row

    async def update_status(self, creative_id, status, **kw):
        for c in self.created:
            if c["id"] == creative_id:
                c["status"] = status
                c.update(kw)
                return
        self.created.append({"id": creative_id, "status": status, **kw})


class _FakeOSS:
    def download(self, oss_key, local_path):
        with open(local_path, "wb") as f:
            f.write(b"x")

    def upload(self, local_path, oss_key):
        return oss_key


class _FakeRuntime:
    def __init__(self):
        self.submitted = []

    async def submit_task(self, envelope, **kw):
        self.submitted.append(envelope)
        return "q-1"


def _fake_segments(count: int = 20) -> list[dict]:
    return [
        {"start_time": 4.0 * k, "end_time": 4.0 * k + 4.0,
         "visual": "画面描述内容足够长", "copy": "这句对白足够长所以能通过校验。", "category": "真人口播"}
        for k in range(count)
    ]


@pytest.fixture
def patch_ffmpeg_gemini(monkeypatch):
    # ffmpeg / probe 全部 no-op；时长固定每集 20s
    monkeypatch.setattr(ex, "_ffmpeg_normalize_clip", lambda src, dst: open(dst, "wb").close())
    monkeypatch.setattr(ex, "_ffmpeg_concat", lambda lst, dst: open(dst, "wb").close())
    monkeypatch.setattr(ex, "_ffmpeg_cut_clip", lambda src, dst, a, b: open(dst, "wb").close())
    monkeypatch.setattr(ex, "_write_concat_list", lambda path, paths: open(path, "w").close())
    monkeypatch.setattr(ex, "_probe_duration_seconds", lambda p: 20.0)

    # PySceneDetect 物理切点：整数秒网格，使 align_timestamps 近似恒等
    # （段落边界本就落在切点上，吸附后不变），便于断言。
    async def fake_cuts(path, **kw):
        return [float(i) for i in range(0, 121)]

    # 拆镜（Stage A 整体 + Stage C span 共用）：连续 4s/段、台词长度满足 MIN_DIALOGUE_CHARS 校验
    async def fake_decompose(path, **kw):
        return _fake_segments()

    # Stage B：Gemini 选起点 → 指向全局 44s（前3集合并里 idx=11 = 第3集第4秒）。
    async def fake_pick(shots, top_n=3, **kw):
        return [{"idx": 11, "hook_strength": 9.0, "reason": "冲突爆发的精彩场景"}]

    monkeypatch.setattr(ex, "detect_scene_cuts", fake_cuts)
    monkeypatch.setattr(ex, "analyze_video", fake_decompose)
    monkeypatch.setattr(ex, "select_start_shots", fake_pick)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_plan_creates_candidates_and_enqueues_compose(patch_ffmpeg_gemini):
    episodes = [
        {"id": 10 + i, "episode_no": i + 1, "oss_key": f"k{i+1}",
         "oss_url": f"u{i+1}", "duration": 20.0, "drama_name": "西瓜地"}
        for i in range(6)
    ]
    asset_repo = _FakeAssetRepo(episodes)
    creative_repo = _FakeCreativeRepo()
    runtime = _FakeRuntime()
    executor = make_highlight_plan_executor(
        runtime=runtime, highlight_asset_repo=asset_repo,
        creative_repo=creative_repo, oss_client=_FakeOSS(),
    )
    task = TaskEnvelope(
        task_type="highlight_plan",
        payload={"drama_name": "西瓜地", "num_candidates": 2,
                 "tenant_key": "flowcut", "session_key": "s"},
        stream=FlowcutTaskStream.HIGHLIGHT_PLAN,
        tenant_key="flowcut",
    )
    result = await executor(task)

    assert result.status == "succeeded"
    assert len(creative_repo.created) >= 1
    # 规划即合成：每个候选产出后立即入队 VIDEO_COMPOSE 合成任务
    assert len(runtime.submitted) >= 1
    compose_task = runtime.submitted[0]
    assert compose_task.stream == FlowcutTaskStream.VIDEO_COMPOSE
    assert compose_task.task_type == "highlight_compose"
    # clip_plan 跨集：起点在第3集第4秒，应包含多集 entry
    plan = json.loads(creative_repo.created[0]["clip_plan_json"])
    assert plan["start_episode_no"] == 3
    assert len(plan["entries"]) >= 2
    assert plan["entries"][0]["episode_no"] == 3
    # 上下文前扩展生效：local_start=4.0 - PRE_ROLL_S 前滚
    assert plan["entries"][0]["cut_start"] <= 4.0
    # 收尾应落在逻辑切点（span 细拆的句末边界），不再恒定硬切
    assert plan["boundary_type"] == "sentence"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_plan_respects_episode_range(patch_ffmpeg_gemini):
    """指定集数范围后起点只落在范围内，跨集不超出范围。"""

    episodes = [
        {"id": 10 + i, "episode_no": i + 1, "oss_key": f"k{i+1}",
         "oss_url": f"u{i+1}", "duration": 20.0, "drama_name": "西瓜地"}
        for i in range(10)   # 造 10 集，保证 range 内有足够空间
    ]
    asset_repo = _FakeAssetRepo(episodes)
    creative_repo = _FakeCreativeRepo()
    runtime = _FakeRuntime()
    executor = make_highlight_plan_executor(
        runtime=runtime, highlight_asset_repo=asset_repo,
        creative_repo=creative_repo, oss_client=_FakeOSS(),
    )
    task = TaskEnvelope(
        task_type="highlight_plan",
        payload={
            "drama_name": "西瓜地",
            "start_episode": 1,
            "num_candidates": 1,
            "tenant_key": "flowcut",
            "session_key": "s",
        },
        stream=FlowcutTaskStream.HIGHLIGHT_PLAN,
        tenant_key="flowcut",
    )
    result = await executor(task)

    # 不传 end_episode 的行为应与原始测试一致
    assert result.status == "succeeded"
    assert len(creative_repo.created) >= 1
    plan = json.loads(creative_repo.created[0]["clip_plan_json"])
    assert plan["start_episode_no"] == 3  # idx=11 在第3集


@pytest.mark.integration
@pytest.mark.asyncio
async def test_plan_retries_empty_stage_a_decompose(monkeypatch):
    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(ex.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(ex, "_ffmpeg_normalize_clip", lambda src, dst: open(dst, "wb").close())
    monkeypatch.setattr(ex, "_ffmpeg_concat", lambda lst, dst: open(dst, "wb").close())
    monkeypatch.setattr(ex, "_ffmpeg_cut_clip", lambda src, dst, a, b: open(dst, "wb").close())
    monkeypatch.setattr(ex, "_write_concat_list", lambda path, paths: open(path, "w").close())
    monkeypatch.setattr(ex, "_probe_duration_seconds", lambda p: 20.0)

    async def fake_cuts(path, **kw):
        return [float(i) for i in range(0, 121)]

    calls = {"n": 0}

    async def flaky_decompose(path, **kw):
        calls["n"] += 1
        if calls["n"] <= 2:
            return []
        return _fake_segments()

    async def fake_pick(shots, top_n=3, **kw):
        return [{"idx": 11, "hook_strength": 9.0, "reason": "冲突爆发"}]

    monkeypatch.setattr(ex, "detect_scene_cuts", fake_cuts)
    monkeypatch.setattr(ex, "analyze_video", flaky_decompose)
    monkeypatch.setattr(ex, "select_start_shots", fake_pick)

    episodes = [
        {"id": 10 + i, "episode_no": i + 1, "oss_key": f"k{i+1}",
         "oss_url": f"u{i+1}", "duration": 20.0, "drama_name": "西瓜地"}
        for i in range(6)
    ]
    executor = make_highlight_plan_executor(
        runtime=_FakeRuntime(),
        highlight_asset_repo=_FakeAssetRepo(episodes),
        creative_repo=_FakeCreativeRepo(),
        oss_client=_FakeOSS(),
    )
    task = TaskEnvelope(
        task_type="highlight_plan",
        payload={"drama_name": "西瓜地", "num_candidates": 1,
                 "tenant_key": "flowcut", "session_key": "s"},
        stream=FlowcutTaskStream.HIGHLIGHT_PLAN,
        tenant_key="flowcut",
    )

    result = await executor(task)

    assert result.status == "succeeded"
    assert calls["n"] >= 3


@pytest.mark.integration
@pytest.mark.asyncio
async def test_plan_reports_episode_diagnostics_when_all_decompose_empty(monkeypatch):
    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(ex.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(ex, "_ffmpeg_normalize_clip", lambda src, dst: open(dst, "wb").close())
    monkeypatch.setattr(ex, "_probe_duration_seconds", lambda p: 20.0)

    async def fake_cuts(path, **kw):
        return [0.0, 20.0]

    async def empty_decompose(path, **kw):
        return []

    monkeypatch.setattr(ex, "detect_scene_cuts", fake_cuts)
    monkeypatch.setattr(ex, "analyze_video", empty_decompose)

    episodes = [
        {"id": 10 + i, "episode_no": i + 1, "oss_key": f"k{i+1}",
         "oss_url": f"u{i+1}", "duration": 20.0, "drama_name": "十年客情，一掌成空"}
        for i in range(2)
    ]
    executor = make_highlight_plan_executor(
        runtime=_FakeRuntime(),
        highlight_asset_repo=_FakeAssetRepo(episodes),
        creative_repo=_FakeCreativeRepo(),
        oss_client=_FakeOSS(),
    )
    task = TaskEnvelope(
        task_type="highlight_plan",
        payload={
            "drama_name": "十年客情，一掌成空",
            "num_candidates": 1,
            "end_episode": 2,
            "tenant_key": "flowcut",
            "session_key": "s",
        },
        stream=FlowcutTaskStream.HIGHLIGHT_PLAN,
        tenant_key="flowcut",
    )

    result = await executor(task)

    assert result.status == "failed"
    assert result.error is not None
    assert "第1-2集（共2集）拆镜为空" in result.error
    assert "ep1" in result.error
    assert "ep2" in result.error
    assert "EmptyDecomposeResultError" in result.error
    diagnostics = result.details["results"][0]["decompose_diagnostics"]
    assert [d["episode_no"] for d in diagnostics] == [1, 2]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_plan_retries_windows_read_error(monkeypatch):
    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(ex.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(ex, "_ffmpeg_normalize_clip", lambda src, dst: open(dst, "wb").close())
    monkeypatch.setattr(ex, "_ffmpeg_concat", lambda lst, dst: open(dst, "wb").close())
    monkeypatch.setattr(ex, "_ffmpeg_cut_clip", lambda src, dst, a, b: open(dst, "wb").close())
    monkeypatch.setattr(ex, "_write_concat_list", lambda path, paths: open(path, "w").close())
    monkeypatch.setattr(ex, "_probe_duration_seconds", lambda p: 20.0)

    async def fake_cuts(path, **kw):
        return [float(i) for i in range(0, 121)]

    calls = {"n": 0}

    async def flaky_decompose(path, **kw):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError(
                "ReadError: [WinError 10054] 远程主机强迫关闭了一个现有的连接。"
            )
        return _fake_segments()

    async def fake_pick(shots, top_n=3, **kw):
        return [{"idx": 11, "hook_strength": 9.0, "reason": "冲突爆发"}]

    monkeypatch.setattr(ex, "detect_scene_cuts", fake_cuts)
    monkeypatch.setattr(ex, "analyze_video", flaky_decompose)
    monkeypatch.setattr(ex, "select_start_shots", fake_pick)

    episodes = [
        {"id": 10 + i, "episode_no": i + 1, "oss_key": f"k{i+1}",
         "oss_url": f"u{i+1}", "duration": 20.0, "drama_name": "西瓜地"}
        for i in range(6)
    ]
    executor = make_highlight_plan_executor(
        runtime=_FakeRuntime(),
        highlight_asset_repo=_FakeAssetRepo(episodes),
        creative_repo=_FakeCreativeRepo(),
        oss_client=_FakeOSS(),
    )
    task = TaskEnvelope(
        task_type="highlight_plan",
        payload={"drama_name": "西瓜地", "num_candidates": 1,
                 "tenant_key": "flowcut", "session_key": "s"},
        stream=FlowcutTaskStream.HIGHLIGHT_PLAN,
        tenant_key="flowcut",
    )

    result = await executor(task)

    assert result.status == "succeeded"
    assert calls["n"] >= 3
