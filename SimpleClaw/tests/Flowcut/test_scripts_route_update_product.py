"""POST /flowcut/scripts/{id}/update-product 路由测试。"""
import os

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from Flowcut.api.server import app
from Flowcut.storage.database import Database, ensure_schema
from Flowcut.storage.script_repo import ScriptRepository


@pytest_asyncio.fixture
async def script_id():
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
        tenant_key="t_route_test",
        source="decomposed",
        segments=[{"idx": 0, "visual": "v", "copy": "c"}],
    )
    yield rec["id"]
    await db.close()


@pytest.mark.integration
def test_update_product_success(script_id: int):
    with TestClient(app) as client:
        resp = client.post(
            f"/flowcut/scripts/{script_id}/update-product",
            json={"product": "洗发水X"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["product"] == "洗发水X"

        get_resp = client.get(f"/flowcut/scripts/{script_id}")
        assert get_resp.json()["product"] == "洗发水X"


@pytest.mark.integration
def test_update_product_missing_id_returns_404():
    with TestClient(app) as client:
        resp = client.post(
            "/flowcut/scripts/999999999/update-product",
            json={"product": "X"},
        )
        assert resp.status_code == 404


@pytest.mark.integration
def test_update_product_empty_string_treated_as_null(script_id: int):
    with TestClient(app) as client:
        resp = client.post(
            f"/flowcut/scripts/{script_id}/update-product",
            json={"product": ""},
        )
        assert resp.status_code == 200
        get_resp = client.get(f"/flowcut/scripts/{script_id}")
        assert get_resp.json()["product"] is None
