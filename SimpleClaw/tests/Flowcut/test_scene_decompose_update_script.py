"""scene_decompose executor 走 UPDATE（而非 INSERT）预建 script 的集成测试。

验证：
- 上层（POST /upload 或 /decompose）已预建 fc_script(PROCESSING) 并在 ref_video.script_id 回填。
- executor 拿到 ref_video.script_id，应 UPDATE 那条 script 的 segments + status=DRAFT，
  而不是再 INSERT 出一条新的。
- 失败路径（Gemini 抛错）：script.status=FAILED，ref_video.status=FAILED。

外部依赖（Gemini / PySceneDetect / ASR / OSS）全部 monkeypatch。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import pytest_asyncio

from Flowcut.runtime import executors as executors_mod
from Flowcut.runtime.executors import make_scene_decompose_executor
from Flowcut.runtime.streams import FlowcutTaskStream
from Flowcut.storage.database import Database, ensure_schema
from Flowcut.storage.reference_video_repo import ReferenceVideoRepository
from Flowcut.storage.material_repo import MaterialRepository
from Flowcut.storage.script_repo import ScriptRepository
from simpleclaw.runtime.task_protocol import TaskEnvelope


class _StubEmbedding:
    async def embed(self, text: str) -> list[float]:
        return [0.0]


class _StubVectorStore:
    async def upsert(self, *args, **kwargs):
        return None

    async def search(self, *args, **kwargs):
        return []


@pytest_asyncio.fixture
async def db():
    d = Database(
        host=os.environ["MYSQL_HOST"],
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        db=os.environ["MYSQL_DB"],
    )
    await d.connect()
    await ensure_schema(d)
    yield d
    await d.close()


def _stub_external_deps(monkeypatch, *, gemini_segments=None, gemini_raises=False):
    """统一 mock 掉所有外部依赖。"""
    # OSS download —— 写一个非空 fake mp4
    class _FakeOSS:
        def download(self, key, path):
            Path(path).write_bytes(b"fake-mp4-bytes" * 32)

        def upload(self, local_path, key):
            return None

    monkeypatch.setattr(executors_mod, "build_oss_client", lambda: _FakeOSS())

    # Gemini analyze_video — 新 schema：visual + copy 由 Gemini 直接给出
    async def _fake_analyze(video_path):
        if gemini_raises:
            raise RuntimeError("gemini exploded")
        return gemini_segments or [
            {"start_time": 0.0, "end_time": 1.5,
             "visual": "镜头一：真人特写", "copy": "大家好今天给你们安利",
             "category": "真人口播"},
            {"start_time": 1.5, "end_time": 3.0,
             "visual": "镜头二：产品特写", "copy": "",
             "category": "产品展示"},
        ]

    monkeypatch.setattr(executors_mod, "analyze_video", _fake_analyze)

    # PySceneDetect cuts
    async def _fake_cuts(video_path):
        return []

    monkeypatch.setattr(executors_mod, "detect_scene_cuts", _fake_cuts)

    # align_timestamps —— 直接返回 segments
    monkeypatch.setattr(
        executors_mod, "align_timestamps", lambda segs, cuts: list(segs)
    )

    # 音轨 ffmpeg 子进程（抽 MP3）—— 让它"成功"但不真跑
    import subprocess as _subprocess

    class _FakeCompleted:
        returncode = 0

    def _fake_run(cmd, **kwargs):
        # 在输出路径写个空文件，让后面的 upload 不报错
        try:
            out = cmd[-1]
            Path(out).write_bytes(b"")
        except Exception:
            pass
        return _FakeCompleted()

    monkeypatch.setattr(_subprocess, "run", _fake_run)

    # _extract_audio_ffmpeg —— 写一个 fake wav
    def _fake_extract(video_path, wav_path):
        Path(wav_path).write_bytes(b"\x00" * 64)

    monkeypatch.setattr(executors_mod, "_extract_audio_ffmpeg", _fake_extract)

    # ASR —— 返回空 words
    async def _fake_asr(wav_path):
        return ("", [])

    monkeypatch.setattr(
        executors_mod, "_call_asr_websocket_with_words", _fake_asr
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scene_decompose_updates_prebuilt_script(db, monkeypatch):
    """上层已预建 script(PROCESSING)，executor 应 UPDATE 而非 INSERT。"""
    _stub_external_deps(monkeypatch)

    script_repo = ScriptRepository(db)
    ref_video_repo = ReferenceVideoRepository(db)
    material_repo = MaterialRepository(db)
    embedding = _StubEmbedding()
    vector_store = _StubVectorStore()

    tenant_key = "t_decompose_update_ok"

    # 1) 造 ref_video
    rv = await ref_video_repo.create(
        tenant_key=tenant_key,
        oss_key=f"uploads/{tenant_key}/x.mp4",
        oss_url="",
        name="x.mp4",
        duration=3.0,
        file_size=1024,
        product="洗发水Z",
    )
    ref_video_id = rv["id"]

    # 2) 预建 script(PROCESSING)
    prebuilt = await script_repo.create(
        tenant_key=tenant_key,
        source="decomposed",
        reference_video_id=ref_video_id,
        product="洗发水Z",
        segments=[],
        status="PROCESSING",
    )
    prebuilt_id = prebuilt["id"]
    await ref_video_repo.set_script_id(ref_video_id, prebuilt_id)

    # 3) 调 executor
    executor = make_scene_decompose_executor(
        material_repo=material_repo,
        ref_video_repo=ref_video_repo,
        embedding_service=embedding,
        vector_store=vector_store,
        script_repo=script_repo,
    )
    envelope = TaskEnvelope(
        task_type="scene_decompose",
        stream=FlowcutTaskStream.SCENE_DECOMPOSE,
        tenant_key=tenant_key,
        payload={
            "ref_video_id": ref_video_id,
            "oss_key": rv["oss_key"],
            "oss_url": "",
            "tenant_key": tenant_key,
        },
    )
    result = await executor(envelope)
    assert result.status == "succeeded", result

    # 4) 断言：关联该 ref_video 的 script 只有那一条 prebuilt_id（未新增）
    scripts = await script_repo.list_by_tenant(tenant_key)
    related = [s for s in scripts if s["reference_video_id"] == ref_video_id]
    assert len(related) == 1
    only = related[0]
    assert only["id"] == prebuilt_id
    assert only["status"] == "DRAFT"
    assert len(only["segments"]) == 2
    # copy 由 Gemini 多模态直接产出（非 ASR 切片）
    segs_by_idx = {s["idx"]: s for s in only["segments"]}
    assert segs_by_idx[0]["copy"] == "大家好今天给你们安利"
    assert segs_by_idx[0]["visual"] == "镜头一：真人特写"
    assert segs_by_idx[1]["copy"] == ""
    assert segs_by_idx[1]["visual"] == "镜头二：产品特写"

    # 5) ref_video.script_id 没变 + status=READY
    rv_after = await ref_video_repo.get(ref_video_id)
    assert rv_after["script_id"] == prebuilt_id
    assert rv_after["status"] == "READY"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scene_decompose_marks_script_failed_on_gemini_error(db, monkeypatch):
    """Gemini 抛错时，预建的 script 应标记为 FAILED。"""
    _stub_external_deps(monkeypatch, gemini_raises=True)

    script_repo = ScriptRepository(db)
    ref_video_repo = ReferenceVideoRepository(db)
    material_repo = MaterialRepository(db)
    embedding = _StubEmbedding()
    vector_store = _StubVectorStore()

    tenant_key = "t_decompose_update_fail"

    rv = await ref_video_repo.create(
        tenant_key=tenant_key,
        oss_key=f"uploads/{tenant_key}/y.mp4",
        oss_url="",
        name="y.mp4",
        duration=3.0,
        file_size=1024,
    )
    ref_video_id = rv["id"]
    prebuilt = await script_repo.create(
        tenant_key=tenant_key,
        source="decomposed",
        reference_video_id=ref_video_id,
        product=None,
        segments=[],
        status="PROCESSING",
    )
    prebuilt_id = prebuilt["id"]
    await ref_video_repo.set_script_id(ref_video_id, prebuilt_id)

    executor = make_scene_decompose_executor(
        material_repo=material_repo,
        ref_video_repo=ref_video_repo,
        embedding_service=embedding,
        vector_store=vector_store,
        script_repo=script_repo,
    )
    envelope = TaskEnvelope(
        task_type="scene_decompose",
        stream=FlowcutTaskStream.SCENE_DECOMPOSE,
        tenant_key=tenant_key,
        payload={
            "ref_video_id": ref_video_id,
            "oss_key": rv["oss_key"],
            "oss_url": "",
            "tenant_key": tenant_key,
        },
    )
    result = await executor(envelope)
    assert result.status == "failed"

    fetched = await script_repo.get(prebuilt_id)
    assert fetched is not None
    assert fetched["status"] == "FAILED"

    rv_after = await ref_video_repo.get(ref_video_id)
    assert rv_after["status"] == "FAILED"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_scene_decompose_fallback_creates_script_if_no_prebuilt(db, monkeypatch):
    """老数据兼容：ref_video.script_id 为空时走 fallback INSERT 路径。"""
    _stub_external_deps(monkeypatch)

    script_repo = ScriptRepository(db)
    ref_video_repo = ReferenceVideoRepository(db)
    material_repo = MaterialRepository(db)
    embedding = _StubEmbedding()
    vector_store = _StubVectorStore()

    tenant_key = "t_decompose_fallback_create"

    rv = await ref_video_repo.create(
        tenant_key=tenant_key,
        oss_key=f"uploads/{tenant_key}/z.mp4",
        oss_url="",
        name="z.mp4",
        duration=3.0,
        file_size=1024,
    )
    ref_video_id = rv["id"]
    # 故意不预建 script，也不 set_script_id

    executor = make_scene_decompose_executor(
        material_repo=material_repo,
        ref_video_repo=ref_video_repo,
        embedding_service=embedding,
        vector_store=vector_store,
        script_repo=script_repo,
    )
    envelope = TaskEnvelope(
        task_type="scene_decompose",
        stream=FlowcutTaskStream.SCENE_DECOMPOSE,
        tenant_key=tenant_key,
        payload={
            "ref_video_id": ref_video_id,
            "oss_key": rv["oss_key"],
            "oss_url": "",
            "tenant_key": tenant_key,
        },
    )
    result = await executor(envelope)
    assert result.status == "succeeded", result

    scripts = await script_repo.list_by_tenant(tenant_key)
    related = [s for s in scripts if s["reference_video_id"] == ref_video_id]
    assert len(related) == 1
    assert related[0]["status"] == "DRAFT"
    assert len(related[0]["segments"]) == 2

    rv_after = await ref_video_repo.get(ref_video_id)
    assert rv_after["status"] == "READY"
    assert rv_after["script_id"] == related[0]["id"]
