"""POST /creatives/upload — 直接上传成片视频，落 fc_creative READY。

成片库新增的「↑ 上传成片」按钮调用。
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient


class _StubOSSClient:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, str]] = []

    def upload(self, local_path: str, oss_key: str) -> None:
        self.uploads.append((local_path, oss_key))


class _FakeCreativeRepo:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.status_updates: list[dict] = []
        self._next_id = 1
        self._store: dict[int, dict] = {}

    async def create(self, *, tenant_key, session_key, script_id=None) -> dict:
        row = {
            "id": self._next_id,
            "tenant_key": tenant_key,
            "session_key": session_key,
            "script_id": script_id,
            "status": "PENDING",
            "label": "NORMAL",
            "oss_key": None,
            "oss_url": None,
            "created_at": "2026-05-30T00:00:00",
            "updated_at": "2026-05-30T00:00:00",
        }
        self._store[self._next_id] = row
        self.created.append(row)
        self._next_id += 1
        return dict(row)

    async def update_status(self, creative_id, status, **kwargs) -> None:
        self.status_updates.append({"id": creative_id, "status": status, **kwargs})
        if creative_id in self._store:
            self._store[creative_id].update(status=status, **kwargs)

    async def get(self, creative_id):
        return dict(self._store.get(creative_id, {})) or None


@pytest.fixture
def app_client(monkeypatch):
    from Flowcut.api.server import app
    from Flowcut.api.routes import creatives as creatives_route

    stub_oss = _StubOSSClient()
    monkeypatch.setattr(creatives_route, "build_oss_client", lambda: stub_oss)

    with TestClient(app) as client:
        fake_repo = _FakeCreativeRepo()
        app.state.container.creative_repo = fake_repo  # type: ignore[attr-defined]
        yield client, fake_repo, stub_oss


@pytest.mark.integration
def test_upload_creative_video_creates_ready_row(app_client) -> None:
    client, repo, oss = app_client
    payload = io.BytesIO(b"\x00" * 1024)
    resp = client.post(
        "/creatives/upload",
        files={"file": ("hot_promo.mp4", payload, "video/mp4")},
        data={"tenant_key": "flowcut"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["data"]["status"] == "READY"
    assert body["data"]["tenant_key"] == "flowcut"
    assert body["data"]["session_key"] == "manual_upload"
    assert body["data"]["script_id"] is None
    assert body["data"]["oss_key"].startswith("creatives/flowcut/uploads/")
    assert body["data"]["oss_key"].endswith("_hot_promo.mp4")

    assert len(repo.created) == 1
    assert len(repo.status_updates) == 1
    assert repo.status_updates[0]["status"] == "READY"
    assert len(oss.uploads) == 1


@pytest.mark.integration
def test_upload_creative_rejects_non_video_extension(app_client) -> None:
    client, _, _ = app_client
    payload = io.BytesIO(b"PNG fake")
    resp = client.post(
        "/creatives/upload",
        files={"file": ("not_a_video.png", payload, "image/png")},
        data={"tenant_key": "flowcut"},
    )
    assert resp.status_code == 415
    assert ".png" in resp.json()["detail"]
