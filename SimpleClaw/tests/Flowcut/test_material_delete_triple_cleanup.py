"""DELETE /materials/{id} 三方一致清理测试。

验收：DELETE 后，MySQL fc_material 行删除 + Qdrant 向量点删除 + OSS delete_object 被调用。

约束（按项目惯例）：
- vector_store + material_repo 走真集成（真 Qdrant、真 MySQL）。
- OSS 用 fake client 替换 container.oss_client，避免对真 bucket 写删。
- 上传走 HTTP POST /materials/upload 避免 fixture 与 app event loop 冲突。
- Qdrant 向量插入用独立的 VectorStore 实例（asyncio.run），不复用 container 内 client。
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


def _post_upload(client: TestClient, *, tenant_key: str, product: str) -> dict:
    files = {"file": ("test_delete.mp4", io.BytesIO(b"fake-bytes"), "video/mp4")}
    data = {"tenant_key": tenant_key, "product": product}
    resp = client.post("/materials/upload", files=files, data=data)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _qdrant_upsert(material_id: int, tenant_key: str, product: str) -> None:
    """用独立 VectorStore 实例 upsert（独立 loop，避免与 app loop 串扰）。"""
    async def _go() -> None:
        vs = VectorStore(url=make_qdrant_url())
        await vs.upsert(
            material_id=material_id,
            desc_vector=[0.1] * 1024,
            transcript_vector=None,
            payload={"tenant_key": tenant_key, "product": product, "scene_role": None},
        )

    asyncio.run(_go())


def _qdrant_list_all_ids() -> list[int]:
    async def _go() -> list[int]:
        vs = VectorStore(url=make_qdrant_url())
        return await vs.list_all_point_ids()

    return asyncio.run(_go())


def _qdrant_delete(material_id: int) -> None:
    async def _go() -> None:
        vs = VectorStore(url=make_qdrant_url())
        await vs.delete(material_id)

    asyncio.run(_go())


@pytest.fixture
def app_client(monkeypatch):
    """启动 app + mock OSS upload + mock submit_task，并把 container.oss_client 换成 fake。"""
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
        # 在 app.state 上挂一个引用，便于测试取回
        client._fake_oss = fake_oss  # type: ignore[attr-defined]
        yield client


@pytest.mark.integration
def test_delete_removes_mysql_qdrant_and_calls_oss(app_client: TestClient) -> None:
    info = _post_upload(app_client, tenant_key="t_del_test", product="通用")
    material_id = info["material_id"]
    oss_key = info["oss_key"]
    fake_oss: _FakeOSSClient = app_client._fake_oss  # type: ignore[attr-defined]

    # 真插入一个 Qdrant 向量点
    _qdrant_upsert(material_id, "t_del_test", "通用")
    try:
        # 前置：素材存在
        assert app_client.get(f"/materials/{material_id}").status_code == 200
        # 前置：Qdrant 含有该 point
        assert material_id in _qdrant_list_all_ids()

        # 执行 DELETE
        resp = app_client.delete(f"/materials/{material_id}")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"ok": True}

        # 1) MySQL 行没了
        assert app_client.get(f"/materials/{material_id}").status_code == 404

        # 2) Qdrant 向量点没了
        assert material_id not in _qdrant_list_all_ids()

        # 3) OSS delete_object 被调用
        assert fake_oss.deleted_keys == [oss_key]
    finally:
        # 兜底防遗留
        try:
            _qdrant_delete(material_id)
        except Exception:
            pass


@pytest.mark.integration
def test_delete_missing_id_returns_404(app_client: TestClient) -> None:
    resp = app_client.delete("/materials/999999999")
    assert resp.status_code == 404


@pytest.mark.integration
def test_delete_continues_when_oss_delete_fails(app_client: TestClient) -> None:
    """OSS 删除失败仅 warn，DB / Qdrant 仍被清理，路由仍返回 200。"""
    info = _post_upload(app_client, tenant_key="t_del_test_oss_fail", product="通用")
    material_id = info["material_id"]
    fake_oss: _FakeOSSClient = app_client._fake_oss  # type: ignore[attr-defined]

    def _boom(key: str) -> None:
        raise RuntimeError("simulated OSS failure")

    fake_oss.delete_object = _boom  # type: ignore[assignment]

    _qdrant_upsert(material_id, "t_del_test_oss_fail", "通用")
    try:
        resp = app_client.delete(f"/materials/{material_id}")
        assert resp.status_code == 200, resp.text
        # DB 行已删
        assert app_client.get(f"/materials/{material_id}").status_code == 404
        # Qdrant 向量已删
        assert material_id not in _qdrant_list_all_ids()
    finally:
        try:
            _qdrant_delete(material_id)
        except Exception:
            pass
