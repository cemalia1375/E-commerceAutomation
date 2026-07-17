"""GET /flowcut/tasks/{task_id} 应返回 details 字段（来自 result_details_json）。

通过 monkeypatch 替换 container.task_repo.find_by_task_id，避免引入 DB 依赖。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_client(monkeypatch):
    from Flowcut.api.server import app

    with TestClient(app) as client:
        task_repo = app.state.container.task_repo

        async def _find_by_task_id(task_id: str):
            if task_id == "task-with-details":
                return {
                    "task_id": task_id,
                    "task_type": "SCENE_DECOMPOSE",
                    "status": "succeeded",
                    "result_url": None,
                    "result_details": {"script_id": 777, "ref_video_id": 12},
                    "last_error": None,
                    "created_at": "2026-05-27 00:00:00",
                    "updated_at": "2026-05-27 00:01:00",
                }
            if task_id == "task-no-details":
                return {
                    "task_id": task_id,
                    "task_type": "EXPORT_PACKAGE",
                    "status": "running",
                    "result_url": None,
                    "result_details": {},
                    "last_error": None,
                    "created_at": "2026-05-27 00:00:00",
                    "updated_at": "2026-05-27 00:00:30",
                }
            return None

        monkeypatch.setattr(task_repo, "find_by_task_id", _find_by_task_id)
        yield client


@pytest.mark.integration
def test_task_route_returns_details_dict(app_client: TestClient) -> None:
    resp = app_client.get("/flowcut/tasks/task-with-details")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["task_id"] == "task-with-details"
    assert body["status"] == "succeeded"
    assert body["details"] == {"script_id": 777, "ref_video_id": 12}


@pytest.mark.integration
def test_task_route_returns_empty_details_when_none(app_client: TestClient) -> None:
    resp = app_client.get("/flowcut/tasks/task-no-details")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["details"] == {}


@pytest.mark.integration
def test_task_route_returns_404_when_missing(app_client: TestClient) -> None:
    resp = app_client.get("/flowcut/tasks/nope")
    assert resp.status_code == 404
