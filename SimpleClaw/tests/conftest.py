import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--video",
        action="store",
        default=None,
        help="本地视频文件路径（test_scene_decompose_local 使用）",
    )
    parser.addoption(
        "--out-dir",
        action="store",
        default="/tmp/scene_decompose_out",
        help="切片输出目录，默认 /tmp/scene_decompose_out",
    )
