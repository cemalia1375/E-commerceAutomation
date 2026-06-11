# Flowcut v2 流程改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 spec `2026-05-22-flowcut-v2-pipeline-design.md` 描述的 4 项产品需求 —— 脚本拆解扩展、用户上传脚本、素材匹配接通、素材导出 —— 端到端打通。

**Architecture:** 渐进式 Tool 化（spec § 4）：新增能力全部 Tool 化，REST 路由是薄壳子调 Tool；老路由/老代码不动。SCENE_DECOMPOSE executor 增产 fc_script + 音频；新增 EXPORT_PACKAGE stream；material_matcher 改为 visual/copy 双 query。

**Tech Stack:**
- 后端：Python 3.11+ / FastAPI / aiomysql / openai / google-genai / FFmpeg subprocess / pytest
- 前端：React 19 / TypeScript / Vite / Ant Design 6 / Zustand
- 包管理：uv（后端）/ npm（前端）

**说明：** 所有路径以 monorepo 根目录 `/Users/shengxingou-1/电商自动化运营/E-commerceAutomation` 为基准。后端工作目录是 `SimpleClaw/`，前端工作目录是 `flowcut_frontend/`。

**前置阅读：** 实现者必须先读 `docs/superpowers/specs/2026-05-22-flowcut-v2-pipeline-design.md` 和 `docs/flowcut/process-design.md`（附 C）。

---

## 任务一览（按阶段分组）

- **Phase 0**：技术验证（spike）
- **Phase 1**：数据层（fc_script DDL + ScriptRepository + reset_db）
- **Phase 2**：后端服务改造（material_matcher 双 query + reference_video_repo 新字段）
- **Phase 3**：4 个新 Tool
- **Phase 4**：SCENE_DECOMPOSE executor 改造
- **Phase 5**：EXPORT_PACKAGE stream + executor + worker 注册
- **Phase 6**：REST 路由 + container 装配
- **Phase 7**：前端 types + store
- **Phase 8**：前端 ScriptEditor 页
- **Phase 9**：前端 MaterialPreview 页
- **Phase 10**：前端 ExportButton + 任务轮询
- **Phase 11**：前端入口改造 + 路由
- **Phase 12**：端到端联调 + 清库重跑验证

---

## Phase 0: 技术验证（spike）

### Task 0.1: 验证字节跳动 ASR 返回结构

**目标：** 确认现有 ASR WebSocket 是否返回词级时间戳，决定 § 9.1 走主路径还是 fallback。

**Files:**
- 临时探针：`SimpleClaw/Flowcut/scripts/spike_asr_response.py`（新建，验证完可保留作工具脚本）

- [ ] **Step 1: 找到现有 ASR 调用代码**

Run: `grep -rn "websocket\|ASR\|bigmodel" SimpleClaw/Flowcut/runtime/executors.py | head -10`
读取这段代码理解 ASR 返回格式当前如何被处理。

- [ ] **Step 2: 写探针脚本**

创建 `SimpleClaw/Flowcut/scripts/spike_asr_response.py`：

```python
"""ASR 探针：dump 一次完整返回结构，确认词级时间戳是否存在。"""
import asyncio
import os
import sys
import json
from pathlib import Path

# 从现有 executor 中复用 ASR 调用函数
# 实际代码需参考 executors.py 中 ASR 部分实现
async def main():
    if len(sys.argv) < 2:
        print("用法: python -m Flowcut.scripts.spike_asr_response <wav_file>")
        sys.exit(1)
    wav_path = sys.argv[1]
    # ... 调用 ASR，把完整 response dump 到 stdout
    # 看返回 JSON 是否含 word_offset / word_duration / 类似字段

if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 3: 准备一个测试 wav 文件**

随便从已有视频抽 10-20 秒短音频：
```bash
ffmpeg -i some_video.mp4 -t 15 -ac 1 -ar 16000 /tmp/spike.wav
```

- [ ] **Step 4: 跑探针并人工审查输出**

```bash
cd SimpleClaw && uv run python -m Flowcut.scripts.spike_asr_response /tmp/spike.wav > /tmp/asr_dump.json
```

- [ ] **Step 5: 记录结论到 spec § 9.1**

Edit `docs/superpowers/specs/2026-05-22-flowcut-v2-pipeline-design.md` 的 § 9.1，加一行：
```
**验证结论（YYYY-MM-DD）：** [词级时间戳可用 / 不可用，走 fallback (a)：Gemini 拆镜时同时输出该段口播文本]
```

- [ ] **Step 6: Commit spike 结果**

```bash
git add SimpleClaw/Flowcut/scripts/spike_asr_response.py docs/superpowers/specs/2026-05-22-flowcut-v2-pipeline-design.md
git commit -m "spike: 验证 ASR 词级时间戳可行性"
```

**决策点：** 后续 Task 4.2（ASR 按段截取）的实现策略取决于本任务结论：
- 词级时间戳可用 → Task 4.2 走主路径
- 不可用 → Task 4.2 改走 Gemini fallback（让 Gemini 在拆镜时多输出 `copy` 字段）

---

## Phase 1: 数据层

### Task 1.1: 改造 fc_script 表 DDL

**Files:**
- Modify: `SimpleClaw/Flowcut/storage/database.py` — `ensure_schema()` 内的 fc_script CREATE TABLE 语句

- [ ] **Step 1: 找到现有 fc_script DDL**

Run: `grep -n "fc_script" SimpleClaw/Flowcut/storage/database.py`
找到 `CREATE TABLE IF NOT EXISTS fc_script (...)` 那段。

- [ ] **Step 2: 替换 DDL 为新 schema**

把整段 `CREATE TABLE IF NOT EXISTS fc_script (...)` 替换为：

```sql
CREATE TABLE IF NOT EXISTS fc_script (
    id                  BIGINT       NOT NULL AUTO_INCREMENT,
    tenant_key          VARCHAR(255) NOT NULL,
    source              VARCHAR(16)  NOT NULL,
    reference_video_id  BIGINT       NULL,
    segments_json       JSON         NOT NULL,
    status              VARCHAR(16)  NOT NULL DEFAULT 'DRAFT',
    created_at          DATETIME     NOT NULL,
    updated_at          DATETIME     NOT NULL,
    PRIMARY KEY (id),
    KEY idx_fc_script_tenant_status (tenant_key, status),
    KEY idx_fc_script_ref_video (reference_video_id)
)
```

注意：MySQL 用 VARCHAR 而非 ENUM（保持与现有 fc_material/fc_creative 一致的风格，避免后续加新 source/status 值时改 schema）。

- [ ] **Step 3: 同 DDL 文件加 fc_reference_video 新列**

在 `ensure_schema` 内现有 fc_reference_video CREATE TABLE 语句中，加入两个新字段：

```sql
audio_oss_key  VARCHAR(512) NULL,
script_id      BIGINT       NULL,
```

放在 `status` 之前即可。

- [ ] **Step 4: Commit DDL 改造**

```bash
git add SimpleClaw/Flowcut/storage/database.py
git commit -m "feat: fc_script DDL 改造 + fc_reference_video 新字段 audio_oss_key/script_id"
```

### Task 1.2: 新建 reset_db.py 一次性脚本

**Files:**
- Create: `SimpleClaw/Flowcut/scripts/__init__.py`（若不存在）
- Create: `SimpleClaw/Flowcut/scripts/reset_db.py`

- [ ] **Step 1: 创建 scripts 包**

```bash
mkdir -p SimpleClaw/Flowcut/scripts
touch SimpleClaw/Flowcut/scripts/__init__.py
```

- [ ] **Step 2: 写 reset_db.py**

Create `SimpleClaw/Flowcut/scripts/reset_db.py`:

```python
"""一次性清库脚本：DROP 所有 fc_* 表后调 ensure_schema 重建。

仅限开发环境使用。生产环境的迁移策略不在本脚本范围。

用法：
    cd SimpleClaw && uv run python -m Flowcut.scripts.reset_db
"""
from __future__ import annotations

import asyncio
import os
import sys

from Flowcut.storage.database import Database, ensure_schema


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
```

- [ ] **Step 3: 本地跑一次验证**

```bash
cd SimpleClaw
FLOWCUT_ENV=dev uv run python -m Flowcut.scripts.reset_db
```
预期：打印 DROP 5 条 + "清库重建完成"。

- [ ] **Step 4: 验证表结构**

```bash
mysql -u $MYSQL_USER -p$MYSQL_PASSWORD $MYSQL_DB -e "DESCRIBE fc_script; DESCRIBE fc_reference_video;"
```
预期：fc_script 含 source/reference_video_id/status；fc_reference_video 含 audio_oss_key/script_id。

- [ ] **Step 5: Commit**

```bash
git add SimpleClaw/Flowcut/scripts/__init__.py SimpleClaw/Flowcut/scripts/reset_db.py
git commit -m "feat: 新增 reset_db 一次性清库脚本"
```

### Task 1.3: 改造 ScriptRepository

**Files:**
- Modify: `SimpleClaw/Flowcut/storage/script_repo.py` — 完全重写以匹配新 schema
- Test: `SimpleClaw/tests/test_script_repo.py`

- [ ] **Step 1: 写 ScriptRepository 单元测试**

Create `SimpleClaw/tests/test_script_repo.py`:

```python
"""ScriptRepository 单元测试。"""
from __future__ import annotations

import pytest

from Flowcut.storage.script_repo import ScriptRepository, StatusConflictError


@pytest.mark.unit
async def test_create_uploaded_script(db):
    repo = ScriptRepository(db)
    record = await repo.create(
        tenant_key="t1",
        source="uploaded",
        segments=[
            {"idx": 0, "start_time": 0.0, "end_time": 3.0, "visual": "v", "copy": "c"}
        ],
    )
    assert record["id"] > 0
    assert record["source"] == "uploaded"
    assert record["status"] == "DRAFT"
    assert record["reference_video_id"] is None
    assert record["segments"][0]["visual"] == "v"


@pytest.mark.unit
async def test_create_decomposed_script(db):
    repo = ScriptRepository(db)
    record = await repo.create(
        tenant_key="t1",
        source="decomposed",
        reference_video_id=42,
        segments=[],
    )
    assert record["reference_video_id"] == 42


@pytest.mark.unit
async def test_update_segments_only_draft(db):
    repo = ScriptRepository(db)
    record = await repo.create(tenant_key="t1", source="uploaded", segments=[])
    sid = record["id"]
    # DRAFT 可改
    await repo.update_segments(sid, [{"idx": 0, "visual": "v2", "copy": ""}])
    # 改成 CONFIRMED
    await repo.update_status(sid, "CONFIRMED")
    # CONFIRMED 改 segments 应抛错
    with pytest.raises(StatusConflictError):
        await repo.update_segments(sid, [])


@pytest.mark.unit
async def test_list_by_tenant_filter(db):
    repo = ScriptRepository(db)
    await repo.create(tenant_key="t1", source="uploaded", segments=[])
    await repo.create(tenant_key="t1", source="decomposed", reference_video_id=1, segments=[])
    uploaded = await repo.list_by_tenant("t1", source="uploaded")
    assert all(r["source"] == "uploaded" for r in uploaded)
