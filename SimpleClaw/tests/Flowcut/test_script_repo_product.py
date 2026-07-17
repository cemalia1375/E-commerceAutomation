"""ScriptRepository.product 字段读写测试。"""
import os

import pytest
import pytest_asyncio

from Flowcut.storage.database import Database, ensure_schema
from Flowcut.storage.script_repo import ScriptRepository


@pytest_asyncio.fixture
async def repo():
    db = Database(
        host=os.environ["MYSQL_HOST"],
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        db=os.environ["MYSQL_DB"],
    )
    await db.connect()
    await ensure_schema(db)
    yield ScriptRepository(db)
    await db.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_with_product(repo: ScriptRepository):
    rec = await repo.create(
        tenant_key="t_test_product",
        source="decomposed",
        segments=[{"idx": 0, "visual": "v", "copy": "c"}],
        reference_video_id=None,
        product="洗发水A",
    )
    assert rec["product"] == "洗发水A"
    fetched = await repo.get(rec["id"])
    assert fetched is not None
    assert fetched["product"] == "洗发水A"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_without_product_returns_none(repo: ScriptRepository):
    rec = await repo.create(
        tenant_key="t_test_no_product",
        source="decomposed",
        segments=[{"idx": 0, "visual": "v", "copy": "c"}],
    )
    assert rec["product"] is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_product(repo: ScriptRepository):
    rec = await repo.create(
        tenant_key="t_test_update",
        source="decomposed",
        segments=[{"idx": 0, "visual": "v", "copy": "c"}],
    )
    await repo.update_product(rec["id"], "新产品B")
    fetched = await repo.get(rec["id"])
    assert fetched["product"] == "新产品B"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_product_missing_id_raises(repo: ScriptRepository):
    with pytest.raises(ValueError, match="not found"):
        await repo.update_product(999_999_999, "X")
