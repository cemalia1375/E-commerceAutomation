"""预置/创建 FlowCut 账号。

用法：
    cd SimpleClaw && uv run python -m Flowcut.scripts.create_user <username> <password> <tenant_key> [display_name]

示例（映射到现有存量数据所在的 flowcut 工作台）：
    uv run python -m Flowcut.scripts.create_user admin 's3cret' flowcut 管理员
"""
from __future__ import annotations

import asyncio
import sys

from dotenv import load_dotenv

from Flowcut.auth.security import hash_password
from Flowcut.config import make_db_kwargs
from Flowcut.storage.database import Database, ensure_schema
from Flowcut.storage.user_repo import UserRepository

load_dotenv()


async def main() -> None:
    if len(sys.argv) < 4:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    username, password, tenant_key = sys.argv[1], sys.argv[2], sys.argv[3]
    display_name = sys.argv[4] if len(sys.argv) > 4 else None

    db = Database(**make_db_kwargs())
    await db.connect()
    try:
        await ensure_schema(db)
        repo = UserRepository(db)
        if await repo.get_by_username(username) is not None:
            print(f"用户名已存在：{username}", file=sys.stderr)
            sys.exit(1)
        user_id = await repo.create(
            username=username,
            password_hash=hash_password(password),
            tenant_key=tenant_key,
            display_name=display_name,
        )
        print(f"已创建用户 id={user_id} username={username} tenant_key={tenant_key}")
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
