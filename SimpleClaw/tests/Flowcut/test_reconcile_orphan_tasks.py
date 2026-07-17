"""orphan 任务 reconciler 集成测试。

策略：
- 用真实 MySQL（项目规则禁止 mock DB）
- 用真实 InMemoryTaskQueue + RuntimeServices
- 直接 INSERT 一条过时的 queued 行 + 一条过时的 running 行 + 一条新鲜的 queued 行
- 调用 reconcile_orphan_tasks，断言：
  * 仅过时的两条被重入队（succeeded == 2）
  * DB 中两条状态被刷成 queued、claimed_by/last_error 清空
  * 新鲜的那条不动
"""
from __future__ import annotations

import json
import os

import pymysql
import pytest
import pytest_asyncio

from Flowcut.runtime.reconcile import reconcile_orphan_tasks
from Flowcut.storage.database import Database, ensure_schema
from Flowcut.storage.task_repo import RuntimeTaskRepository
from simpleclaw.runtime.services import RuntimeServices
from simpleclaw.runtime.task_queue import InMemoryTaskQueue


def _conn():
    return pymysql.connect(
        host=os.environ["MYSQL_HOST"],
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DB"],
        charset="utf8mb4",
    )


@pytest_asyncio.fixture
async def db():
    d = Database(**{
        "host": os.environ["MYSQL_HOST"],
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.environ["MYSQL_USER"],
        "password": os.environ["MYSQL_PASSWORD"],
        "db": os.environ["MYSQL_DB"],
    })
    await d.connect()
    await ensure_schema(d)
    yield d
    await d.close()


def _insert_task(
    task_id: str, *, status: str, stale_seconds: int, attempt: int = 0,
    claimed_by: str | None = None,
) -> None:
    """插入一条 nb_runtime_tasks 记录，updated_at 设为 NOW() - stale_seconds。"""
    payload = json.dumps({"material_id": 999, "oss_key": "x", "oss_url": "x"})
    sql = """
        INSERT INTO nb_runtime_tasks
            (task_id, task_type, stream_name, tenant_key, scope_key,
             trace_id, status, attempt, max_attempts, payload_json, claimed_by,
             created_at, updated_at)
        VALUES (%s, 'material_process', 'flowcut:material_process', 't_reconcile',
                'mp:999', 'trace-x', %s, %s, 3, %s, %s,
                DATE_SUB(NOW(), INTERVAL %s SECOND),
                DATE_SUB(NOW(), INTERVAL %s SECOND))
    """
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(sql, (
            task_id, status, attempt, payload, claimed_by,
            stale_seconds, stale_seconds,
        ))
        conn.commit()


def _clean(task_ids: list[str]) -> None:
    if not task_ids:
        return
    placeholders = ",".join(["%s"] * len(task_ids))
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            f"DELETE FROM nb_runtime_tasks WHERE task_id IN ({placeholders})",
            task_ids,
        )
        conn.commit()


def _read(task_id: str) -> dict:
    with _conn() as conn, conn.cursor(pymysql.cursors.DictCursor) as cur:
        cur.execute(
            "SELECT task_id, status, attempt, claimed_by, last_error "
            "FROM nb_runtime_tasks WHERE task_id=%s",
            (task_id,),
        )
        return cur.fetchone()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reconcile_reenqueues_stale_queued_and_running(db):
    stale_q = "test-reconcile-stale-queued"
    stale_r = "test-reconcile-stale-running"
    fresh_q = "test-reconcile-fresh-queued"
    exhausted = "test-reconcile-exhausted"

    _clean([stale_q, stale_r, fresh_q, exhausted])
    try:
        # 过时 queued（11 分钟前）
        _insert_task(stale_q, status="queued", stale_seconds=11 * 60)
        # 过时 running（30 分钟前），有 claimed_by
        _insert_task(
            stale_r, status="running", stale_seconds=30 * 60,
            claimed_by="worker-dead",
        )
        # 新鲜 queued（1 分钟前）→ 不应被重入队
        _insert_task(fresh_q, status="queued", stale_seconds=60)
        # 用尽重试（attempt == max_attempts）→ 不应被重入队
        _insert_task(
            exhausted, status="queued", stale_seconds=20 * 60, attempt=3,
        )

        queue = InMemoryTaskQueue()
        task_repo = RuntimeTaskRepository(db)
        runtime = RuntimeServices(task_queue=queue, task_state_store=task_repo)

        n = await reconcile_orphan_tasks(db, runtime, threshold_seconds=600)
        assert n == 2

        # 过时记录被刷成 queued + claimed_by 清空
        for tid in (stale_q, stale_r):
            row = _read(tid)
            assert row["status"] == "queued"
            assert row["claimed_by"] is None
            assert row["last_error"] is None

        # 新鲜的 / 已用尽重试的不动
        assert _read(fresh_q)["status"] == "queued"
        assert _read(fresh_q)["claimed_by"] is None  # 本来就是 None
        assert _read(exhausted)["status"] == "queued"
        assert _read(exhausted)["attempt"] == 3
    finally:
        _clean([stale_q, stale_r, fresh_q, exhausted])


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reconcile_no_stale_returns_zero(db):
    fresh = "test-reconcile-only-fresh"
    _clean([fresh])
    try:
        _insert_task(fresh, status="queued", stale_seconds=10)

        queue = InMemoryTaskQueue()
        task_repo = RuntimeTaskRepository(db)
        runtime = RuntimeServices(task_queue=queue, task_state_store=task_repo)

        n = await reconcile_orphan_tasks(db, runtime, threshold_seconds=600)
        assert n == 0
    finally:
        _clean([fresh])


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reconcile_default_picks_up_all_orphans_regardless_of_staleness(db):
    """默认 threshold_seconds=0：startup 时所有 queued/running 都视为孤儿。

    防止 ghost running 记录因为还没"过期"被遗漏的 bug。
    """
    fresh_running = "test-reconcile-fresh-running-orphan"
    fresh_queued = "test-reconcile-fresh-queued-orphan"
    _clean([fresh_running, fresh_queued])
    try:
        # 都是 1 分钟前的新记录，按旧 10min 阈值会被遗漏
        _insert_task(
            fresh_running, status="running", stale_seconds=60,
            claimed_by="worker-dead",
        )
        _insert_task(fresh_queued, status="queued", stale_seconds=60)

        queue = InMemoryTaskQueue()
        task_repo = RuntimeTaskRepository(db)
        runtime = RuntimeServices(task_queue=queue, task_state_store=task_repo)

        # 默认（不传 threshold_seconds 或传 0）应该把这两条都捞起。
        # 不数总数，因为线上同表可能还有其它孤儿任务；只校验本测试塞进去的行被处理。
        await reconcile_orphan_tasks(db, runtime)

        for tid in (fresh_running, fresh_queued):
            row = _read(tid)
            assert row["status"] == "queued"
            assert row["claimed_by"] is None
    finally:
        _clean([fresh_running, fresh_queued])