```

注意 `db` fixture 是 conftest.py 中现成的（参考其他 test 文件确认 fixture 名）。

- [ ] **Step 2: 运行测试看哪个 fixture 名对**

Run: `grep -rn "@pytest.fixture" SimpleClaw/tests/conftest.py`
如果 fixture 名不是 `db`，对应调整测试中参数名。

- [ ] **Step 3: 跑测试确认 FAIL**

```bash
cd SimpleClaw && uv run pytest tests/test_script_repo.py -v -m unit
```
预期：FAIL（旧 ScriptRepository 没有这些方法和签名）。

- [ ] **Step 4: 重写 script_repo.py**

完全替换 `SimpleClaw/Flowcut/storage/script_repo.py` 为：

```python
"""ScriptRepository — fc_script 表的读写封装（v2 schema）。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from Flowcut.storage.database import Database


class StatusConflictError(Exception):
    """脚本状态不允许该操作（如 CONFIRMED 改 segments）。"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _row_to_dict(cols: list[str], row: tuple) -> dict[str, Any]:
    item = dict(zip(cols, row))
    raw = item.get("segments_json")
    if isinstance(raw, str):
        item["segments"] = json.loads(raw)
    else:
        item["segments"] = raw or []
    item.pop("segments_json", None)
    return item


_COLS = [
    "id", "tenant_key", "source", "reference_video_id",
    "segments_json", "status", "created_at", "updated_at",
]
_SELECT_COLS = ", ".join(_COLS)


class ScriptRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(
        self,
        *,
        tenant_key: str,
        source: str,
        segments: list[dict],
        reference_video_id: int | None = None,
    ) -> dict[str, Any]:
        if source not in ("decomposed", "uploaded"):
            raise ValueError(f"invalid source: {source}")
        now = _now()
        segments_json = json.dumps(segments, ensure_ascii=False)
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO fc_script
                        (tenant_key, source, reference_video_id,
                         segments_json, status, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, 'DRAFT', %s, %s)
                    """,
                    (tenant_key, source, reference_video_id,
                     segments_json, now, now),
                )
                script_id = cur.lastrowid
                await conn.commit()
        result = await self.get(script_id)
        assert result is not None
        return result

    async def get(self, script_id: int) -> dict[str, Any] | None:
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"SELECT {_SELECT_COLS} FROM fc_script WHERE id = %s",
                    (script_id,),
                )
                row = await cur.fetchone()
                if row is None:
                    return None
                return _row_to_dict(_COLS, row)

    async def list_by_tenant(
        self,
        tenant_key: str,
        *,
        status: str | None = None,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        where = ["tenant_key = %s"]
        args: list[Any] = [tenant_key]
        if status:
            where.append("status = %s")
            args.append(status)
        if source:
            where.append("source = %s")
            args.append(source)
        sql = (
            f"SELECT {_SELECT_COLS} FROM fc_script "
            f"WHERE {' AND '.join(where)} ORDER BY id DESC"
        )
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, args)
                rows = await cur.fetchall()
                return [_row_to_dict(_COLS, r) for r in rows]

    async def update_segments(
        self, script_id: int, segments: list[dict]
    ) -> None:
        record = await self.get(script_id)
        if record is None:
            raise ValueError(f"script {script_id} not found")
        if record["status"] != "DRAFT":
            raise StatusConflictError(
                f"script {script_id} status={record['status']}, only DRAFT can edit"
            )
        segments_json = json.dumps(segments, ensure_ascii=False)
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE fc_script SET segments_json=%s, updated_at=%s WHERE id=%s",
                    (segments_json, _now(), script_id),
                )
                await conn.commit()

    async def update_status(self, script_id: int, status: str) -> None:
        if status not in ("DRAFT", "CONFIRMED"):
            raise ValueError(f"invalid status: {status}")
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE fc_script SET status=%s, updated_at=%s WHERE id=%s",
                    (status, _now(), script_id),
                )
                await conn.commit()
```

- [ ] **Step 5: 跑测试确认 PASS**

```bash
cd SimpleClaw && uv run pytest tests/test_script_repo.py -v -m unit
```
预期：4 个测试全 PASS。

- [ ] **Step 6: Commit**

```bash
git add SimpleClaw/Flowcut/storage/script_repo.py SimpleClaw/tests/test_script_repo.py
git commit -m "feat: ScriptRepository v2 (source/reference_video_id/status)"
```

### Task 1.4: ReferenceVideoRepository 加新字段读写

**Files:**
- Modify: `SimpleClaw/Flowcut/storage/reference_video_repo.py`

- [ ] **Step 1: 找到现有 ReferenceVideoRepository**

Read `SimpleClaw/Flowcut/storage/reference_video_repo.py` 全文。

- [ ] **Step 2: 在 SELECT 字段中加入 audio_oss_key、script_id**

找到所有 `SELECT id, tenant_key, ...` 这种字段列表，把 `audio_oss_key, script_id` 加到末尾。如果用了常量 `_COLS`，更新它。

- [ ] **Step 3: 新增两个 update 方法**

在类末尾追加：

```python
    async def set_audio(self, ref_video_id: int, audio_oss_key: str) -> None:
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE fc_reference_video SET audio_oss_key=%s, updated_at=%s WHERE id=%s",
                    (audio_oss_key, _now(), ref_video_id),
                )
                await conn.commit()

    async def set_script_id(self, ref_video_id: int, script_id: int) -> None:
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE fc_reference_video SET script_id=%s, updated_at=%s WHERE id=%s",
                    (script_id, _now(), ref_video_id),
                )
                await conn.commit()
```

如果 `_now` 在该文件没定义，加上：

```python
from datetime import datetime, timezone

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
```

- [ ] **Step 4: Commit**

```bash
git add SimpleClaw/Flowcut/storage/reference_video_repo.py
git commit -m "feat: ReferenceVideoRepository 支持 audio_oss_key / script_id 字段"
```

---

## Phase 2: 后端服务改造

### Task 2.1: material_matcher 改为 visual/copy 双 query

**Files:**
- Modify: `SimpleClaw/Flowcut/services/material_matcher.py::match_segment`
- Test: `SimpleClaw/tests/test_material_matcher.py`（扩展或新建）

- [ ] **Step 1: 写测试覆盖双 query 行为**

Create or append `SimpleClaw/tests/test_material_matcher.py`:

```python
"""material_matcher 双 query 行为测试。"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from Flowcut.services.material_matcher import match_segment


@pytest.mark.unit
async def test_match_segment_embeds_visual_and_copy_separately():
    seg = {"visual": "厨房", "copy": "洗脸的痛点"}
    embedding = AsyncMock()
    embedding.embed.side_effect = lambda text: [float(len(text))]  # 不同文本不同向量
    vector_store = AsyncMock()
    vector_store.search.return_value = []
    material_repo = AsyncMock()

    await match_segment(
        seg, tenant_key="t1", product="P",
        embedding_service=embedding,
        vector_store=vector_store,
        material_repo=material_repo,
    )

    # visual 和 copy 各 embed 一次
    assert embedding.embed.call_count == 2
    embedding.embed.assert_any_call("厨房")
    embedding.embed.assert_any_call("洗脸的痛点")
    # search 拿到的两个向量不同
    args, kwargs = vector_store.search.call_args
    visual_vec, copy_vec = args[0], args[1]
    assert visual_vec != copy_vec


@pytest.mark.unit
async def test_match_segment_visual_empty_uses_copy_only():
    seg = {"visual": "", "copy": "洗脸"}
    embedding = AsyncMock()
    embedding.embed.return_value = [0.1, 0.2]
    vector_store = AsyncMock()
    vector_store.search.return_value = []
    material_repo = AsyncMock()

    await match_segment(
        seg, tenant_key="t1", product="P",
        embedding_service=embedding,
        vector_store=vector_store,
        material_repo=material_repo,
    )

    # visual 为空，只 embed copy 一次
    assert embedding.embed.call_count == 1
    embedding.embed.assert_called_with("洗脸")


@pytest.mark.unit
async def test_match_segment_both_empty_returns_error():
    seg = {"visual": "", "copy": ""}
    embedding = AsyncMock()
    vector_store = AsyncMock()
    material_repo = AsyncMock()

    result = await match_segment(
        seg, tenant_key="t1", product="P",
        embedding_service=embedding,
        vector_store=vector_store,
        material_repo=material_repo,
    )

    assert result["error"] is not None
    assert result["phase1"] == []
    embedding.embed.assert_not_called()
```

- [ ] **Step 2: 跑测试确认 FAIL**

```bash
cd SimpleClaw && uv run pytest tests/test_material_matcher.py -v -m unit
```
预期：FAIL（现有实现读 `seg.description` 而非 `seg.visual` / `seg.copy`）。

- [ ] **Step 3: 改造 match_segment**

Modify `SimpleClaw/Flowcut/services/material_matcher.py::match_segment`：

替换函数体（保留签名）：

```python
async def match_segment(
    seg: dict,
    *,
    tenant_key: str,
    product: str,
    embedding_service,
    vector_store,
    material_repo,
    oss_client=None,
    limit: int = 3,
) -> dict:
    """对单个脚本段执行两阶段语义搜索。

    新版：visual 和 copy 各 embed 一次，分别查 desc_vec 和 transcript_vec。
    至少其中一个非空才能召回；都空时返回 error。
    """
    visual = (seg.get("visual") or "").strip()
    copy = (seg.get("copy") or "").strip()

    if not visual and not copy:
        return {"phase1": [], "phase2": [], "error": "段缺 visual 和 copy"}

    visual_vec = None
    copy_vec = None
    try:
        if visual:
            visual_vec = await embedding_service.embed(visual)
        if copy:
            copy_vec = await embedding_service.embed(copy)
    except Exception as exc:
        return {"phase1": [], "phase2": [], "error": f"embedding 失败：{exc}"}

    # vector_store.search 接受两个 query；缺一时传 None
    # 注意：vector_store 内部需要支持 None query（仅查另一路）
    try:
        raw = await vector_store.search(
            visual_vec, copy_vec,
            tenant_key=tenant_key,
            product=product,
            limit=limit,
        )
        phase1 = await _resolve_materials(raw, material_repo, oss_client=oss_client)
    except Exception as exc:
        return {"phase1": [], "phase2": [], "error": f"阶段一失败：{exc}"}

    phase2: list[dict] = []
    if len(phase1) < limit:
        need = limit - len(phase1)
        try:
            raw2 = await vector_store.search(
                visual_vec, copy_vec,
                tenant_key=tenant_key,
                product=None,
                limit=need,
            )
            phase1_ids = {r["id"] for r in phase1}
            phase2 = await _resolve_materials(
                [(mid, sc) for mid, sc in raw2 if mid not in phase1_ids],
                material_repo,
                oss_client=oss_client,
            )
        except Exception:
            pass

    return {"phase1": phase1, "phase2": phase2, "error": None}
```

- [ ] **Step 4: 检查 vector_store.search 是否支持 None query**

Run: `grep -n "async def search" SimpleClaw/Flowcut/storage/vector_store.py`
读对应代码：
- 如果当前签名要求两个非 None vector，需要支持 None（缺一路时跳过该路 query）
- 如果支持 None 直接 OK

如果不支持，修改 `vector_store.search`：

```python
async def search(
    self,
    desc_query_vector: list[float] | None,
    transcript_query_vector: list[float] | None,
    ...
):
    scores: dict[int, float] = {}
    if desc_query_vector is not None:
        # 查 desc_vec 池（原逻辑）
        ...
    if transcript_query_vector is not None:
        # 查 transcript_vec 池（原逻辑）
        ...
    # max-fusion 逻辑保留
