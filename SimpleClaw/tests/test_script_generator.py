"""tests/test_script_generator.py"""
import pytest
from Flowcut.services.script_generator import _parse_script_response


def _make_raw(role: str, title: str, segments: list[dict]) -> str:
    import json
    return json.dumps({"role": role, "title": title, "segments": segments}, ensure_ascii=False)


_SAMPLE_SEGMENTS = [
    {"segment_idx": 0, "start_time": 0.0, "end_time": 3.96,
     "visual_guide": "特写产品", "copy_text": "你是不是也失眠？"},
    {"segment_idx": 1, "start_time": 3.96, "end_time": 8.2,
     "visual_guide": "主播出镜", "copy_text": "用了这个，第一晚就睡着了"},
]


def test_parse_valid_json():
    raw = _make_raw("痛点型", "失眠的你", _SAMPLE_SEGMENTS)
    result = _parse_script_response(raw, role="痛点型")
    assert result is not None
    assert result["role"] == "痛点型"
    assert result["title"] == "失眠的你"
    assert len(result["segments"]) == 2
    assert result["segments"][0]["copy_text"] == "你是不是也失眠？"


def test_parse_markdown_fence():
    import json
    inner = json.dumps({"role": "场景型", "title": "T", "segments": _SAMPLE_SEGMENTS})
    raw = f"```json\n{inner}\n```"
    result = _parse_script_response(raw, role="场景型")
    assert result is not None
    assert result["role"] == "场景型"


def test_parse_invalid_json_returns_none():
    result = _parse_script_response("not json at all", role="对比型")
    assert result is None


def test_parse_missing_segments_returns_none():
    import json
    raw = json.dumps({"role": "口碑型", "title": "T"})
    result = _parse_script_response(raw, role="口碑型")
    assert result is None


def test_parse_wrong_role_corrected():
    """模型偶尔返回错误的 role 字段，应被覆盖为传入的 role。"""
    raw = _make_raw("随便什么", "T", _SAMPLE_SEGMENTS)
    result = _parse_script_response(raw, role="痛点型")
    assert result["role"] == "痛点型"


def test_parse_segment_missing_copy_text():
    """copy_text 缺失时段落仍解析，copy_text 默认空串。"""
    import json
    segs = [{"segment_idx": 0, "start_time": 0.0, "end_time": 3.0, "visual_guide": "x"}]
    raw = json.dumps({"role": "场景型", "title": "T", "segments": segs})
    result = _parse_script_response(raw, role="场景型")
    assert result is not None
    assert result["segments"][0]["copy_text"] == ""
