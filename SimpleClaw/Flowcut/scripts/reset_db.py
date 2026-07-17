"""一次性清库脚本：DROP 所有 fc_* 表后调 ensure_schema 重建。

仅限开发环境使用。生产环境的迁移策略不在本脚本范围。

用法：
    cd SimpleClaw && uv run python -m Flowcut.scripts.reset_db
"""
from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

from Flowcut.storage.database import Database, ensure_schema

load_dotenv()


DROP_ORDER = [
    "fc_material_usage",
    "fc_creative",
    "fc_material",
    "fc_reference_video",
    "fc_script",
]


async def main() -> None:
    env = os.environ.get("FLOWCUT_ENV", "dev")
    if env not in ("dev", "test"):
        print(f"拒绝在 FLOWCUT_ENV={env} 下清库", file=sys.stderr)
        sys.exit(1)

    db = Database(
        host=os.environ["MYSQL_HOST"],
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        db=os.environ["MYSQL_DB"],
    )
    await db.connect()

    async with db.acquire() as conn:
        async with conn.cursor() as cur:
            for table in DROP_ORDER:
                print(f"DROP TABLE IF EXISTS {table}")
                await cur.execute(f"DROP TABLE IF EXISTS {table}")
            await conn.commit()

    await ensure_schema(db)
    print("清库重建完成")
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
