from __future__ import annotations

import pytest

from Flowcut.services import gemini_video as gv


@pytest.mark.unit
def test_request_too_large_error_detection() -> None:
    assert gv._is_request_too_large(RuntimeError("ClientError: 413 None"))
    assert gv._is_request_too_large(RuntimeError("请求体过大，请减少内容后重试"))
    assert not gv._is_request_too_large(RuntimeError("ClientError: 503 UNAVAILABLE"))


@pytest.mark.unit
def test_base_url_prepare_uses_inline_rescue_compression(monkeypatch) -> None:
    calls: list[object] = []

    def fake_compress(src_path: str, dst_dir: str) -> str:
        calls.append(("default", src_path, dst_dir))
        return "default.mp4"

    def fake_rescue(src_path: str, dst_dir: str, limit_mb: float) -> str:
        calls.append(("rescue", src_path, dst_dir, limit_mb))
        return "compact.mp4"

    def fake_size(path: str) -> float:
        return {"default.mp4": 12.0, "compact.mp4": 5.0}[path]

    monkeypatch.setattr(gv, "_compress_video", fake_compress)
    monkeypatch.setattr(gv, "_compress_video_to_inline_limit", fake_rescue)
    monkeypatch.setattr(gv, "_file_size_mb", fake_size)

    path, size_mb = gv._prepare_video_for_request(
        "source.mp4",
        "tmp",
        inline_part_limit_mb=6.0,
        use_base_url=True,
    )

    assert path == "compact.mp4"
    assert size_mb == 5.0
    assert calls == [
        ("default", "source.mp4", "tmp"),
        ("rescue", "source.mp4", "tmp", 6.0),
    ]


@pytest.mark.unit
def test_direct_prepare_keeps_default_compression(monkeypatch) -> None:
    calls: list[str] = []

    def fake_compress(src_path: str, dst_dir: str) -> str:
        calls.append("default")
        return "default.mp4"

    def fail_rescue(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("direct mode should not force inline rescue")

    monkeypatch.setattr(gv, "_compress_video", fake_compress)
    monkeypatch.setattr(gv, "_compress_video_to_inline_limit", fail_rescue)
    monkeypatch.setattr(gv, "_file_size_mb", lambda path: 12.0)

    path, size_mb = gv._prepare_video_for_request(
        "source.mp4",
        "tmp",
        inline_part_limit_mb=6.0,
        use_base_url=False,
    )

    assert path == "default.mp4"
    assert size_mb == 12.0
    assert calls == ["default"]
