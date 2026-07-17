"""export_package executor: materials/ + manifest.json 包结构。

验证：
1. happy path: selections 跨段共享素材 → materials/ 下每个 mid 只一份
2. empty seg: manifest 跳过空段；script.md 写"使用素材：（未选）"
3. missing material: missing_materials.txt 出现，不阻塞 zip 生成
4. decomposed 源: zip 含 audio.mp3 + reference.mp4
5. uploaded 源: zip 不含 audio.mp3 / reference.mp4

不连 DB；用 FakeRepo + monkeypatch OSS 客户端。
"""
from __future__ import annotations

import asyncio
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from Flowcut.runtime.streams import FlowcutTaskStream
from simpleclaw.runtime.task_protocol import TaskEnvelope


# ---------- Fakes ---------- #


class FakeScriptRepo:
    def __init__(self, script: dict[str, Any]) -> None:
        self._script = script

    async def get(self, script_id: int) -> dict[str, Any] | None:
        if script_id != self._script["id"]:
            return None
        return dict(self._script)


class FakeMaterialRepo:
    def __init__(self, materials: dict[int, dict[str, Any]]) -> None:
        self._materials = materials

    async def get(self, mid: int) -> dict[str, Any] | None:
        return self._materials.get(mid)


class FakeRefVideoRepo:
    def __init__(self, ref: dict[str, Any] | None) -> None:
        self._ref = ref

    async def get(self, vid: int) -> dict[str, Any] | None:
        if self._ref is None:
            return None
        if vid != self._ref["id"]:
            return None
        return dict(self._ref)


class FakeOSSClient:
    """记录所有 download/upload，并把 download 写入假 bytes。

    download(key, local_path): 若 key 存在于 store → 写入对应 bytes；否则 raise。
    upload(local_path, key): 把本地文件 bytes 存进 uploaded[key]。
    """

    def __init__(self, store: dict[str, bytes]) -> None:
        self.store = store
        self.downloaded: list[tuple[str, str]] = []
        self.uploaded: dict[str, bytes] = {}

    def download(self, key: str, local_path: str) -> None:
        if key not in self.store:
            raise FileNotFoundError(f"oss key not found: {key}")
        Path(local_path).write_bytes(self.store[key])
        self.downloaded.append((key, local_path))

    def upload(self, local_path: str, key: str) -> None:
        self.uploaded[key] = Path(local_path).read_bytes()

    def presigned_get_url(self, key: str, expires: int = 3600) -> str:
        return f"https://fake-oss/{key}?exp={expires}"


# ---------- Helpers ---------- #


def _make_envelope(script_id: int, selections: dict[str, list[int]]) -> TaskEnvelope:
    return TaskEnvelope(
        task_type="export_package",
        payload={
            "script_id": script_id,
            "selections": selections,
            # 即使 route 同时塞了 material_ids 兼容字段，executor 也应忽略
            "material_ids": [999_999],
        },
        stream=FlowcutTaskStream.EXPORT_PACKAGE,
        tenant_key="t_export",
    )


def _extract_zip(zip_bytes: bytes) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for name in zf.namelist():
            out[name] = zf.read(name)
    return out


def _run(coro):
    return asyncio.run(coro)


# 仅有一个 zip 输出 key；helper 返回它的内容
def _get_uploaded_zip(oss: FakeOSSClient) -> bytes:
    keys = [k for k in oss.uploaded if k.endswith(".zip")]
    assert len(keys) == 1, f"expected 1 zip upload, got {keys}"
    return oss.uploaded[keys[0]]


# ---------- Fixtures ---------- #


@pytest.fixture
def make_executor():
    from Flowcut.runtime.executors import make_export_package_executor

    def _build(
        *,
        script: dict[str, Any],
        materials: dict[int, dict[str, Any]],
        ref_video: dict[str, Any] | None,
        oss_store: dict[str, bytes],
    ) -> tuple[Any, FakeOSSClient]:
        oss = FakeOSSClient(oss_store)
        executor = make_export_package_executor(
            script_repo=FakeScriptRepo(script),
            material_repo=FakeMaterialRepo(materials),
            ref_video_repo=FakeRefVideoRepo(ref_video),
            oss_client=oss,
        )
        return executor, oss

    return _build


# ---------- Tests ---------- #


@pytest.mark.integration
def test_happy_path_dedupes_materials_and_writes_manifest(make_executor):
    script = {
        "id": 1,
        "source": "uploaded",
        "reference_video_id": None,
        "segments": [
            {"idx": 0, "visual": "v0", "copy": "c0", "start_time": 0, "end_time": 1.5},
            {"idx": 1, "visual": "v1", "copy": "c1", "start_time": 1.5, "end_time": 3.0},
            {"idx": 2, "visual": "v2", "copy": "c2", "start_time": 3.0, "end_time": 4.0},
        ],
    }
    materials = {
        5: {"id": 5, "oss_key": "m/5.mp4"},
        7: {"id": 7, "oss_key": "m/7.mp4"},
        12: {"id": 12, "oss_key": "m/12.mp4"},
    }
    oss_store = {
        "m/5.mp4": b"bytes-5",
        "m/7.mp4": b"bytes-7",
        "m/12.mp4": b"bytes-12",
    }
    executor, oss = make_executor(
        script=script, materials=materials, ref_video=None, oss_store=oss_store,
    )
    env = _make_envelope(1, {"0": [12, 7], "1": [12], "2": [5]})

    result = _run(executor(env))
    assert result.status == "succeeded", getattr(result, "error", result.summary)

    files = _extract_zip(_get_uploaded_zip(oss))

    # materials/ 每个 mid 只一份
    material_files = sorted(n for n in files if n.startswith("materials/"))
    assert material_files == ["materials/12.mp4", "materials/5.mp4", "materials/7.mp4"]
    # 12 只下载一次（去重）
    download_keys = [k for k, _ in oss.downloaded]
    assert download_keys.count("m/12.mp4") == 1

    # manifest.json：按 seg_idx 升序，包含全部非空段
    manifest = json.loads(files["manifest.json"])
    assert manifest == [
        {"seg_idx": 0, "material_ids": [12, 7]},
        {"seg_idx": 1, "material_ids": [12]},
        {"seg_idx": 2, "material_ids": [5]},
    ]

    # script.md：每段含"使用素材"行
    md = files["script.md"].decode("utf-8")
    assert "使用素材：12, 7" in md
    assert "使用素材：12" in md
    assert "使用素材：5" in md

    # script.json 仍存在
    assert "script.json" in files
    # 旧 clips/ 命名被删除
    assert not any(n.startswith("clips/") for n in files)