```

- [ ] **Step 5: 跑测试确认 PASS**

```bash
cd SimpleClaw && uv run pytest tests/test_material_matcher.py -v -m unit
```
预期：3 个新测试 PASS（已有测试如使用 seg.description 需要更新或删除）。

- [ ] **Step 6: 跑全量 unit 测试看是否 regression**

```bash
cd SimpleClaw && uv run pytest -m unit
```
任何失败的旧测试，定位是否因为 seg.description → seg.visual 改名引起。如是，更新旧测试用 visual/copy 字段。

- [ ] **Step 7: Commit**

```bash
git add SimpleClaw/Flowcut/services/material_matcher.py SimpleClaw/Flowcut/storage/vector_store.py SimpleClaw/tests/test_material_matcher.py
git commit -m "feat: material_matcher 改造为 visual/copy 双 query"
```

---

## Phase 3: 4 个新 Tool

> **共同模式：** 每个 Tool 类用 TDD —— 先写单测覆盖 happy path 和关键校验，再写实现。所有 Tool 继承 `simpleclaw.tools.base.Tool`，参考 `Flowcut/tools/check_task_status.py` 的结构。

### Task 3.1: UploadScriptTool

**Files:**
- Create: `SimpleClaw/Flowcut/tools/upload_script.py`
- Test: `SimpleClaw/tests/test_upload_script_tool.py`

- [ ] **Step 1: 写测试**

Create `SimpleClaw/tests/test_upload_script_tool.py`:

```python
"""UploadScriptTool 单元测试。"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from Flowcut.tools.upload_script import UploadScriptTool


@pytest.mark.unit
async def test_upload_creates_script():
    script_repo = AsyncMock()
    script_repo.create.return_value = {"id": 7, "source": "uploaded"}
    tool = UploadScriptTool(script_repo=script_repo)

    result = await tool.execute(
        tenant_key="t1",
        segments=[{"visual": "v", "copy": "c"}],
    )

    assert result.ok is True
    assert "7" in result.content
    script_repo.create.assert_called_once()
    call_kwargs = script_repo.create.call_args.kwargs
    assert call_kwargs["source"] == "uploaded"
    assert call_kwargs["tenant_key"] == "t1"


@pytest.mark.unit
async def test_upload_rejects_empty_segments():
    script_repo = AsyncMock()
    tool = UploadScriptTool(script_repo=script_repo)

    result = await tool.execute(tenant_key="t1", segments=[])

    assert result.ok is False
    assert "至少一段" in result.content or "empty" in result.content.lower()
    script_repo.create.assert_not_called()


@pytest.mark.unit
async def test_upload_rejects_segment_both_empty():
    script_repo = AsyncMock()
    tool = UploadScriptTool(script_repo=script_repo)

    result = await tool.execute(
        tenant_key="t1",
        segments=[{"visual": "", "copy": ""}],
    )

    assert result.ok is False
    script_repo.create.assert_not_called()
```

- [ ] **Step 2: 跑测试看 FAIL**

```bash
cd SimpleClaw && uv run pytest tests/test_upload_script_tool.py -v -m unit
```

- [ ] **Step 3: 写 UploadScriptTool**

Create `SimpleClaw/Flowcut/tools/upload_script.py`:

```python
"""UploadScriptTool — 创建用户上传脚本（source=uploaded）。"""
from __future__ import annotations

from typing import TYPE_CHECKING

from simpleclaw.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from Flowcut.storage.script_repo import ScriptRepository


def _normalize_segment(seg: dict, idx: int) -> dict:
    visual = (seg.get("visual") or "").strip()
    copy = (seg.get("copy") or "").strip()
    return {
        "idx": idx,
        "start_time": float(seg.get("start_time") or 0.0),
        "end_time": float(seg.get("end_time") or 0.0),
        "visual": visual,
        "copy": copy,
    }


def _validate_segments(segments: list[dict]) -> str | None:
    if not segments:
        return "脚本至少需要一段"
    for i, seg in enumerate(segments):
        if not (seg.get("visual") or "").strip() and not (seg.get("copy") or "").strip():
            return f"第 {i} 段 visual 和 copy 都为空"
    return None


class UploadScriptTool(Tool):
    name = "upload_script"
    description = "上传一份用户编写的脚本（含画面与文案），创建后进入 DRAFT 状态。"
    parameters = {
        "type": "object",
        "properties": {
            "tenant_key": {"type": "string"},
            "segments": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "visual": {"type": "string"},
                        "copy": {"type": "string"},
                        "start_time": {"type": "number"},
                        "end_time": {"type": "number"},
                    },
                },
            },
        },
        "required": ["tenant_key", "segments"],
    }
    execution_mode = "inline"
    needs_followup = True

    def __init__(self, *, script_repo: "ScriptRepository") -> None:
        self._repo = script_repo

    async def execute(
        self, tenant_key: str, segments: list[dict], **kwargs
    ) -> ToolResult:
        err = _validate_segments(segments)
        if err:
            return ToolResult(content=err, ok=False)

        normalized = [_normalize_segment(s, i) for i, s in enumerate(segments)]
        record = await self._repo.create(
            tenant_key=tenant_key,
            source="uploaded",
            segments=normalized,
        )
        return ToolResult(
            content=f"脚本已创建：script_id={record['id']}",
            ok=True,
        )
```

- [ ] **Step 4: 跑测试 PASS**

```bash
cd SimpleClaw && uv run pytest tests/test_upload_script_tool.py -v -m unit
```

- [ ] **Step 5: Commit**

```bash
git add SimpleClaw/Flowcut/tools/upload_script.py SimpleClaw/tests/test_upload_script_tool.py
git commit -m "feat: UploadScriptTool"
```

### Task 3.2: UpdateScriptTool

**Files:**
- Create: `SimpleClaw/Flowcut/tools/update_script.py`
- Test: `SimpleClaw/tests/test_update_script_tool.py`

- [ ] **Step 1: 写测试**

Create `SimpleClaw/tests/test_update_script_tool.py`:

```python
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from Flowcut.storage.script_repo import StatusConflictError
from Flowcut.tools.update_script import UpdateScriptTool


@pytest.mark.unit
async def test_update_success():
    repo = AsyncMock()
    repo.update_segments.return_value = None
    tool = UpdateScriptTool(script_repo=repo)

    result = await tool.execute(
        script_id=1,
        segments=[{"visual": "v", "copy": "c"}],
    )

    assert result.ok is True
    repo.update_segments.assert_called_once()


@pytest.mark.unit
async def test_update_rejects_confirmed():
    repo = AsyncMock()
    repo.update_segments.side_effect = StatusConflictError("not DRAFT")
    tool = UpdateScriptTool(script_repo=repo)

    result = await tool.execute(
        script_id=1,
        segments=[{"visual": "v", "copy": "c"}],
    )

    assert result.ok is False
    assert "DRAFT" in result.content
```

- [ ] **Step 2: 跑测试看 FAIL**

```bash
cd SimpleClaw && uv run pytest tests/test_update_script_tool.py -v -m unit
```

- [ ] **Step 3: 写 UpdateScriptTool**

Create `SimpleClaw/Flowcut/tools/update_script.py`:

```python
"""UpdateScriptTool — 更新脚本 segments（仅 DRAFT）。"""
from __future__ import annotations

from typing import TYPE_CHECKING

from simpleclaw.tools.base import Tool, ToolResult

# 共用校验 / 归一化函数，避免在 update_script.py 里重复实现
from Flowcut.tools.upload_script import _normalize_segment, _validate_segments

if TYPE_CHECKING:
    from Flowcut.storage.script_repo import ScriptRepository


class UpdateScriptTool(Tool):
    name = "update_script"
    description = "更新脚本的 segments；仅当 status=DRAFT 允许更新。"
    parameters = {
        "type": "object",
        "properties": {
            "script_id": {"type": "integer"},
            "segments": {"type": "array"},
        },
        "required": ["script_id", "segments"],
    }
    execution_mode = "inline"
    needs_followup = True

    def __init__(self, *, script_repo: "ScriptRepository") -> None:
        self._repo = script_repo

    async def execute(
        self, script_id: int, segments: list[dict], **kwargs
    ) -> ToolResult:
        err = _validate_segments(segments)
        if err:
            return ToolResult(content=err, ok=False)

        normalized = [_normalize_segment(s, i) for i, s in enumerate(segments)]
        try:
            await self._repo.update_segments(script_id, normalized)
        except Exception as exc:
            # StatusConflictError 或 ValueError(not found)
            return ToolResult(
                content=f"更新失败：{exc}（请先确认脚本状态为 DRAFT）",
                ok=False,
            )
        return ToolResult(content=f"脚本 {script_id} 已更新", ok=True)
```

- [ ] **Step 4: 跑测试 PASS**

```bash
cd SimpleClaw && uv run pytest tests/test_update_script_tool.py -v -m unit
```

- [ ] **Step 5: Commit**

```bash
git add SimpleClaw/Flowcut/tools/update_script.py SimpleClaw/tests/test_update_script_tool.py
git commit -m "feat: UpdateScriptTool"
```

### Task 3.3: MatchByScriptTool

**Files:**
- Create: `SimpleClaw/Flowcut/tools/match_by_script.py`
- Test: `SimpleClaw/tests/test_match_by_script_tool.py`

- [ ] **Step 1: 写测试**

Create `SimpleClaw/tests/test_match_by_script_tool.py`:

```python
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from Flowcut.tools.match_by_script import MatchByScriptTool


@pytest.mark.unit
async def test_match_calls_matcher_with_script_segments():
    script_repo = AsyncMock()
    script_repo.get.return_value = {
        "id": 1,
        "status": "CONFIRMED",
        "segments": [
            {"idx": 0, "visual": "v", "copy": "c", "start_time": 0, "end_time": 3},
        ],
    }
    embedding = AsyncMock()
    vector_store = AsyncMock()
    material_repo = AsyncMock()

    tool = MatchByScriptTool(
        script_repo=script_repo,
        embedding_service=embedding,
        vector_store=vector_store,
        material_repo=material_repo,
        oss_client=None,
    )

    with patch(
        "Flowcut.tools.match_by_script.match_segments_parallel",
        new=AsyncMock(return_value=[{"index": 0, "phase1": [], "phase2": [], "error": None}]),
    ) as mock_matcher:
        result = await tool.execute(script_id=1, product="P", tenant_key="t1")

    assert result.ok is True
    mock_matcher.assert_called_once()


@pytest.mark.unit
async def test_match_rejects_draft_script():
    script_repo = AsyncMock()
    script_repo.get.return_value = {
        "id": 1, "status": "DRAFT", "segments": [],
    }
    tool = MatchByScriptTool(
        script_repo=script_repo,
        embedding_service=AsyncMock(),
        vector_store=AsyncMock(),
        material_repo=AsyncMock(),
        oss_client=None,
    )

    result = await tool.execute(script_id=1, product="P", tenant_key="t1")

    assert result.ok is False
    assert "CONFIRMED" in result.content


@pytest.mark.unit
async def test_match_missing_script():
    script_repo = AsyncMock()
    script_repo.get.return_value = None
    tool = MatchByScriptTool(
        script_repo=script_repo,
        embedding_service=AsyncMock(),
        vector_store=AsyncMock(),
        material_repo=AsyncMock(),
        oss_client=None,
    )

    result = await tool.execute(script_id=99, product="P", tenant_key="t1")

    assert result.ok is False
    assert "不存在" in result.content or "not found" in result.content.lower()
```

- [ ] **Step 2: 跑测试看 FAIL**

```bash
cd SimpleClaw && uv run pytest tests/test_match_by_script_tool.py -v -m unit
```

- [ ] **Step 3: 写 MatchByScriptTool**

Create `SimpleClaw/Flowcut/tools/match_by_script.py`:

```python
"""MatchByScriptTool — 按脚本驱动素材召回。"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from simpleclaw.tools.base import Tool, ToolResult

from Flowcut.services.material_matcher import match_segments_parallel

if TYPE_CHECKING:
    from Flowcut.storage.script_repo import ScriptRepository
    from Flowcut.services.embedding import EmbeddingService
    from Flowcut.storage.vector_store import VectorStore
    from Flowcut.storage.material_repo import MaterialRepository
    from Flowcut.storage.oss_client import OSSClient


