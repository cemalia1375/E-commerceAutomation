"""DecomposeVideoTool 改为 inline 工具后输出 navigate 字段。

工具不再触发新拆解任务（由上传端点入队），仅读 ref_video 拿 script_id 返回
跳工作台指令。
"""
from __future__ import annotations

import json

import pytest

from Flowcut.tools.decompose_video import DecomposeVideoTool


class _FakeRefVideoRepo:
    def __init__(self, records: dict[int, dict | None]) -> None:
        self._records = records

    async def get(self, ref_video_id: int) -> dict | None:
        return self._records.get(ref_video_id)


def _make_tool(records: dict[int, dict | None]) -> DecomposeVideoTool:
    return DecomposeVideoTool(
        runtime=object(),  # type: ignore[arg-type]
        ref_video_repo=_FakeRefVideoRepo(records),  # type: ignore[arg-type]
    )


@pytest.mark.unit
def test_metadata_is_inline_navigation() -> None:
    tool = _make_tool({})
    assert tool.execution_mode == "inline"
    assert tool.needs_followup is True
    assert tool.tool_category == "navigation"
    assert tool.read_only is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_returns_navigate_when_script_ready() -> None:
    tool = _make_tool({
        12: {
            "id": 12,
            "script_id": 88,
            "status": "PROCESSING",
            "tenant_key": "t_test",
        }
    })
    result = await tool.execute(ref_video_id=12)
    assert result.ok is True
    payload = json.loads(result.content)
    assert payload["ok"] is True
    assert payload["data"] == {
        "ref_video_id": 12,
        "script_id": 88,
        "status": "PROCESSING",
    }
    assert payload["navigate"] == {
        "route": "/workspace/:scriptId",
        "params": {"scriptId": 88},
        "mode": "push",
    }
    assert payload["ui_hint"]["render_as"] == "text"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_missing_ref_video_returns_error() -> None:
    tool = _make_tool({})
    result = await tool.execute(ref_video_id=999)
    assert result.ok is False
    payload = json.loads(result.content)
    assert payload["ok"] is False
    assert "999" in payload["error"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_missing_script_id_returns_error() -> None:
    tool = _make_tool({
        12: {
            "id": 12,
            "script_id": None,
            "status": "PROCESSING",
        }
    })
    result = await tool.execute(ref_video_id=12)
    assert result.ok is False
    payload = json.loads(result.content)
    assert payload["ok"] is False
    assert "尚未关联脚本" in payload["error"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_execute_ready_status_still_returns_navigate() -> None:
    """已完成拆镜的视频，依旧允许打开工作台查看结果。"""
    tool = _make_tool({
        20: {
            "id": 20,
            "script_id": 200,
            "status": "READY",
        }
    })
    result = await tool.execute(ref_video_id=20)
    assert result.ok is True
    payload = json.loads(result.content)
    assert payload["data"]["status"] == "READY"
    assert payload["navigate"]["params"]["scriptId"] == 200
