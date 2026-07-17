"""ScriptRepository.status 枚举扩展测试（PROCESSING / FAILED）。"""
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
async def test_create_defaults_to_draft(repo: ScriptRepository):
    rec = await repo.create(
        tenant_key="t_status_default",
        source="decomposed",
        segments=[],
    )
    assert rec["status"] == "DRAFT"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_with_processing(repo: ScriptRepository):
    rec = await repo.create(
        tenant_key="t_status_processing",
        source="decomposed",
        segments=[],
        status="PROCESSING",
    )
    assert rec["status"] == "PROCESSING"
    fetched = await repo.get(rec["id"])
    assert fetched is not None
    assert fetched["status"] == "PROCESSING"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_with_failed(repo: ScriptRepository):
    rec = await repo.create(
        tenant_key="t_status_failed",
        source="decomposed",
        segments=[],
        status="FAILED",
    )
    assert rec["status"] == "FAILED"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_with_invalid_status_raises(repo: ScriptRepository):
    with pytest.raises(ValueError, match="invalid status"):
        await repo.create(
            tenant_key="t_status_bad",
            source="decomposed",
            segments=[],
            status="WEIRD",
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_status_to_failed(repo: ScriptRepository):
    rec = await repo.create(
        tenant_key="t_status_to_failed",
        source="decomposed",
        segments=[],
        status="PROCESSING",
    )
    await repo.update_status(rec["id"], "FAILED")
    fetched = await repo.get(rec["id"])
    assert fetched is not None
    assert fetched["status"] == "FAILED"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_status_processing_to_draft(repo: ScriptRepository):
    rec = await repo.create(
        tenant_key="t_status_proc_draft",
        source="decomposed",
        segments=[],
        status="PROCESSING",
    )
    await repo.update_status(rec["id"], "DRAFT")
    fetched = await repo.get(rec["id"])
    assert fetched is not None
    assert fetched["status"] == "DRAFT"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_status_rejects_invalid(repo: ScriptRepository):
    rec = await repo.create(
        tenant_key="t_status_invalid_update",
        source="decomposed",
        segments=[],
    )
    with pytest.raises(ValueError, match="invalid status"):
        await repo.update_status(rec["id"], "WEIRD")
