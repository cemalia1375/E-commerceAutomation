"""T+1 千川数据回流定时脚本。

# B2: 本机开发期不启用，上云后由 systemd/supervisord 拉起
# 启动方式：uv run python -m Flowcut.scripts.cron_qianchuan_sync

每天 02:30 UTC+8 向 QIANCHUAN_SYNC 流投递一条任务。
脚本本身不执行抓取，只负责入队——Worker 消费并执行。

依赖：
  - croniter
  - dotenv（通过 Flowcut.config 间接加载）
  - Flowcut 所有依赖已安装（uv pip install -r requirements.txt）
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

# 把 SimpleClaw/ 加入 sys.path，支持直接以 -m 运行
_ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..")
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv

load_dotenv()

from croniter import croniter

from Flowcut.config import make_db_kwargs, make_qc_cdp_url, make_task_queue
from Flowcut.runtime.streams import FlowcutTaskStream
from Flowcut.storage.database import Database, ensure_schema
from Flowcut.storage.task_repo import RuntimeTaskRepository
from simpleclaw.runtime.services import RuntimeServices
from simpleclaw.runtime.task_protocol import TaskEnvelope

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("cron_qianchuan_sync")

_CRON_EXPR = "30 2 * * *"   # 每天 02:30 本地时间（Asia/Shanghai）
_TENANT_KEY = os.getenv("FLOWCUT_DEFAULT_TENANT_KEY", "flowcut")


async def _submit_once(runtime: RuntimeServices, tenant_key: str) -> str:
    """投递一条 qianchuan_sync 任务，返回 task_id。"""
    envelope = TaskEnvelope(
        task_type="qianchuan_sync",
        payload={"tenant_key": tenant_key},
        stream=FlowcutTaskStream.QIANCHUAN_SYNC,
        tenant_key=tenant_key,
        scope_key="qianchuan_sync:cron",
    )
    await runtime.submit_task(envelope)
    return envelope.task_id


async def main() -> None:
    db = Database(**make_db_kwargs())
    await db.connect()
    await ensure_schema(db)

    task_queue = make_task_queue()
    task_repo = RuntimeTaskRepository(db)
    runtime = RuntimeServices(task_queue=task_queue, task_state_store=task_repo)

    cron = croniter(_CRON_EXPR, datetime.now())
    logger.info("千川数据回流 cron 启动，表达式=%s tenant=%s", _CRON_EXPR, _TENANT_KEY)

    try:
        while True:
            next_run: datetime = cron.get_next(datetime)
            now = datetime.now()
            sleep_secs = max(0.0, (next_run - now).total_seconds())
            logger.info("下次同步：%s（%d 秒后）", next_run.strftime("%Y-%m-%d %H:%M:%S"), int(sleep_secs))
            await asyncio.sleep(sleep_secs)

            task_id = await _submit_once(runtime, _TENANT_KEY)
            logger.info("已投递 qianchuan_sync task_id=%s", task_id)
    except asyncio.CancelledError:
        logger.info("cron_qianchuan_sync 收到取消信号，退出")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