class MatchByScriptTool(Tool):
    name = "match_by_script"
    description = "根据已确认的脚本（status=CONFIRMED）逐段召回素材。"
    parameters = {
        "type": "object",
        "properties": {
            "script_id": {"type": "integer"},
            "product": {"type": "string"},
            "tenant_key": {"type": "string"},
        },
        "required": ["script_id", "tenant_key"],
    }
    execution_mode = "inline"
    needs_followup = True

    def __init__(
        self,
        *,
        script_repo: "ScriptRepository",
        embedding_service: "EmbeddingService",
        vector_store: "VectorStore",
        material_repo: "MaterialRepository",
        oss_client: "OSSClient | None",
    ) -> None:
        self._repo = script_repo
        self._embedding = embedding_service
        self._vector_store = vector_store
        self._material_repo = material_repo
        self._oss = oss_client

    async def execute(
        self,
        script_id: int,
        tenant_key: str,
        product: str = "",
        **kwargs,
    ) -> ToolResult:
        script = await self._repo.get(script_id)
        if script is None:
            return ToolResult(content=f"脚本 {script_id} 不存在", ok=False)
        if script["status"] != "CONFIRMED":
            return ToolResult(
                content=f"脚本 {script_id} 状态={script['status']}，请先 CONFIRMED",
                ok=False,
            )

        results = await match_segments_parallel(
            script["segments"],
            tenant_key=tenant_key,
            product=product,
            embedding_service=self._embedding,
            vector_store=self._vector_store,
            material_repo=self._material_repo,
            oss_client=self._oss,
        )

        # 返回结构化 JSON 给 ReactLoop / REST 路由
        return ToolResult(
            content=json.dumps({"results": results}, ensure_ascii=False),
            ok=True,
        )
```

- [ ] **Step 4: 跑测试 PASS**

```bash
cd SimpleClaw && uv run pytest tests/test_match_by_script_tool.py -v -m unit
```

- [ ] **Step 5: Commit**

```bash
git add SimpleClaw/Flowcut/tools/match_by_script.py SimpleClaw/tests/test_match_by_script_tool.py
git commit -m "feat: MatchByScriptTool"
```

### Task 3.4: ExportPackageTool

**Files:**
- Create: `SimpleClaw/Flowcut/tools/export_package.py`
- Test: `SimpleClaw/tests/test_export_package_tool.py`
- Modify: `SimpleClaw/Flowcut/runtime/streams.py` —— 加常量

- [ ] **Step 1: 在 streams.py 加 EXPORT_PACKAGE 常量**

Edit `SimpleClaw/Flowcut/runtime/streams.py`，在 `FlowcutTaskStream` 类末尾追加：

```python
    EXPORT_PACKAGE     = "flowcut:export_package"      # 素材打包导出（zip）
```

- [ ] **Step 2: 写测试**

Create `SimpleClaw/tests/test_export_package_tool.py`:

```python
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from Flowcut.tools.export_package import ExportPackageTool


@pytest.mark.unit
async def test_export_submits_task():
    runtime = MagicMock()
    runtime.submit_task = AsyncMock(return_value="task-123")
    tool = ExportPackageTool(runtime=runtime)

    envelope = await tool.prepare_task(
        script_id=1, material_ids=[10, 11], tenant_key="t1",
    )

    assert envelope.task_type == "export_package"
    assert envelope.stream == "flowcut:export_package"
    assert envelope.payload["script_id"] == 1
    assert envelope.payload["material_ids"] == [10, 11]


@pytest.mark.unit
async def test_export_rejects_empty_materials():
    runtime = MagicMock()
    tool = ExportPackageTool(runtime=runtime)

    # prepare_task 不入队也不抛错的情况下，由路由层处理空 material_ids 的 422
    # 这里测 ToolResult 路径（execute 不应被 durable 工具调用，但保留校验）
    with pytest.raises(ValueError):
        await tool.prepare_task(script_id=1, material_ids=[], tenant_key="t1")
