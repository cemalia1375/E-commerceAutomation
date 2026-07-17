"""POST /flowcut/scripts/{id}/export 新 selections payload 集成测试。

验证：
- selections 新格式：payload.selections 透传，material_ids 为去重 flatten
- 旧 material_ids：normalize 成 {"0": material_ids}（全部归到段 0）
- selections 为空 dict 或所有段为空 → 422
- 同时传 selections 和 material_ids → 优先 selections
- 缺 tenant_key → 422
"""
from __future__ import annotations

import os
from typing import Any

import pymysql
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient


@pytest_asyncio.fixture
async def script_id():
    """造一条 fc_script，返回其 id。"""
    from Flowcut.storage.database import Database, ensure_schema
    from Flowcut.storage.script_repo import ScriptRepository

    db = Database(
        host=os.environ["MYSQL_HOST"],
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        db=os.environ["MYSQL_DB"],
    )
    await db.connect()
    await ensure_schema(db)
    repo = ScriptRepository(db)
    rec = await repo.create(
        tenant_key="t_export_sel",
        source="uploaded",
        segments=[
            {"idx": 0, "visual": "v0", "copy": "c0"},
            {"idx": 1, "visual": "v1", "copy": "c1"},
        ],
    )
    sid = rec["id"]
    yield sid
    # 清理
    conn = pymysql.connect(
        host=os.environ["MYSQL_HOST"],
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DB"],
    )
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM fc_script WHERE id=%s", (sid,))
        conn.commit()
    finally:
        conn.close()
    await db.close()


@pytest.fixture
def app_client(monkeypatch):
    """启动 FlowCut app，mock submit_task 捕获 envelope。"""
    from Flowcut.api.server import app

    captured: dict[str, Any] = {}

    with TestClient(app) as client:
        container = app.state.container

        async def _fake_submit_task(envelope):  # type: ignore[no-untyped-def]
            captured["envelope"] = envelope
            return "task-export-fake-0001"

        monkeypatch.setattr(container.runtime, "submit_task", _fake_submit_task)
        client.captured = captured  # type: ignore[attr-defined]
        yield client


@pytest.mark.integration
def test_export_with_selections_succeeds_and_payload_keeps_selections(
    app_client: TestClient, script_id: int,
):
    resp = app_client.post(
        f"/flowcut/scripts/{script_id}/export",
        json={
            "tenant_key": "t_export_sel",
            "selections": {"0": [101, 102], "1": [103]},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True

    envelope = app_client.captured["envelope"]  # type: ignore[attr-defined]
    assert body["task_id"] == envelope.task_id  # route returns envelope.task_id, not queue id
    assert envelope.task_id.startswith("export-")
    payload = envelope.payload
    assert payload["script_id"] == script_id
    assert payload["selections"] == {"0": [101, 102], "1": [103]}
    # flatten 去重保兼容
    assert sorted(payload["material_ids"]) == [101, 102, 103]


@pytest.mark.integration
def test_export_with_legacy_material_ids_normalizes_to_segment_zero(
    app_client: TestClient, script_id: int,
):
    """旧 material_ids 客户端：normalize 成 {"0": [...]}。"""
    resp = app_client.post(
        f"/flowcut/scripts/{script_id}/export",
        json={
            "tenant_key": "t_export_sel",
            "material_ids": [201, 202, 202, 203],
        },
    )
    assert resp.status_code == 200, resp.text
    envelope = app_client.captured["envelope"]  # type: ignore[attr-defined]
    payload = envelope.payload
    assert payload["selections"] == {"0": [201, 202, 202, 203]}
    assert sorted(payload["material_ids"]) == [201, 202, 203]


@pytest.mark.integration
def test_export_empty_selections_dict_returns_422(
    app_client: TestClient, script_id: int,
):
    resp = app_client.post(
        f"/flowcut/scripts/{script_id}/export",
        json={"tenant_key": "t_export_sel", "selections": {}},
    )
    assert resp.status_code == 422


@pytest.mark.integration
def test_export_selections_all_segments_empty_returns_422(
    app_client: TestClient, script_id: int,
):
    resp = app_client.post(
        f"/flowcut/scripts/{script_id}/export",
        json={
            "tenant_key": "t_export_sel",
            "selections": {"0": [], "1": []},
        },
    )
    assert resp.status_code == 422


@pytest.mark.integration
def test_export_no_selections_no_material_ids_returns_422(
    app_client: TestClient, script_id: int,
):
    resp = app_client.post(
        f"/flowcut/scripts/{script_id}/export",
        json={"tenant_key": "t_export_sel"},
    )
    assert resp.status_code == 422


@pytest.mark.integration
def test_export_selections_takes_precedence_over_material_ids(
    app_client: TestClient, script_id: int,
):
    resp = app_client.post(
        f"/flowcut/scripts/{script_id}/export",
        json={
            "tenant_key": "t_export_sel",
            "selections": {"0": [301], "1": [302]},
            "material_ids": [999],  # 应被忽略
        },
    )
    assert resp.status_code == 200, resp.text
    envelope = app_client.captured["envelope"]  # type: ignore[attr-defined]
    payload = envelope.payload
    assert payload["selections"] == {"0": [301], "1": [302]}
    assert 999 not in payload["material_ids"]
    assert sorted(payload["material_ids"]) == [301, 302]


@pytest.mark.integration
def test_export_missing_tenant_key_returns_422(
    app_client: TestClient, script_id: int,
):
    resp = app_client.post(
        f"/flowcut/scripts/{script_id}/export",
        json={"selections": {"0": [1]}},
    )
    assert resp.status_code == 422


@pytest.mark.integration
def test_export_selections_non_list_value_returns_422(
    app_client: TestClient, script_id: int,
):
    """selections value 不是 list（如字符串）→ 422，错误信息指明段。"""
    resp = app_client.post(
        f"/flowcut/scripts/{script_id}/export",
        json={
            "tenant_key": "t_export_sel",
            "selections": {"0": "abc"},
        },
    )
    assert resp.status_code == 422
    detail = resp.json().get("detail", "")
    assert "0" in detail
    assert "list" in detail.lower()


@pytest.mark.integration
def test_export_selections_non_int_element_returns_422(
    app_client: TestClient, script_id: int,
):
    """selections list 内含非 int 元素 → 422，错误信息指明段 + 期望 int。"""
    resp = app_client.post(
        f"/flowcut/scripts/{script_id}/export",
        json={
            "tenant_key": "t_export_sel",
            "selections": {"0": [1, "x", 3]},
        },
    )
    assert resp.status_code == 422
    detail = resp.json().get("detail", "")
    assert "0" in detail
    assert "int" in detail.lower()
