"""GET /highlight-assets/groups — 入口层轻量分组（剧名/角色 + 数量）。

验收：
- asset_type=episode_source → 按 drama_name 分组。
- asset_type=digital_human_connector → 按 connector_role 分组。
- 非法 asset_type → 400。

约束：用 dependency_overrides 覆盖 require_tenant，避免依赖登录 cookie。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from Flowcut.api.deps import require_tenant


class _FakeHighlightAssetRepo:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def list_groups(self, tenant_key, *, asset_type, group_field):
        self.calls.append(
            {"tenant_key": tenant_key, "asset_type": asset_type, "group_field": group_field}
        )
        return [{"name": "都市修罗", "count": 12}, {"name": "霸总归来", "count": 5}]


@pytest.fixture
def app_client(monkeypatch):
    from Flowcut.api.server import app

    with TestClient(app) as client:
        app.dependency_overrides[require_tenant] = lambda: "flowcut"
        container = app.state.container
        fake_repo = _FakeHighlightAssetRepo()
        monkeypatch.setattr(container, "highlight_asset_repo", fake_repo)
        try:
            yield client, fake_repo
        finally:
            app.dependency_overrides.pop(require_tenant, None)


@pytest.mark.integration
def test_groups_episode_source_uses_drama_name(app_client) -> None:
    client, repo = app_client
    resp = client.get("/highlight-assets/groups", params={"asset_type": "episode_source"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == [
        {"name": "都市修罗", "count": 12},
        {"name": "霸总归来", "count": 5},
    ]
    assert repo.calls[0]["group_field"] == "drama_name"
    assert repo.calls[0]["asset_type"] == "episode_source"


@pytest.mark.integration
def test_groups_digital_human_uses_connector_role(app_client) -> None:
    client, repo = app_client
    resp = client.get(
        "/highlight-assets/groups", params={"asset_type": "digital_human_connector"}
    )
    assert resp.status_code == 200, resp.text
    assert repo.calls[0]["group_field"] == "connector_role"


@pytest.mark.integration
def test_groups_invalid_asset_type_returns_400(app_client) -> None:
    client, repo = app_client
    resp = client.get("/highlight-assets/groups", params={"asset_type": "bogus"})
    assert resp.status_code == 400
    assert repo.calls == []