```

- [ ] **Step 3: 跑测试看 FAIL**

```bash
cd SimpleClaw && uv run pytest tests/test_export_package_tool.py -v -m unit
```

- [ ] **Step 4: 写 ExportPackageTool**

Create `SimpleClaw/Flowcut/tools/export_package.py`:

```python
"""ExportPackageTool — 打包脚本+素材+音频+原视频为 zip，异步任务。"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from simpleclaw.runtime.task_protocol import TaskEnvelope
from simpleclaw.tools.base import Tool, ToolResult

from Flowcut.runtime.streams import FlowcutTaskStream

if TYPE_CHECKING:
    from simpleclaw.runtime.services import RuntimeServices


class ExportPackageTool(Tool):
    name = "export_package"
    description = (
        "把脚本 + 选中素材 + 音频 + 原爆款视频打包成 zip，异步任务。"
        "成功后返回 task_id，前端轮询 /flowcut/tasks/{task_id} 拿下载链接。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "script_id": {"type": "integer"},
            "material_ids": {
                "type": "array",
                "items": {"type": "integer"},
            },
            "tenant_key": {"type": "string"},
        },
        "required": ["script_id", "material_ids", "tenant_key"],
    }
    execution_mode = "durable"
    needs_followup = True

    def __init__(self, *, runtime: "RuntimeServices") -> None:
        self._runtime = runtime

    async def prepare_task(
        self,
        script_id: int,
        material_ids: list[int],
        tenant_key: str,
        **kwargs,
    ) -> TaskEnvelope:
        if not material_ids:
            raise ValueError("material_ids 不能为空")

        task_id = f"export-{uuid.uuid4().hex[:12]}"
        return TaskEnvelope(
            task_id=task_id,
            task_type="export_package",
            tenant_key=tenant_key,
            stream=FlowcutTaskStream.EXPORT_PACKAGE,
            scope_key=f"export:{script_id}:{task_id}",
            payload={
                "script_id": script_id,
                "material_ids": material_ids,
            },
        )
```

- [ ] **Step 5: 跑测试 PASS**

```bash
cd SimpleClaw && uv run pytest tests/test_export_package_tool.py -v -m unit
```

- [ ] **Step 6: Commit**

```bash
git add SimpleClaw/Flowcut/tools/export_package.py SimpleClaw/Flowcut/runtime/streams.py SimpleClaw/tests/test_export_package_tool.py
git commit -m "feat: ExportPackageTool + EXPORT_PACKAGE stream 常量"
```

---

## Phase 4: SCENE_DECOMPOSE executor 改造

### Task 4.1: 抽音轨 + 写 audio_oss_key

**Files:**
- Modify: `SimpleClaw/Flowcut/runtime/executors.py::make_scene_decompose_executor`

- [ ] **Step 1: 找到 scene_decompose executor 当前实现**

Run: `grep -n "make_scene_decompose_executor" SimpleClaw/Flowcut/runtime/executors.py`
Read 整个函数体。

- [ ] **Step 2: 在 scene 分段完成后、return 之前插入抽音轨逻辑**

在 Gemini 分段 + PySceneDetect 对齐之后，加入：

```python
# === 抽音轨 ===
try:
    audio_local = local_video_path.with_suffix(".audio.mp3")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(local_video_path),
         "-vn", "-acodec", "libmp3lame", "-q:a", "4",
         str(audio_local)],
        check=True, capture_output=True,
    )
    audio_key = f"reference_videos/{tenant_key}/{ref_video_id}/audio.mp3"
    oss_client.upload(str(audio_local), audio_key)
    await ref_video_repo.set_audio(ref_video_id, audio_key)
except Exception as exc:
    logger.warning(f"抽音轨失败: {exc}")
    # 不阻塞拆镜整体
```

注意：实际参数名、变量名按现有 executor 代码中的命名调整。`ref_video_repo`、`oss_client` 是 executor 闭包内已经有的依赖。

- [ ] **Step 3: 启动 server 跑一次拆镜验证**

```bash
cd SimpleClaw && ./uvdev.sh
```
另开窗口上传一个短视频跑拆镜，结束后查询数据库：
```bash
mysql -e "SELECT id, audio_oss_key FROM fc_reference_video ORDER BY id DESC LIMIT 1;"
```
预期：audio_oss_key 非 NULL。

- [ ] **Step 4: Commit**

```bash
git add SimpleClaw/Flowcut/runtime/executors.py
git commit -m "feat: SCENE_DECOMPOSE 增产音频抽轨，写 fc_reference_video.audio_oss_key"
```

### Task 4.2: ASR 按段截取 copy 字段

**实现路径取决于 Task 0.1 spike 结论：**

#### 情况 A：词级时间戳可用

- [ ] **Step 1: 改造 ASR 结果解析**

在现有 ASR 调用代码后，把 ASR 词序列按 `scene_data[i].start_time/end_time` 时间窗截取，每段拼接成一个 `copy` 字符串。

伪代码示意（实际位置在 executor 内部）：

```python
# words: [{"text": "每", "start": 0.0, "end": 0.12}, ...]
def slice_words_for_segment(words: list[dict], start: float, end: float) -> str:
    return "".join(w["text"] for w in words if w["start"] >= start and w["end"] <= end)

for seg in scene_data:
    seg["copy"] = slice_words_for_segment(asr_words, seg["start_time"], seg["end_time"])
```

#### 情况 B：词级不可用，走 Gemini fallback

- [ ] **Step 1: 改造 Gemini prompt（services/gemini_video.py）**

在 `analyze_video` 的 prompt 中，要求 Gemini 在 JSON 中每段除 `content` 外还输出 `copy` 字段（"这段时间内视频中说的话/口播文字"）。

```python
prompt = """...
请按场景切分该视频，对每个场景输出 JSON:
[
  {
    "start_time": 0.0,
    "end_time": 3.5,
    "content": "<画面描述>",
    "copy": "<这段时间视频中的口播原文>"  // 新增
  },
  ...
]
"""
```

在 executor 中：

```python
for seg in scene_data:
    seg.setdefault("copy", "")  # Gemini 没给时降级为空
```

#### 共同后续

- [ ] **Step 2: 验证 scene_data 含 copy 字段**

跑一次拆镜：
```bash
mysql -e "SELECT scene_data_json FROM fc_reference_video ORDER BY id DESC LIMIT 1\\G" | head -50
```
预期：每段含 copy 字段。

- [ ] **Step 3: Commit**

```bash
git add SimpleClaw/Flowcut/runtime/executors.py SimpleClaw/Flowcut/services/gemini_video.py
git commit -m "feat: scene_data 每段补 copy 字段（ASR 截取 或 Gemini fallback）"
```

### Task 4.3: 拆镜产物写 fc_script + 回填 script_id

**Files:**
- Modify: `SimpleClaw/Flowcut/runtime/executors.py::make_scene_decompose_executor`

- [ ] **Step 1: 在 scene_data 完成写入 fc_reference_video 后，新增 fc_script 写入**

```python
# === 产出 fc_script ===
script_segments = [
    {
        "idx": i,
        "start_time": float(seg.get("start_time", 0)),
        "end_time": float(seg.get("end_time", 0)),
        "visual": seg.get("content", ""),
        "copy": seg.get("copy", ""),
    }
    for i, seg in enumerate(scene_data)
]
script_record = await script_repo.create(
    tenant_key=tenant_key,
    source="decomposed",
    reference_video_id=ref_video_id,
    segments=script_segments,
)
await ref_video_repo.set_script_id(ref_video_id, script_record["id"])
```

注意：`script_repo` 需要从 executor 工厂函数参数注入（看现有 executor 工厂函数签名，按相同模式加参数）。

- [ ] **Step 2: 改 executor 工厂签名**

找到 `make_scene_decompose_executor(...)` 工厂函数签名，加入 `script_repo: ScriptRepository`。

- [ ] **Step 3: 改 worker 工厂传递 script_repo**

Edit `SimpleClaw/Flowcut/runtime/worker.py::make_workers`，确保 script_repo 被传入 `make_scene_decompose_executor`：

```python
SCENE_DECOMPOSE_WORKER = TaskWorker(
    stream=FlowcutTaskStream.SCENE_DECOMPOSE,
    executor=make_scene_decompose_executor(
        ...,
        script_repo=script_repo,  # 新增
    ),
    ...
)
```

并在 `make_workers` 参数列表加 `script_repo`。

- [ ] **Step 4: 改 container.py 传递 script_repo**

Edit `SimpleClaw/Flowcut/api/container.py`，构造 ScriptRepository 实例并传给 make_workers：

```python
from Flowcut.storage.script_repo import ScriptRepository

script_repo = ScriptRepository(db)
# ... 在 make_workers 调用处加 script_repo=script_repo
```

- [ ] **Step 5: 启动 server 跑一次拆镜端到端验证**

```bash
cd SimpleClaw && ./uvdev.sh
# 另开窗口上传短视频
mysql -e "SELECT id, source, reference_video_id, status, segments_json FROM fc_script ORDER BY id DESC LIMIT 1\\G"
mysql -e "SELECT id, script_id, audio_oss_key FROM fc_reference_video ORDER BY id DESC LIMIT 1\\G"
```
预期：fc_script 有新行（source=decomposed），fc_reference_video.script_id 关联。

- [ ] **Step 6: Commit**

```bash
git add SimpleClaw/Flowcut/runtime/executors.py SimpleClaw/Flowcut/runtime/worker.py SimpleClaw/Flowcut/api/container.py
git commit -m "feat: SCENE_DECOMPOSE 增产 fc_script 记录并回填 reference_video.script_id"
```

---

## Phase 5: EXPORT_PACKAGE stream + executor + worker

### Task 5.1: make_export_package_executor

**Files:**
- Modify: `SimpleClaw/Flowcut/runtime/executors.py` —— 新增工厂函数

- [ ] **Step 1: 在 executors.py 末尾追加 export 工厂**

```python
def make_export_package_executor(
    *,
    script_repo: "ScriptRepository",
    material_repo: "MaterialRepository",
    ref_video_repo: "ReferenceVideoRepository",
    oss_client: "OSSClient",
) -> Callable[[TaskEnvelope], Awaitable[TaskExecutionResult]]:
    """打包 zip：script.json/md + clips/*.mp4 + audio.mp3 + reference.mp4。"""
    import shutil
    import tempfile
    import zipfile
    from pathlib import Path

    async def _executor(task: TaskEnvelope) -> TaskExecutionResult:
        script_id = task.payload["script_id"]
        material_ids = task.payload["material_ids"]
        tenant_key = task.tenant_key

        script = await script_repo.get(script_id)
        if script is None:
            return TaskExecutionResult(ok=False, error=f"script {script_id} not found")

        ref_video = None
        if script["source"] == "decomposed" and script.get("reference_video_id"):
            ref_video = await ref_video_repo.get(script["reference_video_id"])

        workdir = Path(tempfile.mkdtemp(prefix=f"flowcut-export-{script_id}-"))
        missing: list[int] = []
        try:
            # 1) clips/
            clips_dir = workdir / "clips"
            clips_dir.mkdir()
            for i, mid in enumerate(material_ids):
                mat = await material_repo.get(mid)
                if mat is None or not mat.get("oss_key"):
                    missing.append(mid)
                    continue
                ext = ".mp4"
                local = clips_dir / f"seg{i:02d}_{mid}{ext}"
                try:
                    oss_client.download(mat["oss_key"], str(local))
                except Exception:
                    missing.append(mid)

            # 2) audio.mp3（仅 decomposed 且 audio_oss_key 非空）
            if ref_video and ref_video.get("audio_oss_key"):
                try:
                    oss_client.download(ref_video["audio_oss_key"], str(workdir / "audio.mp3"))
                except Exception:
                    pass

            # 3) reference.mp4
            if ref_video and ref_video.get("oss_key"):
                try:
                    oss_client.download(ref_video["oss_key"], str(workdir / "reference.mp4"))
                except Exception:
                    pass

            # 4) script.json
            import json as _json
            (workdir / "script.json").write_text(
                _json.dumps(script, ensure_ascii=False, indent=2,
                            default=str),
                encoding="utf-8",
            )

            # 5) script.md（人读版）
            md_lines = [f"# 脚本 {script_id}\n"]
            for seg in script["segments"]:
                md_lines.append(f"## 段 {seg.get('idx', 0)} ({seg.get('start_time', 0):.2f}s - {seg.get('end_time', 0):.2f}s)\n")
                md_lines.append(f"**画面**：{seg.get('visual', '')}\n")
                md_lines.append(f"**文案**：{seg.get('copy', '')}\n")
            (workdir / "script.md").write_text("\n".join(md_lines), encoding="utf-8")

            # 6) missing_materials.txt
            if missing:
                (workdir / "missing_materials.txt").write_text(
                    "\n".join(str(m) for m in missing), encoding="utf-8"
                )

            # 7) 打 zip
            import time
            ts = int(time.time())
            zip_path = workdir.parent / f"export_{ts}_{script_id}.zip"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in workdir.rglob("*"):
                    if f.is_file():
                        zf.write(f, f.relative_to(workdir))

            # 8) 上传 OSS
            export_key = f"exports/{tenant_key}/{ts}_{script_id}.zip"
            oss_client.upload(str(zip_path), export_key)
            result_url = oss_client.presigned_get_url(export_key, expires=24 * 3600)

            return TaskExecutionResult(ok=True, result_url=result_url)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    return _executor
```

注意：
- `TaskExecutionResult` 的具体字段名按 simpleclaw 当前实现校对（`result_url` 字段可能叫 `result` 或在 payload 里）
- `oss_client.download` 是否存在？如不存在，用 presigned_get_url + httpx 下载

- [ ] **Step 2: 在 worker.py 注册 EXPORT_PACKAGE worker**

Edit `SimpleClaw/Flowcut/runtime/worker.py::make_workers`，加入：

```python
from Flowcut.runtime.executors import make_export_package_executor

EXPORT_WORKER = TaskWorker(
    stream=FlowcutTaskStream.EXPORT_PACKAGE,
    executor=make_export_package_executor(
        script_repo=script_repo,
        material_repo=material_repo,
        ref_video_repo=ref_video_repo,
        oss_client=oss_client,
    ),
    task_queue=task_queue,
    scope_locks=task_scope_locks,
    state_store=task_state_store,
)
# 加入 workers 列表
workers.append(EXPORT_WORKER)
```

- [ ] **Step 3: 写集成测试（mock OSS）**

Create `SimpleClaw/tests/test_export_package_e2e.py`:

```python
"""ExportPackage executor 集成测试（mock OSS）。"""
from __future__ import annotations

import os
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.runtime.task_protocol import TaskEnvelope
from Flowcut.runtime.executors import make_export_package_executor


@pytest.mark.integration
async def test_export_produces_valid_zip(tmp_path):
    script_repo = AsyncMock()
    script_repo.get.return_value = {
        "id": 1, "source": "uploaded", "segments": [
            {"idx": 0, "start_time": 0, "end_time": 3, "visual": "v", "copy": "c"}
        ],
    }
    material_repo = AsyncMock()
    material_repo.get.return_value = {"id": 10, "oss_key": "fake/10.mp4"}
    ref_video_repo = AsyncMock()

    # 准备一个 fake 本地视频
    fake_clip = tmp_path / "fake.mp4"
    fake_clip.write_bytes(b"\x00" * 100)

    oss_client = MagicMock()
    def fake_download(key, dst):
        Path(dst).write_bytes(b"\x00" * 100)
    oss_client.download = fake_download
    oss_client.upload = MagicMock()
    oss_client.presigned_get_url = MagicMock(return_value="https://oss/url.zip")

    executor = make_export_package_executor(
        script_repo=script_repo,
        material_repo=material_repo,
        ref_video_repo=ref_video_repo,
        oss_client=oss_client,
    )

    task = TaskEnvelope(
        task_id="t1",
        task_type="export_package",
        tenant_key="t1",
        stream="flowcut:export_package",
        scope_key="export:1:t1",
        payload={"script_id": 1, "material_ids": [10]},
    )

    result = await executor(task)
    assert result.ok is True
    oss_client.upload.assert_called_once()
```

- [ ] **Step 4: 跑测试 PASS**

```bash
cd SimpleClaw && uv run pytest tests/test_export_package_e2e.py -v -m integration
```

- [ ] **Step 5: Commit**

```bash
git add SimpleClaw/Flowcut/runtime/executors.py SimpleClaw/Flowcut/runtime/worker.py SimpleClaw/tests/test_export_package_e2e.py
git commit -m "feat: make_export_package_executor + EXPORT_PACKAGE worker"
```

---

## Phase 6: REST 路由 + container 装配

### Task 6.1: 新建 scripts 路由文件

**Files:**
- Create: `SimpleClaw/Flowcut/api/routes/scripts.py`

- [ ] **Step 1: 写 scripts.py**

> **注：** 现有 container 用 `tool_factories: list[Callable]`，不是 `tools: dict`。路由里不能用 `c.tools[name]`，要么从 container 的具体 attribute（如 `c.script_repo`、`c.runtime`）取依赖直接构造 Tool 实例，要么调底层 service。本路由文件采用**直接构造 Tool 实例 + 调依赖**的混合方案。

Create `SimpleClaw/Flowcut/api/routes/scripts.py`:

```python
"""脚本相关路由：/flowcut/scripts/..."""
from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from Flowcut.storage.script_repo import StatusConflictError
from Flowcut.tools.upload_script import UploadScriptTool
from Flowcut.tools.update_script import UpdateScriptTool
from Flowcut.tools.match_by_script import MatchByScriptTool
from Flowcut.tools.export_package import ExportPackageTool

router = APIRouter(prefix="/flowcut/scripts", tags=["flowcut-scripts"])


def _c(request: Request):
    return request.app.state.container


@router.post("")
async def upload_script(request: Request) -> dict[str, Any]:
    payload = await request.json()
    tenant_key = (payload.get("tenant_key") or "").strip()
    segments = payload.get("segments") or []
    if not tenant_key:
        raise HTTPException(422, "tenant_key 必填")

    c = _c(request)
    tool = UploadScriptTool(script_repo=c.script_repo)
    result = await tool.execute(tenant_key=tenant_key, segments=segments)
    if not result.ok:
        raise HTTPException(422, result.content)
    m = re.search(r"script_id=(\d+)", result.content)
    return {"ok": True, "script_id": int(m.group(1)) if m else None}


@router.get("")
async def list_scripts(
    request: Request,
    tenant_key: str,
    source: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    c = _c(request)
    scripts = await c.script_repo.list_by_tenant(
        tenant_key, source=source, status=status
    )
    return {"ok": True, "scripts": scripts}


@router.get("/{script_id}")
async def get_script(request: Request, script_id: int) -> dict[str, Any]:
    c = _c(request)
    script = await c.script_repo.get(script_id)
    if script is None:
        raise HTTPException(404, f"script {script_id} not found")
    return {"ok": True, **script}


@router.patch("/{script_id}")
async def update_script(request: Request, script_id: int) -> dict[str, Any]:
    payload = await request.json()
    segments = payload.get("segments") or []
    c = _c(request)
    tool = UpdateScriptTool(script_repo=c.script_repo)
    result = await tool.execute(script_id=script_id, segments=segments)
    if not result.ok:
        code = 409 if "DRAFT" in result.content else 422
        raise HTTPException(code, result.content)
    return {"ok": True}


@router.post("/{script_id}/confirm")
async def confirm_script(request: Request, script_id: int) -> dict[str, Any]:
    c = _c(request)
    await c.script_repo.update_status(script_id, "CONFIRMED")
    return {"ok": True, "status": "CONFIRMED"}


@router.post("/{script_id}/reopen")
async def reopen_script(request: Request, script_id: int) -> dict[str, Any]:
    c = _c(request)
    await c.script_repo.update_status(script_id, "DRAFT")
    return {"ok": True, "status": "DRAFT"}


@router.post("/{script_id}/match")
async def match_script(request: Request, script_id: int) -> dict[str, Any]:
    payload = await request.json()
    tenant_key = (payload.get("tenant_key") or "").strip()
    product = payload.get("product") or ""
    if not tenant_key:
        raise HTTPException(422, "tenant_key 必填")

    c = _c(request)
    tool = MatchByScriptTool(
        script_repo=c.script_repo,
        embedding_service=c.embedding_service,
        vector_store=c.vector_store,
        material_repo=c.material_repo,
        oss_client=c.oss_client,
    )
    result = await tool.execute(
        script_id=script_id, tenant_key=tenant_key, product=product,
    )
    if not result.ok:
        raise HTTPException(400, result.content)
    data = json.loads(result.content)
    return {"ok": True, **data}


@router.post("/{script_id}/export")
async def export_script(request: Request, script_id: int) -> dict[str, Any]:
    payload = await request.json()
    material_ids = payload.get("material_ids") or []
    tenant_key = (payload.get("tenant_key") or "").strip()
    if not material_ids:
        raise HTTPException(422, "material_ids 不能为空")
    if not tenant_key:
        raise HTTPException(422, "tenant_key 必填")

    c = _c(request)
    tool = ExportPackageTool(runtime=c.runtime)
    envelope = await tool.prepare_task(
        script_id=script_id,
        material_ids=material_ids,
        tenant_key=tenant_key,
    )
    task_id = await c.runtime.submit_task(envelope)
    return {"ok": True, "task_id": task_id}
```

- [ ] **Step 2: Commit（路由独立但还未挂载，下个任务挂载）**

```bash
git add SimpleClaw/Flowcut/api/routes/scripts.py
git commit -m "feat: /flowcut/scripts/* 路由（待挂载）"
```

### Task 6.2: 新建 tasks 路由（轮询任务状态）

**Files:**
- Create: `SimpleClaw/Flowcut/api/routes/tasks.py`

- [ ] **Step 1: 写 tasks.py**

Create `SimpleClaw/Flowcut/api/routes/tasks.py`:

```python
"""任务状态查询：/flowcut/tasks/{task_id}"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/flowcut/tasks", tags=["flowcut-tasks"])


@router.get("/{task_id}")
async def get_task(request: Request, task_id: str) -> dict:
    c = request.app.state.container
    task = await c.task_repo.find_by_task_id(task_id)
    if task is None:
        raise HTTPException(404, f"task {task_id} not found")
    return {
        "ok": True,
        "task_id": task_id,
        "status": task.get("status"),
        "task_type": task.get("task_type"),
        "result_url": task.get("result_url"),
        "last_error": task.get("last_error"),
        "created_at": str(task.get("created_at")),
        "updated_at": str(task.get("updated_at")),
    }
```

注意：`result_url` 字段在 task_repo 是否已经写入？检查 `Flowcut/storage/task_repo.py`，如果 task 状态写入逻辑没保存 `result_url`，要补一下（在 worker 写入任务成功时把 executor 返回的 result_url 持久化）。

- [ ] **Step 2: 检查 task_repo 是否支持 result_url 字段**

Run: `grep -n "result_url\|result" SimpleClaw/Flowcut/storage/task_repo.py`
如缺少，加：
- DDL 表加列 `result_url VARCHAR(1024) NULL`（在 database.py 对应 task 表 DDL 加）
- task_repo 写入方法接受 result_url 参数

具体扩展点参考现有 task 持久化 schema。

- [ ] **Step 3: Commit**

```bash
git add SimpleClaw/Flowcut/api/routes/tasks.py SimpleClaw/Flowcut/storage/task_repo.py SimpleClaw/Flowcut/storage/database.py
git commit -m "feat: /flowcut/tasks/{id} 路由 + task_repo 支持 result_url"
```

### Task 6.3: container.py 注册 Tools + 挂路由

**Files:**
- Modify: `SimpleClaw/Flowcut/api/container.py`
- Modify: `SimpleClaw/Flowcut/api/server.py`

- [ ] **Step 1: container.py 注册 4 新 Tools 到 tool_factories**

`tool_factories` 是 list（不是 dict）。在现有 6 个 lambda 后追加 4 个：

```python
from Flowcut.tools.upload_script import UploadScriptTool
from Flowcut.tools.update_script import UpdateScriptTool
from Flowcut.tools.match_by_script import MatchByScriptTool
from Flowcut.tools.export_package import ExportPackageTool

tool_factories = [
    # ... 现有 6 个 lambda 保留 ...
    lambda _: UploadScriptTool(script_repo=script_repo),
    lambda _: UpdateScriptTool(script_repo=script_repo),
    lambda _: MatchByScriptTool(
        script_repo=script_repo,
        embedding_service=embedding_service,
        vector_store=vector_store,
        material_repo=material_repo,
        oss_client=oss_client,
    ),
    lambda _: ExportPackageTool(runtime=runtime),
]
```

注：这样 Agent 轨道（/agent/chat）可以调到新 Tool。路由轨道（/flowcut/scripts/*）通过 Task 6.1 里在路由内直接构造 Tool 实例。

- [ ] **Step 2: AppContainer 暴露依赖给路由**

在 `AppContainer` dataclass 中确保以下 attribute 已存在（如缺则添加）：

```python
@dataclass
class AppContainer:
    db: Database
    task_repo: RuntimeTaskRepository
    script_repo: ScriptRepository
    material_repo: MaterialRepository
    ref_video_repo: ReferenceVideoRepository
    embedding_service: EmbeddingService
    vector_store: VectorStore
    oss_client: OSSClient
    runtime: RuntimeServices
    main_agent: MainAgent
    # ... 其他现有字段
```

在 `build_container` 函数 return 时把所有上述对象传入 AppContainer 实例化。

- [ ] **Step 3: server.py 把 container 暴露到 app.state**

确认 `SimpleClaw/Flowcut/api/server.py` 启动时挂 container：

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    container = await build_container()
    app.state.container = container
    yield
    # ... cleanup
```

如果已经如此，跳过本步骤。

- [ ] **Step 4: server.py include 新路由**

Edit `SimpleClaw/Flowcut/api/server.py`：

```python
from Flowcut.api.routes.scripts import router as scripts_router
from Flowcut.api.routes.tasks import router as tasks_router

app.include_router(scripts_router)
app.include_router(tasks_router)
```

- [ ] **Step 5: 启动 server 并 curl 测试**

```bash
cd SimpleClaw && ./uvdev.sh
# 另开窗口
curl -X POST http://localhost:8001/flowcut/scripts \
  -H "Content-Type: application/json" \
  -d '{"tenant_key": "t1", "segments": [{"visual": "v", "copy": "c"}]}'
```
预期返回 `{"ok": true, "script_id": N}`。

```bash
curl http://localhost:8001/flowcut/scripts/N
```
预期返回完整脚本对象。

```bash
curl -X PATCH http://localhost:8001/flowcut/scripts/N \
  -H "Content-Type: application/json" \
  -d '{"segments": [{"visual": "v2", "copy": "c2"}]}'
```
预期 `{"ok": true}`。

```bash
curl -X POST http://localhost:8001/flowcut/scripts/N/confirm
curl -X PATCH http://localhost:8001/flowcut/scripts/N \
  -H "Content-Type: application/json" \
  -d '{"segments": [{"visual": "v3", "copy": "c3"}]}'
```
预期第二个 PATCH 返回 409 Conflict。

- [ ] **Step 6: Commit**

```bash
git add SimpleClaw/Flowcut/api/container.py SimpleClaw/Flowcut/api/server.py
git commit -m "feat: container 注册 4 新 Tool + server 挂载 scripts/tasks 路由"
```

---

## Phase 7: 前端 types + store

### Task 7.1: 新增 script 类型定义

**Files:**
- Create or Modify: `flowcut_frontend/src/types/script.ts`

- [ ] **Step 1: 写类型**

Create `flowcut_frontend/src/types/script.ts`:

```typescript
export type ScriptSource = 'decomposed' | 'uploaded'
export type ScriptStatus = 'DRAFT' | 'CONFIRMED'

export interface ScriptSegment {
  idx: number
  start_time: number
  end_time: number
  visual: string
  copy: string
}

export interface Script {
  id: number
  tenant_key: string
  source: ScriptSource
  reference_video_id: number | null
  segments: ScriptSegment[]
  status: ScriptStatus
  created_at: string
  updated_at: string
}

export interface MatchedMaterial {
  material_id: number
  name: string
  score: number
  preview_url: string | null
  duration: number
  scene_role: string | null
}

export interface SegmentMatchResult {
  seg_idx: number
  visual: string
  copy: string
  phase1: MatchedMaterial[]
  phase2: MatchedMaterial[]
}

export interface TaskStatus {
  task_id: string
  status: 'pending' | 'running' | 'succeeded' | 'failed' | string
  result_url: string | null
  last_error: string | null
}
```

- [ ] **Step 2: Commit**

```bash
git add flowcut_frontend/src/types/script.ts
git commit -m "feat(fe): script 相关 TypeScript 类型定义"
```

### Task 7.2: scriptStore（Zustand）

**Files:**
- Create: `flowcut_frontend/src/stores/scriptStore.ts`

- [ ] **Step 1: 写 store**

Create `flowcut_frontend/src/stores/scriptStore.ts`:

```typescript
import { create } from 'zustand'
import type { Script, ScriptSegment, SegmentMatchResult } from '../types/script'

interface ScriptState {
  currentScript: Script | null
  matchResults: SegmentMatchResult[]
  selectedMaterials: Set<number>      // 默认全选；勾去就 remove
  exportTaskId: string | null

  setScript: (script: Script | null) => void
  updateSegments: (segments: ScriptSegment[]) => void
  setMatchResults: (results: SegmentMatchResult[]) => void
  toggleMaterial: (materialId: number) => void
  setExportTaskId: (taskId: string | null) => void
  reset: () => void
}

export const useScriptStore = create<ScriptState>((set) => ({
  currentScript: null,
  matchResults: [],
  selectedMaterials: new Set<number>(),
  exportTaskId: null,

  setScript: (script) => set({ currentScript: script }),
  updateSegments: (segments) => set((s) => s.currentScript
    ? { currentScript: { ...s.currentScript, segments } }
    : s),
  setMatchResults: (results) => set(() => {
    const ids = new Set<number>()
    for (const r of results) {
      for (const m of [...r.phase1, ...r.phase2]) ids.add(m.material_id)
    }
    return { matchResults: results, selectedMaterials: ids }
  }),
  toggleMaterial: (id) => set((s) => {
    const next = new Set(s.selectedMaterials)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return { selectedMaterials: next }
  }),
  setExportTaskId: (taskId) => set({ exportTaskId: taskId }),
  reset: () => set({
    currentScript: null,
    matchResults: [],
    selectedMaterials: new Set(),
    exportTaskId: null,
  }),
}))
```

- [ ] **Step 2: Commit**

```bash
git add flowcut_frontend/src/stores/scriptStore.ts
git commit -m "feat(fe): scriptStore（Zustand）管理脚本+召回+选中态"
```

### Task 7.3: API client

**Files:**
- Create: `flowcut_frontend/src/api/script.ts`

- [ ] **Step 1: 写 client**

Create `flowcut_frontend/src/api/script.ts`:

```typescript
import type { Script, ScriptSegment, SegmentMatchResult, TaskStatus } from '../types/script'

const BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8001'

interface UploadResp { ok: boolean; script_id: number }
interface MatchResp { ok: boolean; results: SegmentMatchResult[] }
interface ExportResp { ok: boolean; task_id: string }

async function jsonFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!resp.ok) {
    let detail = ''
    try { detail = (await resp.json()).detail || '' } catch {}
    throw new Error(`${resp.status}: ${detail || resp.statusText}`)
  }
  return resp.json()
}

export const scriptApi = {
  upload: (tenantKey: string, segments: Partial<ScriptSegment>[]) =>
    jsonFetch<UploadResp>('/flowcut/scripts', {
      method: 'POST',
      body: JSON.stringify({ tenant_key: tenantKey, segments }),
    }),

  get: (scriptId: number) =>
    jsonFetch<{ ok: boolean } & Script>(`/flowcut/scripts/${scriptId}`),

  update: (scriptId: number, segments: ScriptSegment[]) =>
    jsonFetch<{ ok: boolean }>(`/flowcut/scripts/${scriptId}`, {
      method: 'PATCH',
      body: JSON.stringify({ segments }),
    }),

  confirm: (scriptId: number) =>
    jsonFetch<{ ok: boolean }>(`/flowcut/scripts/${scriptId}/confirm`, {
      method: 'POST',
    }),

  reopen: (scriptId: number) =>
    jsonFetch<{ ok: boolean }>(`/flowcut/scripts/${scriptId}/reopen`, {
      method: 'POST',
    }),

  match: (scriptId: number, tenantKey: string, product = '') =>
    jsonFetch<MatchResp>(`/flowcut/scripts/${scriptId}/match`, {
      method: 'POST',
      body: JSON.stringify({ tenant_key: tenantKey, product }),
    }),

  export: (scriptId: number, materialIds: number[], tenantKey: string) =>
    jsonFetch<ExportResp>(`/flowcut/scripts/${scriptId}/export`, {
      method: 'POST',
      body: JSON.stringify({ material_ids: materialIds, tenant_key: tenantKey }),
    }),
}

export const taskApi = {
  get: (taskId: string) =>
    jsonFetch<{ ok: boolean } & TaskStatus>(`/flowcut/tasks/${taskId}`),
}
```

- [ ] **Step 2: Commit**

```bash
git add flowcut_frontend/src/api/script.ts
git commit -m "feat(fe): scriptApi/taskApi client"
```

---

## Phase 8: 前端 ScriptEditor 页

### Task 8.1: ScriptEditor 组件

**Files:**
- Create: `flowcut_frontend/src/components/generate/ScriptEditor.tsx`

- [ ] **Step 1: 写组件**

Create `flowcut_frontend/src/components/generate/ScriptEditor.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Button, Card, Input, Space, message, Modal, Tag } from 'antd'
import { PlusOutlined, DeleteOutlined } from '@ant-design/icons'
import { useScriptStore } from '../../stores/scriptStore'
import { scriptApi } from '../../api/script'
import type { ScriptSegment } from '../../types/script'

const TENANT_KEY = 'default'  // 临时硬编码，未来从登录态拿

export default function ScriptEditor() {
  const { scriptId } = useParams<{ scriptId: string }>()
  const navigate = useNavigate()
  const { currentScript, setScript, updateSegments } = useScriptStore()
  const [loading, setLoading] = useState(true)
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    if (!scriptId) return
    setLoading(true)
    scriptApi.get(Number(scriptId))
      .then((s) => {
        setScript(s as any)
        setLoading(false)
      })
      .catch((e) => {
        message.error(String(e))
        setLoading(false)
      })
  }, [scriptId, setScript])

  useEffect(() => {
    if (!dirty) return
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [dirty])

  if (loading || !currentScript) return <div>加载中...</div>

  const segments = currentScript.segments
  const isConfirmed = currentScript.status === 'CONFIRMED'

  const onChange = (idx: number, field: 'visual' | 'copy', value: string) => {
    const next = segments.map((s, i) => i === idx ? { ...s, [field]: value } : s)
    updateSegments(next)
    setDirty(true)
  }

  const onAdd = () => {
    const next: ScriptSegment[] = [
      ...segments,
      { idx: segments.length, start_time: 0, end_time: 0, visual: '', copy: '' },
    ]
    updateSegments(next)
    setDirty(true)
  }

  const onDelete = (idx: number) => {
    const next = segments
      .filter((_, i) => i !== idx)
      .map((s, i) => ({ ...s, idx: i }))
    updateSegments(next)
    setDirty(true)
  }

  const onSave = async () => {
    try {
      await scriptApi.update(currentScript.id, segments)
      setDirty(false)
      message.success('已保存草稿')
    } catch (e) {
      message.error(String(e))
    }
  }

  const onConfirmAndMatch = async () => {
    try {
      if (dirty) await scriptApi.update(currentScript.id, segments)
      await scriptApi.confirm(currentScript.id)
      navigate(`/scripts/${currentScript.id}/preview`)
    } catch (e) {
      message.error(String(e))
    }
  }

  const onReopen = async () => {
    Modal.confirm({
      title: '重新编辑会清空召回结果，确定吗？',
      onOk: async () => {
        await scriptApi.reopen(currentScript.id)
        const refreshed = await scriptApi.get(currentScript.id)
        setScript(refreshed as any)
      },
    })
  }

  return (
    <div style={{ padding: 24 }}>
      <Card title={`脚本编辑 #${currentScript.id}`} extra={
        <Tag color={isConfirmed ? 'green' : 'orange'}>{currentScript.status}</Tag>
      }>
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          {segments.map((seg, i) => (
            <Card key={i} type="inner" title={`段 ${seg.idx}`}
              extra={!isConfirmed && (
                <Button danger size="small" icon={<DeleteOutlined />} onClick={() => onDelete(i)} />
              )}
            >
              <Space direction="vertical" size="small" style={{ width: '100%' }}>
                <div>
                  <div style={{ fontSize: 12, color: '#888' }}>画面（visual）</div>
                  <Input.TextArea
                    value={seg.visual}
                    onChange={(e) => onChange(i, 'visual', e.target.value)}
                    disabled={isConfirmed}
                    autoSize={{ minRows: 2 }}
                  />
                </div>
                <div>
                  <div style={{ fontSize: 12, color: '#888' }}>文案（copy）</div>
                  <Input.TextArea
                    value={seg.copy}
                    onChange={(e) => onChange(i, 'copy', e.target.value)}
                    disabled={isConfirmed}
                    autoSize={{ minRows: 2 }}
                  />
                </div>
                <div style={{ fontSize: 12, color: '#888' }}>
                  时间：{seg.start_time.toFixed(2)}s - {seg.end_time.toFixed(2)}s
                </div>
              </Space>
            </Card>
          ))}
          {!isConfirmed && (
            <Button icon={<PlusOutlined />} onClick={onAdd} block>加一段</Button>
          )}
        </Space>
      </Card>

      <div style={{ position: 'sticky', bottom: 0, padding: 16, background: '#fff', borderTop: '1px solid #eee', marginTop: 16 }}>
        <Space>
          {isConfirmed
            ? <Button onClick={onReopen}>重新编辑</Button>
            : <>
                <Button onClick={onSave}>保存草稿</Button>
                <Button type="primary" onClick={onConfirmAndMatch}>确认脚本并匹配</Button>
              </>
          }
        </Space>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add flowcut_frontend/src/components/generate/ScriptEditor.tsx
git commit -m "feat(fe): ScriptEditor 组件"
```

---

## Phase 9: 前端 MaterialPreview 页

### Task 9.1: MaterialPreview 组件

**Files:**
- Create: `flowcut_frontend/src/components/generate/MaterialPreview.tsx`

- [ ] **Step 1: 写组件**

Create `flowcut_frontend/src/components/generate/MaterialPreview.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Card, Checkbox, Button, Space, message, Tag, Empty } from 'antd'
import { useScriptStore } from '../../stores/scriptStore'
import { scriptApi } from '../../api/script'
import type { MatchedMaterial } from '../../types/script'
import ExportButton from './ExportButton'

const TENANT_KEY = 'default'

function MaterialCard({ mat, checked, onToggle, dim }: {
  mat: MatchedMaterial; checked: boolean; onToggle: () => void; dim?: boolean
}) {
  return (
    <Card
      hoverable
      style={{ width: 200, opacity: dim ? 0.7 : 1 }}
      cover={mat.preview_url
        ? <video src={mat.preview_url} style={{ height: 120, objectFit: 'cover' }} controls={false} muted />
        : <div style={{ height: 120, background: '#f0f0f0' }} />
      }
      bodyStyle={{ padding: 8 }}
    >
      <Checkbox checked={checked} onChange={onToggle}>
        <span style={{ fontSize: 12 }}>{mat.name}</span>
      </Checkbox>
      <div style={{ fontSize: 11, color: '#888' }}>
        {mat.duration?.toFixed(1)}s · score {mat.score.toFixed(2)}
      </div>
      {mat.scene_role && <Tag style={{ marginTop: 4 }}>{mat.scene_role}</Tag>}
    </Card>
  )
}

export default function MaterialPreview() {
  const { scriptId } = useParams<{ scriptId: string }>()
  const navigate = useNavigate()
  const { currentScript, matchResults, selectedMaterials, setScript, setMatchResults, toggleMaterial } = useScriptStore()
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    if (!scriptId) return
    let alive = true
    setLoading(true)
    ;(async () => {
      try {
        const s = await scriptApi.get(Number(scriptId))
        if (!alive) return
        setScript(s as any)
        const m = await scriptApi.match(Number(scriptId), TENANT_KEY, '')
        if (!alive) return
        setMatchResults(m.results)
      } catch (e) {
        message.error(String(e))
      } finally {
        if (alive) setLoading(false)
      }
    })()
    return () => { alive = false }
  }, [scriptId, setScript, setMatchResults])

  if (loading) return <div>召回中...</div>
  if (!currentScript) return <Empty description="脚本不存在" />

  return (
    <div style={{ padding: 24 }}>
      <Space style={{ marginBottom: 16 }}>
        <Button onClick={() => navigate(`/scripts/${currentScript.id}`)}>重新编辑</Button>
        <span>脚本 #{currentScript.id} · {matchResults.length} 段</span>
      </Space>

      <Space direction="vertical" size="large" style={{ width: '100%' }}>
        {matchResults.map((r) => (
          <Card key={r.seg_idx} title={`段 ${r.seg_idx}`}>
            <div style={{ marginBottom: 12, fontSize: 13, color: '#555' }}>
              <div>画面：{r.visual}</div>
              <div>文案：{r.copy}</div>
            </div>
            {r.phase1.length === 0 && r.phase2.length === 0 && (
              <Empty description="召回为空" />
            )}
            {r.phase1.length > 0 && (
              <>
                <div style={{ fontSize: 12, color: '#888', marginBottom: 8 }}>
                  产品专属（默认勾选）
                </div>
                <Space wrap>
                  {r.phase1.map((m) => (
                    <MaterialCard
                      key={m.material_id}
                      mat={m}
                      checked={selectedMaterials.has(m.material_id)}
                      onToggle={() => toggleMaterial(m.material_id)}
                    />
                  ))}
                </Space>
              </>
            )}
            {r.phase2.length > 0 && (
              <>
                <div style={{ fontSize: 12, color: '#888', margin: '12px 0 8px' }}>
                  通用兜底
                </div>
                <Space wrap>
                  {r.phase2.map((m) => (
                    <MaterialCard
                      key={m.material_id}
                      mat={m}
                      checked={selectedMaterials.has(m.material_id)}
                      onToggle={() => toggleMaterial(m.material_id)}
                      dim
                    />
                  ))}
                </Space>
              </>
            )}
          </Card>
        ))}
      </Space>

      <div style={{ position: 'sticky', bottom: 0, padding: 16, background: '#fff', borderTop: '1px solid #eee', marginTop: 16 }}>
        <ExportButton scriptId={currentScript.id} tenantKey={TENANT_KEY} />
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add flowcut_frontend/src/components/generate/MaterialPreview.tsx
git commit -m "feat(fe): MaterialPreview 组件（双阶段召回 + 复选框）"
```

---

## Phase 10: 前端 ExportButton + 任务轮询

### Task 10.1: ExportButton 组件

**Files:**
- Create: `flowcut_frontend/src/components/generate/ExportButton.tsx`

- [ ] **Step 1: 写组件**

Create `flowcut_frontend/src/components/generate/ExportButton.tsx`:

```tsx
import { useState } from 'react'
import { Button, Modal, Progress, message } from 'antd'
import { useScriptStore } from '../../stores/scriptStore'
import { scriptApi, taskApi } from '../../api/script'

const POLL_INTERVAL = 2000
const MAX_POLL_DURATION = 5 * 60 * 1000

export default function ExportButton({ scriptId, tenantKey }: {
  scriptId: number
  tenantKey: string
}) {
  const { selectedMaterials } = useScriptStore()
  const [exporting, setExporting] = useState(false)
  const [downloadUrl, setDownloadUrl] = useState<string | null>(null)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  const onClick = async () => {
    if (selectedMaterials.size === 0) {
      message.warning('至少选一个素材')
      return
    }
    setExporting(true)
    setDownloadUrl(null)
    setErrorMsg(null)
    try {
      const resp = await scriptApi.export(scriptId, [...selectedMaterials], tenantKey)
      const taskId = resp.task_id
      const url = await pollTask(taskId)
      setDownloadUrl(url)
    } catch (e) {
      setErrorMsg(String(e))
    } finally {
      setExporting(false)
    }
  }

  async function pollTask(taskId: string): Promise<string> {
    const startedAt = Date.now()
    while (Date.now() - startedAt < MAX_POLL_DURATION) {
      const t = await taskApi.get(taskId)
      if (t.status === 'succeeded' && t.result_url) return t.result_url
      if (t.status === 'failed') throw new Error(t.last_error || '任务失败')
      await new Promise((r) => setTimeout(r, POLL_INTERVAL))
    }
    throw new Error('导出耗时较久，请稍后再来')
  }

  return (
    <>
      <Button type="primary" onClick={onClick} disabled={exporting}>
        {exporting ? '导出中...' : `导出素材包（已选 ${selectedMaterials.size}）`}
      </Button>

      <Modal
        open={exporting}
        closable={false}
        footer={null}
        title="导出中"
      >
        <p>请勿关闭页面...</p>
        <Progress percent={undefined} status="active" />
      </Modal>

      <Modal
        open={!!downloadUrl}
        title="导出成功"
        onCancel={() => setDownloadUrl(null)}
        footer={[
          <Button key="ok" type="primary" onClick={() => window.open(downloadUrl!)}>下载 ZIP</Button>,
        ]}
      >
        <p>素材包已生成。链接 24 小时有效。</p>
      </Modal>

      <Modal
        open={!!errorMsg}
        title="导出失败"
        onCancel={() => setErrorMsg(null)}
        footer={[
          <Button key="retry" onClick={() => { setErrorMsg(null); onClick() }}>重试</Button>,
          <Button key="close" onClick={() => setErrorMsg(null)}>关闭</Button>,
        ]}
      >
        <p>{errorMsg}</p>
      </Modal>
    </>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add flowcut_frontend/src/components/generate/ExportButton.tsx
git commit -m "feat(fe): ExportButton 异步导出 + 任务轮询"
```

---

## Phase 11: 前端入口改造 + 路由

### Task 11.1: 路由表更新

**Files:**
- Modify: `flowcut_frontend/src/router.tsx`

- [ ] **Step 1: 加新路由**

```tsx
import { Routes, Route } from 'react-router-dom'
import GenerateTab from './components/generate/GenerateTab'
import MaterialTab from './components/material/MaterialTab'
import CreativeTab from './components/creative/CreativeTab'
import DashboardTab from './components/dashboard/DashboardTab'
import ScriptEditor from './components/generate/ScriptEditor'
import MaterialPreview from './components/generate/MaterialPreview'

export default function AppRouter() {
  return (
    <Routes>
      <Route path="/"                          element={<GenerateTab />} />
      <Route path="/scripts/:scriptId"         element={<ScriptEditor />} />
      <Route path="/scripts/:scriptId/preview" element={<MaterialPreview />} />
      <Route path="/material"  element={<MaterialTab />} />
      <Route path="/creative"  element={<CreativeTab />} />
      <Route path="/dashboard" element={<DashboardTab />} />
    </Routes>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add flowcut_frontend/src/router.tsx
git commit -m "feat(fe): 新增 /scripts/:id 和 /scripts/:id/preview 路由"
```

### Task 11.2: GenerateTab 加"直接编写脚本"入口

**Files:**
- Modify: `flowcut_frontend/src/components/generate/GenerateTab.tsx`

- [ ] **Step 1: 加入口卡片**

在 GenerateTab 现有入口（"上传爆款视频"）旁边新增一个并列入口卡片：

```tsx
import { Card, Space, Button } from 'antd'
import { useNavigate } from 'react-router-dom'
import { scriptApi } from '../../api/script'

// ... 在现有 GenerateTab 组件 return 内加：

<Card title="直接编写脚本" style={{ width: 320 }}>
  <p style={{ minHeight: 60, color: '#666' }}>
    跳过拆镜，手动填写画面与文案，直接进入素材匹配
  </p>
  <Button block onClick={async () => {
    const resp = await scriptApi.upload('default', [{ visual: '', copy: '' }])
    navigate(`/scripts/${resp.script_id}`)
  }}>
    新建空脚本
  </Button>
</Card>
```

注意：`navigate` 从 `useNavigate()` 拿，`TENANT_KEY` 与其他组件保持一致。

- [ ] **Step 2: 分类确认页加"查看脚本"按钮**

找到分类确认页面（CategoryConfirm 或类似名）的代码，在底部"提交分类"按钮旁加：

```tsx
{refVideo.script_id && (
  <Button onClick={() => navigate(`/scripts/${refVideo.script_id}`)}>
    查看脚本
  </Button>
)}
```

`refVideo.script_id` 来自 `/flowcut/reference-videos/{id}` 返回（需要确认 reference_videos 路由 GET 返回是否包含 script_id 字段，如缺少需补）。

- [ ] **Step 3: 启动前端**

```bash
cd flowcut_frontend && npm run dev
```
浏览器打开，确认：
- 生成 Tab 有两个入口卡
- 点"新建空脚本" → 跳到 /scripts/:id，能看到一段空表单
- 跑一次拆镜 → 分类确认页底部出现"查看脚本"按钮

- [ ] **Step 4: Commit**

```bash
git add flowcut_frontend/src/components/generate/GenerateTab.tsx
git commit -m "feat(fe): GenerateTab 加入'直接编写脚本'入口 + 分类页跳转"
```

---

## Phase 12: 端到端联调

### Task 12.1: 清库重跑 + 完整流程跑一遍

- [ ] **Step 1: 清库**

```bash
cd SimpleClaw && FLOWCUT_ENV=dev uv run python -m Flowcut.scripts.reset_db
```

- [ ] **Step 2: 启后端 + 前端**

```bash
# 终端 A
cd SimpleClaw && ./uvdev.sh

# 终端 B
cd flowcut_frontend && npm run dev
```

- [ ] **Step 3: 验证场景 A — 拆镜出脚本流**

1. 浏览器打开生成 Tab，上传一个短视频
2. 等拆镜完成（看后端日志或刷新页面）
3. 分类确认 → 提交（这一步触发 CLIP_CREATE 切片入素材库）
4. 点"查看脚本"按钮 → 跳到脚本编辑页
5. 验证：脚本有多段，每段含 visual（画面）和 copy（口播文字）
6. 编辑某段的 visual / copy，点保存 → 提示"已保存"
7. 点"确认脚本并匹配"→ 跳到素材预览页
8. 验证：每段有 phase1 / phase2 素材，默认全部勾选
9. 勾去一些不要的，点"导出素材包"
10. 等任务完成 → 弹出下载链接，点击下载
11. 解压 zip，验证：含 `script.json`、`script.md`、`clips/seg*.mp4`、`audio.mp3`、`reference.mp4`

- [ ] **Step 4: 验证场景 B — 用户上传脚本流**

1. 生成 Tab 点"新建空脚本"
2. 跳到编辑页，加几段（visual + copy 都填）
3. 保存草稿 → 确认匹配
4. 验证：召回正常出结果
5. 导出 → 验证 zip 不含 audio.mp3 和 reference.mp4（uploaded 来源没有）

- [ ] **Step 5: 验证负向场景**

- 上传空 segments → 422
- CONFIRMED 状态再 PATCH → 409
- 不存在的 script_id → 404
- 选 0 个素材点导出 → 前端阻止 + 后端 422

- [ ] **Step 6: 记录联调结果到 spec § 9（已知风险章节追加"验证记录"）**

Edit `docs/superpowers/specs/2026-05-22-flowcut-v2-pipeline-design.md`，在 § 9 末尾追加：

```
### 9.4 端到端联调记录（YYYY-MM-DD）

- 场景 A（拆镜→脚本→导出）：✅ 通过
- 场景 B（上传→脚本→导出）：✅ 通过
- 负向：✅ 通过
- 已发现需后续跟踪的问题：[列举或写"无"]
```

- [ ] **Step 7: Final commit**

```bash
git add docs/superpowers/specs/2026-05-22-flowcut-v2-pipeline-design.md
git commit -m "docs: flowcut v2 pipeline 端到端联调记录"
```

---

## 完成标准

本 plan 全部完成时，下述清单全部勾选：

- [ ] 清库脚本可用，DB 含 fc_script 新表 + fc_reference_video.audio_oss_key/script_id
- [ ] 4 个新 Tool 已实现并有单元测试（全 PASS）
- [ ] material_matcher 双 query 改造完毕，旧测试无 regression
- [ ] SCENE_DECOMPOSE 拆镜后写入 fc_script + 抽音轨
- [ ] EXPORT_PACKAGE stream 跑通，集成测试 PASS
- [ ] 8 个新 REST 路由全部可用（含 confirm/reopen/match/export）
- [ ] 前端 ScriptEditor、MaterialPreview、ExportButton 可用
- [ ] 端到端两种场景（拆镜来源、上传来源）联调通过
- [ ] 端到端负向场景（422/409/404）符合预期

## 不在本 plan 范围

- 成片预览（时间轴拼接 UI）— 下轮专项
- 千川投放 — 长期不在 Flowcut v2 范围
- 任务列表 UI — 本轮简化为 alert
- 老 6 个 Tool 与 REST 路由的等价性校对 — 等编排层评估时再做
- 旧拆镜数据迁移 — 走清库重跑
