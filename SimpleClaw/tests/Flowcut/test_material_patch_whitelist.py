"""PATCH /materials/{id} 字段白名单测试。

可编辑字段：name / product / scene_role。
其它字段（oss_key / status / transcript / category / description 等）→ Pydantic 422。
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient


def _post_upload(client: TestClient, *, tenant_key: str, product: str) -> dict:
    """通过 HTTP 路由创建素材，避免直接 await fixture 与 app loop 冲突。"""
    files = {"file": ("orig.mp4", io.BytesIO(b"fake-bytes"), "video/mp4")}
    data = {"tenant_key": tenant_key, "product": product, "scene_role": "开场"}
    resp = client.post("/materials/upload", files=files, data=data)
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture
def app_client_and_id(monkeypatch):
    """启动 app + mock OSS / submit_task，通过 HTTP 创建一条素材。"""
    from Flowcut.storage import oss_client as oss_mod

    monkeypatch.setattr(oss_mod.OSSClient, "upload", lambda self, p, k: None)

    from Flowcut.api.server import app

    with TestClient(app) as client:
        container = app.state.container

        async def _fake_submit_task(envelope):
            return "task-fake"

        monkeypatch.setattr(container.runtime, "submit_task", _fake_submit_task)

        info = _post_upload(client, tenant_key="t_patch_test", product="通用")
        material_id = info["material_id"]
        yield client, material_id


@pytest.mark.integration
def test_patch_name_product_scene_role_succeeds(app_client_and_id) -> None:
    client, material_id = app_client_and_id
    resp = client.patch(
        f"/materials/{material_id}",
        json={"name": "renamed.mp4", "product": "洗发水X", "scene_role": "卖点"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "renamed.mp4"
    assert body["product"] == "洗发水X"
    assert body["scene_role"] == "卖点"


@pytest.mark.integration
def test_patch_partial_only_name(app_client_and_id) -> None:
    client, material_id = app_client_and_id
    resp = client.patch(f"/materials/{material_id}", json={"name": "only_name.mp4"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "only_name.mp4"


@pytest.mark.integration
@pytest.mark.parametrize(
    "forbidden",
    [
        {"oss_key": "evil"},
        {"status": "READY"},
        {"transcript": "x"},
        {"category": "audio"},
        {"description": "x"},
        {"name": "ok", "oss_key": "evil"},
    ],
)
def test_patch_rejects_non_whitelisted_fields(app_client_and_id, forbidden: dict) -> None:
    client, material_id = app_client_and_id
    resp = client.patch(f"/materials/{material_id}", json=forbidden)
    assert resp.status_code == 422, resp.text


@pytest.mark.integration
def test_patch_missing_id_returns_404(app_client_and_id) -> None:
    client, _ = app_client_and_id
    resp = client.patch("/materials/999999999", json={"name": "x"})
    assert resp.status_code == 404
