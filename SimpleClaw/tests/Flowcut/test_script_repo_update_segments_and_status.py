"""ScriptRepository.update_segments_and_status 测试。

用于 scene_decompose executor 从 PROCESSING → DRAFT 的状态迁移：
一次 UPDATE 写 segments + status + updated_at。
"""
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
async def test_update_segments_and_status_processing_to_draft(
    repo: ScriptRepository,
):
    rec = await repo.create(
        tenant_key="t_uss_processing_to_draft",
        source="decomposed",
        segments=[],
        status="PROCESSING",
    )
    new_segments = [
        {"idx": 0, "start_time": 0.0, "end_time": 1.5,
         "visual": "镜头", "copy": "台词", "category": "产品展示"},
    ]
    await repo.update_segments_and_status(rec["id"], new_segments, "DRAFT")

    fetched = await repo.get(rec["id"])
    assert fetched is not None
    assert fetched["status"] == "DRAFT"
    assert fetched["segments"] == new_segments


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_segments_and_status_to_failed(
    repo: ScriptRepository,
):
    """允许 PROCESSING → FAILED 路径（segments 留空也行）。"""
    rec = await repo.create(
        tenant_key="t_uss_to_failed",
        source="decomposed",
        segments=[],
        status="PROCESSING",
    )
    await repo.update_segments_and_status(rec["id"], [], "FAILED")
    fetched = await repo.get(rec["id"])
    assert fetched is not None
    assert fetched["status"] == "FAILED"
    assert fetched["segments"] == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_segments_and_status_invalid_status_raises(
    repo: ScriptRepository,
):
    rec = await repo.create(
        tenant_key="t_uss_invalid",
        source="decomposed",
        segments=[],
        status="PROCESSING",
    )
    with pytest.raises(ValueError, match="invalid status"):
        await repo.update_segments_and_status(rec["id"], [], "WEIRD")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_update_segments_and_status_missing_id_raises(
    repo: ScriptRepository,
):
    with pytest.raises(ValueError, match="not found"):
        await repo.update_segments_and_status(99999999, [], "DRAFT")
