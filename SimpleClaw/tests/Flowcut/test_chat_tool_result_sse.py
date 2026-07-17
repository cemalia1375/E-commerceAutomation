"""POST /agent/chat SSE 流：当 ReactLoop yield ToolResultEvent 时，
   响应流应包含 `event:"tool_result"` 的 data 行，并带 tool_name + content。

策略：通过 monkeypatch 替换 container.sessions 的 get_or_create / get_lock /
      save_turn / set_turn_context，注入一个会 yield ToolResultEvent + DoneEvent
      的 fake loop。验证 SSE 输出 bytes 含 'tool_result' + tool_name。
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient

from Flowcut.api.deps import require_tenant
from simpleclaw.core.events import DoneEvent, TextEvent, ToolResultEvent


class _FakeLoop:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.tool_name = "upload_script"
        self.result = json.dumps({"script_id": 4242, "ok": True})

    async def run(self, message: str):
        yield TextEvent(token="hi")
        yield ToolResultEvent(
            tool_name=self.tool_name,
            result=self.result,
        )
        yield DoneEvent()


@pytest.fixture
def app_client(monkeypatch):
    from Flowcut.api.server import app

    app.dependency_overrides[require_tenant] = lambda: "t_chat_sse"
    with TestClient(app) as client:
        sessions = app.state.container.sessions

        fake_loop = _FakeLoop()
        fake_lock = asyncio.Lock()

        async def _get_or_create(session_key, tenant_key):  # type: ignore[no-untyped-def]
            return fake_loop

        def _get_lock(session_key):  # type: ignore[no-untyped-def]
            return fake_lock

        async def _save_turn(*args, **kwargs):  # type: ignore[no-untyped-def]
            return None

        def _set_turn_context(*args, **kwargs):  # type: ignore[no-untyped-def]
            return None

        monkeypatch.setattr(sessions, "get_or_create", _get_or_create)
        monkeypatch.setattr(sessions, "get_lock", _get_lock)
        monkeypatch.setattr(sessions, "save_turn", _save_turn)
        monkeypatch.setattr(sessions, "set_turn_context", _set_turn_context)
        client.fake_loop = fake_loop  # type: ignore[attr-defined]

        yield client
    app.dependency_overrides.pop(require_tenant, None)


@pytest.mark.integration
def test_chat_emits_tool_result_event(app_client: TestClient) -> None:
    with app_client.stream(
        "POST",
        "/agent/chat",
        json={
            "tenant_key": "t_chat_sse",
            "session_key": "s_chat_sse_01",
            "query": "hello",
        },
    ) as resp:
        assert resp.status_code == 200, resp.read().decode()
        body = b"".join(resp.iter_bytes()).decode("utf-8")

    # 提取所有 SSE data 行
    events = []
    for line in body.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))

    # 至少应有 chunk / tool_result / done
    event_names = [e["event"] for e in events]
    assert "tool_result" in event_names, event_names

    tool_result_payloads = [e for e in events if e["event"] == "tool_result"]
    assert len(tool_result_payloads) == 1
    payload = tool_result_payloads[0]["data"]
    assert payload["tool_name"] == "upload_script"
    assert payload["ok"] is True
    # content 应被 JSON 解析回 dict
    assert isinstance(payload["content"], dict)
    assert payload["content"]["script_id"] == 4242


@pytest.mark.integration
def test_chat_wraps_plain_text_tool_result(app_client: TestClient) -> None:
    app_client.fake_loop.tool_name = "check_task_status"  # type: ignore[attr-defined]
    app_client.fake_loop.result = "任务 task-1 正在执行中…"  # type: ignore[attr-defined]

    with app_client.stream(
        "POST",
        "/agent/chat",
        json={
            "tenant_key": "t_chat_sse",
            "session_key": "s_chat_sse_text",
            "query": "check",
        },
    ) as resp:
        assert resp.status_code == 200, resp.read().decode()
        body = b"".join(resp.iter_bytes()).decode("utf-8")

    events = [
        json.loads(line[6:])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]
    payload = [e for e in events if e["event"] == "tool_result"][0]["data"]
    assert payload["tool_name"] == "check_task_status"
    assert isinstance(payload["content"], dict)
    assert payload["content"]["ok"] is True
    assert payload["content"]["data"] == "任务 task-1 正在执行中…"
    assert payload["content"]["ui_hint"]["render_as"] == "none"


@pytest.mark.integration
def test_chat_wraps_null_tool_result(app_client: TestClient) -> None:
    app_client.fake_loop.tool_name = "check_task_status"  # type: ignore[attr-defined]
    app_client.fake_loop.result = "null"  # type: ignore[attr-defined]

    with app_client.stream(
        "POST",
        "/agent/chat",
        json={
            "tenant_key": "t_chat_sse",
            "session_key": "s_chat_sse_null",
            "query": "check",
        },
    ) as resp:
        assert resp.status_code == 200, resp.read().decode()
        body = b"".join(resp.iter_bytes()).decode("utf-8")

    events = [
        json.loads(line[6:])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]
    payload = [e for e in events if e["event"] == "tool_result"][0]["data"]
    assert payload["tool_name"] == "check_task_status"
    assert isinstance(payload["content"], dict)
    assert payload["content"]["ok"] is True
    assert payload["content"]["data"] is None
    assert payload["content"]["ui_hint"]["render_as"] == "none"
