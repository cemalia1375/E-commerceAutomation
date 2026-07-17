"""tests/test_generate_scripts_tool.py"""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from Flowcut.tools.generate_scripts import GenerateScriptsTool


def _make_tool():
    repo = MagicMock()
    return GenerateScriptsTool(material_repo=repo), repo


def _sample_scene_data():
    return [
        {"start_time": 0.0, "end_time": 3.96, "content": "开场"},
        {"start_time": 3.96, "end_time": 8.2, "content": "主播出镜"},
    ]


def _make_script(role: str):
    return {
        "role": role, "title": f"{role}标题",
        "segments": [
            {"segment_idx": 0, "start_time": 0.0, "end_time": 3.96,
             "visual_guide": "x", "copy_text": "y"},
        ],
    }


async def _async_return(value):
    return value


@pytest.mark.asyncio
async def test_execute_material_not_found():
    tool, repo = _make_tool()
    repo.get = AsyncMock(return_value=None)
    result = await tool.execute(material_id=99)
    assert result.ok is False
    assert "不存在" in result.content


@pytest.mark.asyncio
async def test_execute_scene_data_empty():
    tool, repo = _make_tool()
    repo.get = AsyncMock(return_value={"id": 1, "scene_data_json": None})
    result = await tool.execute(material_id=1)
    assert result.ok is False
    assert "拆镜" in result.content


@pytest.mark.asyncio
async def test_execute_all_success():
    tool, repo = _make_tool()
    scene_data = _sample_scene_data()
    repo.get = AsyncMock(return_value={
        "id": 1,
        "scene_data_json": json.dumps(scene_data),
    })
    roles = ["痛点型", "场景型", "对比型", "口碑型"]
    side_effects = [_make_script(r) for r in roles]

    with patch("Flowcut.tools.generate_scripts.generate_for_role") as mock_gen:
        mock_gen.side_effect = [
            _async_return(s) for s in side_effects
        ]
        result = await tool.execute(material_id=1)

    assert result.ok is True
    scripts = json.loads(result.content)
    assert len(scripts) == 4
    assert {s["role"] for s in scripts} == set(roles)


@pytest.mark.asyncio
async def test_execute_partial_failure():
    tool, repo = _make_tool()
    scene_data = _sample_scene_data()
    repo.get = AsyncMock(return_value={
        "id": 1,
        "scene_data_json": json.dumps(scene_data),
    })

    with patch("Flowcut.tools.generate_scripts.generate_for_role") as mock_gen:
        mock_gen.side_effect = [
            _async_return(_make_script("痛点型")),
            _async_return(None),   # 场景型 失败
            _async_return(_make_script("对比型")),
            _async_return(None),   # 口碑型 失败
        ]
        result = await tool.execute(material_id=1)

    assert result.ok is True
    scripts = json.loads(result.content.split("\n")[0])
    assert len(scripts) == 2
    assert "场景型" in result.content or "口碑型" in result.content


@pytest.mark.asyncio
async def test_execute_all_fail():
    tool, repo = _make_tool()
    scene_data = _sample_scene_data()
    repo.get = AsyncMock(return_value={
        "id": 1,
        "scene_data_json": json.dumps(scene_data),
    })

    with patch("Flowcut.tools.generate_scripts.generate_for_role") as mock_gen:
        mock_gen.side_effect = [_async_return(None)] * 4
        result = await tool.execute(material_id=1)

    assert result.ok is False
