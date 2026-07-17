"""单元测试：gemini_video._parse_segments 解析新 schema (visual + copy)。

覆盖：
- 新 schema：visual + copy 字段同时存在
- 向后兼容：旧 schema 只给 content，copy 取空
- markdown 代码块容错
- category 非法值兜底为 "产品展示"
- 非 list / 非法 JSON 返回空
"""
from __future__ import annotations

import pytest

from Flowcut.services.gemini_video import _parse_segments


@pytest.mark.unit
def test_parse_new_schema_visual_and_copy():
    raw = """[
      {"start_time": 0.0, "end_time": 1.5,
       "visual": "真人特写笑脸", "copy": "今天给大家安利",
       "category": "真人口播"},
      {"start_time": 1.5, "end_time": 3.0,
       "visual": "产品特写", "copy": "",
       "category": "产品展示"}
    ]"""
    segs = _parse_segments(raw)
    assert len(segs) == 2
    assert segs[0]["visual"] == "真人特写笑脸"
    assert segs[0]["copy"] == "今天给大家安利"
    assert segs[0]["category"] == "真人口播"
    assert segs[1]["copy"] == ""
    assert segs[1]["visual"] == "产品特写"


@pytest.mark.unit
def test_parse_old_schema_content_fallback():
    """旧 schema 只有 content：content → visual，copy 留空。"""
    raw = """[
      {"start_time": 0.0, "end_time": 2.0,
       "content": "旧字段画面描述", "category": "产品展示"}
    ]"""
    segs = _parse_segments(raw)
    assert len(segs) == 1
    assert segs[0]["visual"] == "旧字段画面描述"
    assert segs[0]["copy"] == ""
    assert segs[0]["category"] == "产品展示"


@pytest.mark.unit
def test_parse_markdown_fenced_json():
    raw = """```json
    [{"start_time": 0, "end_time": 1, "visual": "v", "copy": "c", "category": "真人口播"}]
    ```"""
    segs = _parse_segments(raw)
    assert len(segs) == 1
    assert segs[0]["visual"] == "v"
    assert segs[0]["copy"] == "c"


@pytest.mark.unit
def test_parse_invalid_category_defaults():
    raw = """[{"start_time": 0, "end_time": 1, "visual": "x", "copy": "y", "category": "搞笑"}]"""
    segs = _parse_segments(raw)
    assert segs[0]["category"] == "产品展示"


@pytest.mark.unit
def test_parse_invalid_json_returns_empty():
    assert _parse_segments("not json") == []
    assert _parse_segments('{"not": "a list"}') == []


@pytest.mark.unit
def test_parse_skips_non_dict_items():
    raw = """[
      "string",
      {"start_time": 0, "end_time": 1, "visual": "v", "copy": "c", "category": "产品展示"},
      123
    ]"""
    segs = _parse_segments(raw)
    assert len(segs) == 1
    assert segs[0]["visual"] == "v"
