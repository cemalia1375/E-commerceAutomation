"""Tests for upload OSS key product partitioning."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_make_upload_oss_key_with_product() -> None:
    from Flowcut.api.routes.materials import _make_upload_oss_key
    key = _make_upload_oss_key("t_001", "雪莲洗液", "clip.mp4", ts=1700000000)
    assert key == "materials/t_001/雪莲洗液/uploads/1700000000_clip.mp4"


def test_make_upload_oss_key_empty_product_uses_通用() -> None:
    from Flowcut.api.routes.materials import _make_upload_oss_key
    key = _make_upload_oss_key("t_001", None, "clip.mp4", ts=1700000000)
    assert key == "materials/t_001/通用/uploads/1700000000_clip.mp4"


def test_make_upload_oss_key_empty_string_product_uses_通用() -> None:
    from Flowcut.api.routes.materials import _make_upload_oss_key
    key = _make_upload_oss_key("t_001", "", "clip.mp4", ts=1700000000)
    assert key == "materials/t_001/通用/uploads/1700000000_clip.mp4"


def test_make_upload_oss_key_sanitizes_path_traversal_in_product() -> None:
    from Flowcut.api.routes.materials import _make_upload_oss_key
    key = _make_upload_oss_key("t_001", "../../admin", "clip.mp4", ts=1700000000)
    assert ".." not in key
    assert "/" not in key.removeprefix("materials/t_001/").split("/uploads/")[0]


def test_make_upload_oss_key_sanitizes_slashes_in_filename() -> None:
    from Flowcut.api.routes.materials import _make_upload_oss_key
    key = _make_upload_oss_key("t_001", "雪莲洗液", "evil/path.mp4", ts=1700000000)
    assert key == "materials/t_001/雪莲洗液/uploads/1700000000_evil_path.mp4"
