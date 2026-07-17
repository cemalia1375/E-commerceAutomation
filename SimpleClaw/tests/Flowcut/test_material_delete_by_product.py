"""DELETE /materials?tenant_key=...&product=... 按产品批量删除集成测试。

约束（沿用 test_material_delete_triple_cleanup.py 套路）：
- material_repo / vector_store 走真集成
- OSS 用 fake client 替换 container.oss_client
- 上传走 HTTP POST /materials/upload
"""
from __future__ import annotations

import asyncio
import io

import pytest
from fastapi.testclient import TestClient

from Flowcut.config import make_qdrant_url
from Flowcut.storage.vector_store import VectorStore


class _FakeOSSClient:
    def __init__(self) -> None:
        self.deleted_keys: list[str] = []

    def delete_object(self, key: str) -> None:
        self.deleted_keys.append(key)


def _post_upload(client: TestClient, *, tenant_key: str, product: str, name: str) -> dict:
    files = {"file": (name, io.BytesIO(b"fake-bytes"), "video/mp4")}
    data = {"tenant_key": tenant_key, "product": product}
    resp = client.post("/materials/upload", files=files, data=data)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _qdrant_upsert(material_id: int, tenant_key: str, product: str) -> None:
    async def _go() -> None:
        vs = VectorStore(url=make_qdrant_url())
        await vs.upsert(
            material_id=material_id,
            desc_vector=[0.1] * 1024,
            transcript_vector=None,
            payload={"tenant_key": tenant_key, "product": product, "scene_role": None},
        )

    asyncio.run(_go())


def _qdrant_delete(material_id: int) -> None:
    async def _go() -> None:
        vs = VectorStore(url=make_qdrant_url())
        await vs.delete(material_id)

    asyncio.run(_go())


def _qdrant_list_all_ids() -> list[int]:
    async def _go() -> list[int]:
        vs = VectorStore(url=make_qdrant_url())
        return await vs.list_all_point_ids()

    return asyncio.run(_go())


@pytest.fixture
def app_client(monkeypatch):
    from Flowcut.storage import oss_client as oss_mod

    monkeypatch.setattr(oss_mod.OSSClient, "upload", lambda self, p, k: None)

    from Flowcut.api.server import app

    with TestClient(app) as client:
        container = app.state.container

        async def _fake_submit_task(envelope):
            return "task-fake"

        monkeypatch.setattr(container.runtime, "submit_task", _fake_submit_task)

        fake_oss = _FakeOSSClient()
        monkeypatch.setattr(container, "oss_client", fake_oss)
        client._fake_oss = fake_oss  # type: ignore[attr-defined]
        yield client


@pytest.mark.integration
def test_delete_by_product_only_removes_matching_product(app_client: TestClient) -> None:
    tenant = "t_bulk_del"
    fake_oss: _FakeOSSClient = app_client._fake_oss  # type: ignore[attr-defined]

    # 3 条 product="测试" + 2 条 product="正式"
    test_ids: list[int] = []
    keep_ids: list[int] = []
    for i in range(3):
        info = _post_upload(app_client, tenant_key=tenant, product="测试", name=f"t{i}.mp4")
        test_ids.append(info["material_id"])
        _qdrant_upsert(info["material_id"], tenant, "测试")
    for i in range(2):
        info = _post_upload(app_client, tenant_key=tenant, product="正式", name=f"k{i}.mp4")
        keep_ids.append(info["material_id"])
        _qdrant_upsert(info["material_id"], tenant, "正式")

    try:
        resp = app_client.delete(
            "/materials", params={"tenant_key": tenant, "product": "测试"}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["deleted"] == 3
        assert body["errors"] == []

        # 测试产品下 3 条 MySQL 行都没了
        for mid in test_ids:
            assert app_client.get(f"/materials/{mid}").status_code == 404
        # 正式产品 2 条保留
        for mid in keep_ids:
            assert app_client.get(f"/materials/{mid}").status_code == 200

        # Qdrant 仅删除测试产品的 3 个点
        remaining = _qdrant_list_all_ids()
        for mid in test_ids:
            assert mid not in remaining
        for mid in keep_ids:
            assert mid in remaining

        # OSS delete_object 被调用 3 次
        assert len(fake_oss.deleted_keys) == 3
    finally:
        for mid in test_ids + keep_ids:
            try:
                _qdrant_delete(mid)
            except Exception:
                pass


@pytest.mark.integration
def test_delete_by_product_returns_zero_when_no_match(app_client: TestClient) -> None:
    resp = app_client.delete(
        "/materials", params={"tenant_key": "t_noexist_xyz", "product": "不存在的产品"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"ok": True, "deleted": 0, "errors": []}


@pytest.mark.integration
def test_delete_by_product_missing_tenant_key_returns_422(app_client: TestClient) -> None:
    resp = app_client.delete("/materials", params={"product": "x"})
    assert resp.status_code == 422


@pytest.mark.integration
def test_delete_by_product_missing_product_returns_422(app_client: TestClient) -> None:
    resp = app_client.delete("/materials", params={"tenant_key": "t"})
    assert resp.status_code == 422


@pytest.mark.integration
def test_delete_by_product_empty_string_returns_422(app_client: TestClient) -> None:
    resp = app_client.delete("/materials", params={"tenant_key": "", "product": ""})
    assert resp.status_code == 422
