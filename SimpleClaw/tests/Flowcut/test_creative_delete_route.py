"""DELETE /creatives/{id} — 删除成片，清 OSS + fc_creative + fc_material_usage。

验收：
- 命中本租户成片 → 200，oss_client.delete_object 收到 oss_key/srt_url，repo.delete 被调。
- 跨租户 / 不存在 → 404。
- 保留源高光资产（不调用 highlight_asset_repo.delete）。

约束：用 dependency_overrides 覆盖 require_tenant，避免依赖登录 cookie。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from Flowcut.api.deps import require_tenant


class _FakeOSSClient:
    def __init__(self) -> None:
        self.deleted_keys: list[str] = []

    def delete_object(self, key: str) -> None:
        self.deleted_keys.append(key)


class _FakeCreativeRepo:
    def __init__(self, rows: dict[int, dict]) -> None:
        self._store = rows
        self.deleted_ids: list[int] = []

    async def get(self, creative_id):
        row = self._store.get(creative_id)
        return dict(row) if row else None

    async def delete(self, creative_id) -> None:
        self.deleted_ids.append(creative_id)
        self._store.pop(creative_id, None)


@pytest.fixture
def app_client(monkeypatch):
    from Flowcut.api.server import app

    with TestClient(app) as client:
        app.dependency_overrides[require_tenant] = lambda: "flowcut"
        container = app.state.container
        rows = {
            1: {
                "id": 1,
                "tenant_key": "flowcut",
                "creative_type": "highlight_original",
                "oss_key": "creatives/flowcut/highlight/1/abc.mp4",
                "srt_url": "creatives/flowcut/highlight/1/abc.srt",
            },
            2: {"id": 2, "tenant_key": "other_tenant", "oss_key": "x.mp4", "srt_url": None},
        }
        fake_repo = _FakeCreativeRepo(rows)
        fake_oss = _FakeOSSClient()
        monkeypatch.setattr(container, "creative_repo", fake_repo)
        monkeypatch.setattr(container, "oss_client", fake_oss)
        try:
            yield client, fake_repo, fake_oss
        finally:
            app.dependency_overrides.pop(require_tenant, None)


@pytest.mark.integration
def test_delete_creative_removes_row_and_oss(app_client) -> None:
    client, repo, oss = app_client
    resp = client.delete("/creatives/1")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True, "deleted": 1}
    assert repo.deleted_ids == [1]
    # oss_key 与 srt_url 都被删
    assert set(oss.deleted_keys) == {
        "creatives/flowcut/highlight/1/abc.mp4",
        "creatives/flowcut/highlight/1/abc.srt",
    }


@pytest.mark.integration
def test_delete_creative_cross_tenant_returns_404(app_client) -> None:
    client, repo, _ = app_client
    resp = client.delete("/creatives/2")
    assert resp.status_code == 404
    assert repo.deleted_ids == []


@pytest.mark.integration
def test_delete_creative_missing_returns_404(app_client) -> None:
    client, repo, _ = app_client
    resp = client.delete("/creatives/999999")
    assert resp.status_code == 404
    assert repo.deleted_ids == []