@pytest.mark.integration
def test_empty_segment_skipped_in_manifest_and_marked_in_md(make_executor):
    script = {
        "id": 2,
        "source": "uploaded",
        "reference_video_id": None,
        "segments": [
            {"idx": 0, "visual": "v0", "copy": "c0"},
            {"idx": 1, "visual": "v1", "copy": "c1"},
        ],
    }
    materials = {12: {"id": 12, "oss_key": "m/12.mp4"}}
    oss_store = {"m/12.mp4": b"bytes-12"}
    executor, oss = make_executor(
        script=script, materials=materials, ref_video=None, oss_store=oss_store,
    )
    env = _make_envelope(2, {"0": [12], "1": []})

    result = _run(executor(env))
    assert result.status == "succeeded", getattr(result, "error", result.summary)

    files = _extract_zip(_get_uploaded_zip(oss))

    manifest = json.loads(files["manifest.json"])
    assert manifest == [{"seg_idx": 0, "material_ids": [12]}]

    md = files["script.md"].decode("utf-8")
    assert "使用素材：12" in md
    assert "使用素材：（未选）" in md


@pytest.mark.integration
def test_missing_material_recorded_but_does_not_block_zip(make_executor):
    script = {
        "id": 3,
        "source": "uploaded",
        "reference_video_id": None,
        "segments": [
            {"idx": 0, "visual": "v0", "copy": "c0"},
        ],
    }
    # mid 7 没在 materials 里 → 缺失
    materials = {5: {"id": 5, "oss_key": "m/5.mp4"}}
    oss_store = {"m/5.mp4": b"bytes-5"}
    executor, oss = make_executor(
        script=script, materials=materials, ref_video=None, oss_store=oss_store,
    )
    env = _make_envelope(3, {"0": [5, 7]})

    result = _run(executor(env))
    assert result.status == "succeeded", getattr(result, "error", result.summary)

    files = _extract_zip(_get_uploaded_zip(oss))

    assert "missing_materials.txt" in files
    missing_text = files["missing_materials.txt"].decode("utf-8")
    assert "7" in missing_text

    # 已有的 5 仍要打包
    assert "materials/5.mp4" in files
    assert "materials/7.mp4" not in files

    # manifest 仍按 selections 原始顺序保留两个 id（不剔除缺失）
    manifest = json.loads(files["manifest.json"])
    assert manifest == [{"seg_idx": 0, "material_ids": [5, 7]}]


@pytest.mark.integration
def test_decomposed_source_includes_audio_and_reference(make_executor):
    script = {
        "id": 4,
        "source": "decomposed",
        "reference_video_id": 99,
        "segments": [{"idx": 0, "visual": "v", "copy": "c"}],
    }
    materials = {1: {"id": 1, "oss_key": "m/1.mp4"}}
    ref_video = {
        "id": 99,
        "audio_oss_key": "ref/99/audio.mp3",
        "oss_key": "ref/99/video.mp4",
    }
    oss_store = {
        "m/1.mp4": b"m1",
        "ref/99/audio.mp3": b"audio-bytes",
        "ref/99/video.mp4": b"video-bytes",
    }
    executor, oss = make_executor(
        script=script, materials=materials, ref_video=ref_video, oss_store=oss_store,
    )
    env = _make_envelope(4, {"0": [1]})

    result = _run(executor(env))
    assert result.status == "succeeded", getattr(result, "error", result.summary)

    files = _extract_zip(_get_uploaded_zip(oss))
    assert files["audio.mp3"] == b"audio-bytes"
    assert files["reference.mp4"] == b"video-bytes"


@pytest.mark.integration
def test_uploaded_source_excludes_audio_and_reference(make_executor):
    script = {
        "id": 5,
        "source": "uploaded",
        "reference_video_id": None,
        "segments": [{"idx": 0, "visual": "v", "copy": "c"}],
    }
    materials = {1: {"id": 1, "oss_key": "m/1.mp4"}}
    oss_store = {"m/1.mp4": b"m1"}
    executor, oss = make_executor(
        script=script, materials=materials, ref_video=None, oss_store=oss_store,
    )
    env = _make_envelope(5, {"0": [1]})

    result = _run(executor(env))
    assert result.status == "succeeded", getattr(result, "error", result.summary)

    files = _extract_zip(_get_uploaded_zip(oss))
    assert "audio.mp3" not in files
    assert "reference.mp4" not in files
