"""Tests for zip upload parser."""
from __future__ import annotations

import io
import zipfile
import pytest

pytestmark = pytest.mark.unit


def _make_zip(entries: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in entries:
            z.writestr(name, content)
    return buf.getvalue()


def test_parse_zip_two_level_paths() -> None:
    from Flowcut.services.zip_parser import parse_zip_structure

    zip_bytes = _make_zip([
        ("雪莲洗液/医生/clip_01.mp4", b"fake"),
        ("雪莲洗液/医生/clip_02.mp4", b"fake"),
        ("雪莲洗液/药材/clip_03.mp4", b"fake"),
    ])
    existing = {"雪莲洗液": {"医生"}}

    preview = parse_zip_structure(zip_bytes, existing_tree=existing)

    assert preview == [
        {
            "product": "雪莲洗液",
            "scene_role": "医生",
            "files": ["clip_01.mp4", "clip_02.mp4"],
            "status": "existing",
        },
        {
            "product": "雪莲洗液",
            "scene_role": "药材",
            "files": ["clip_03.mp4"],
            "status": "new",
        },
    ]


def test_parse_zip_single_level_treated_as_product_only() -> None:
    from Flowcut.services.zip_parser import parse_zip_structure

    zip_bytes = _make_zip([("通用/clip.mp4", b"fake")])
    preview = parse_zip_structure(zip_bytes, existing_tree={"通用": set()})

    assert preview == [{
        "product": "通用",
        "scene_role": None,
        "files": ["clip.mp4"],
        "status": "existing",
    }]


def test_parse_zip_non_video_marked_ignored() -> None:
    from Flowcut.services.zip_parser import parse_zip_structure

    zip_bytes = _make_zip([
        ("雪莲洗液/医生/clip.mp4", b"fake"),
        ("readme.txt", b"hi"),
        ("a/b/c/too_deep.mp4", b"fake"),
    ])
    preview = parse_zip_structure(zip_bytes, existing_tree={})

    ignored = [p for p in preview if p["status"] == "ignored"]
    assert len(ignored) == 1
    assert set(ignored[0]["files"]) == {"readme.txt", "a/b/c/too_deep.mp4"}


def _make_gbk_zip(path: str, content: bytes) -> bytes:
    """Build a zip that stores path as raw GBK bytes with no UTF-8 flag.

    Python's zipfile always sets the UTF-8 flag for non-ASCII filenames, so we
    write with an ASCII placeholder of the same byte length and then patch the
    binary to replace the placeholder with the real GBK bytes.
    """
    name_gbk = path.encode("gbk")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        info = zipfile.ZipInfo(filename="X" * len(name_gbk))
        info.flag_bits = 0  # no UTF-8 flag
        z.writestr(info, content)
    data = bytearray(buf.getvalue())
    placeholder = b"X" * len(name_gbk)
    # Replace all occurrences (local header + central dir header both store the name)
    idx = 0
    while True:
        pos = data.find(placeholder, idx)
        if pos == -1:
            break
        data[pos : pos + len(placeholder)] = name_gbk
        idx = pos + len(name_gbk)
    return bytes(data)


def test_parse_zip_chinese_filename_gbk_decoded() -> None:
    """Filenames encoded as GBK (no UTF-8 flag) should decode correctly."""
    from Flowcut.services.zip_parser import parse_zip_structure

    zip_bytes = _make_gbk_zip("雪莲洗液/医生/视频.mp4", b"fake")
    preview = parse_zip_structure(zip_bytes, existing_tree={})

    assert len(preview) == 1
    assert preview[0]["product"] == "雪莲洗液"
    assert preview[0]["scene_role"] == "医生"
    assert preview[0]["files"] == ["视频.mp4"]


def test_parse_zip_utf8_filename_passthrough() -> None:
    """Filenames with UTF-8 flag bit set should pass through unchanged."""
    from Flowcut.services.zip_parser import parse_zip_structure
    # _make_zip uses writestr which sets the UTF-8 flag by default
    zip_bytes = _make_zip([("雪莲洗液/医生/视频.mp4", b"fake")])
    preview = parse_zip_structure(zip_bytes, existing_tree={})
    assert preview[0]["product"] == "雪莲洗液"
    assert preview[0]["files"] == ["视频.mp4"]


def test_parse_zip_skips_mac_appledouble() -> None:
    """._<name> AppleDouble files should be silently skipped (not in preview, not in ignored)."""
    from Flowcut.services.zip_parser import parse_zip_structure
    zip_bytes = _make_zip([
        ("雪莲洗液/产品展示/0424洗液-8.mp4", b"fake_video"),
        ("雪莲洗液/产品展示/._0424洗液-8.mp4", b"fake_metadata"),
        ("雪莲洗液/产品展示/.DS_Store", b"finder_metadata"),
    ])
    preview = parse_zip_structure(zip_bytes, existing_tree={})

    # Should be exactly ONE group with the real video; no "ignored" entry for metadata
    assert preview == [{
        "product": "雪莲洗液",
        "scene_role": "产品展示",
        "files": ["0424洗液-8.mp4"],
        "status": "new",
    }]


def test_parse_zip_skips_macosx_directory() -> None:
    """__MACOSX/ tree should be silently skipped."""
    from Flowcut.services.zip_parser import parse_zip_structure
    zip_bytes = _make_zip([
        ("雪莲洗液/产品展示/video.mp4", b"fake"),
        ("__MACOSX/雪莲洗液/产品展示/._video.mp4", b"meta"),
    ])
    preview = parse_zip_structure(zip_bytes, existing_tree={})

    assert preview == [{
        "product": "雪莲洗液",
        "scene_role": "产品展示",
        "files": ["video.mp4"],
        "status": "new",
    }]
