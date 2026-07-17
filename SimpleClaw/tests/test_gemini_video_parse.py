"""Unit tests for gemini_video._parse_segments — no network calls."""
import json
import pytest
from Flowcut.services.gemini_video import _parse_segments


def test_parse_valid_json_array():
    raw = json.dumps([
        {"start_time": 0, "end_time": 4, "content": "开场"},
        {"start_time": 4, "end_time": 8, "content": "产品展示"},
    ])
    result = _parse_segments(raw)
    assert len(result) == 2
    assert result[0]["start_time"] == 0.0
    assert result[1]["content"] == "产品展示"


def test_parse_json_with_markdown_fence():
    raw = "```json\n[{\"start_time\": 0, \"end_time\": 3, \"content\": \"A\"}]\n```"
    result = _parse_segments(raw)
    assert len(result) == 1
    assert result[0]["end_time"] == 3.0


def test_parse_missing_end_time_defaults_to_start_plus_one():
    raw = json.dumps([{"start_time": 5, "content": "B"}])
    result = _parse_segments(raw)
    assert result[0]["end_time"] == pytest.approx(6.0)


def test_parse_empty_list_returns_empty():
    result = _parse_segments("[]")
    assert result == []


def test_parse_invalid_json_returns_empty():
    result = _parse_segments("not json at all")
    assert result == []


def test_parse_string_times_converted_to_float():
    raw = json.dumps([{"start_time": "1", "end_time": "5", "content": "C"}])
    result = _parse_segments(raw)
    assert result[0]["start_time"] == pytest.approx(1.0)
    assert result[0]["end_time"] == pytest.approx(5.0)
