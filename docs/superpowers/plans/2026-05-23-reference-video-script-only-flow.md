# 爆款视频"只产脚本不切片"流程改造实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把爆款视频处理链路从"拆镜→分类→切片落库"简化为"拆镜→脚本"，修复 ASR copy 字段为空的 bug，放宽产品选择时机。

**Architecture:** 仅改 Flowcut 子项目。`scene_decompose_executor` 终态从 `AWAITING_CLASSIFICATION` 改为 `READY`，直接写入 `fc_script`；删除 `clip_create` 整条流；新增 `fc_script.product` 列；新增 `POST /scripts/{id}/update-product`；ASR 请求加 `show_utterances: true`。

**Tech Stack:** Python 3 + FastAPI + aiomysql + aiohttp（ByteDance ASR WebSocket）+ pytest。

**Spec:** `docs/superpowers/specs/2026-05-23-reference-video-script-only-flow-design.md`

**实现顺序原则：** 从最独立、最低风险的改动开始（ASR fix），再做数据层（schema、repo），再改业务流（executor、route），最后清理废弃代码 + 前端。每个 Task 一次提交。

---

## File Structure

**修改：**
- `Flowcut/runtime/executors.py` — ASR payload 加 `show_utterances`；scene_decompose 终态改 `READY`、写 product、不写 scene_data；删除 `make_clip_create_executor`
- `Flowcut/storage/database.py` — `ensure_schema()` 加 `fc_script.product` 迁移 + 孤儿状态修复
- `Flowcut/storage/script_repo.py` — 加 `product` 字段读写
- `Flowcut/storage/reference_video_repo.py` — 删 `update_scene_data_and_product`
- `Flowcut/runtime/streams.py` — 删 `CLIP_CREATE`
- `Flowcut/runtime/worker.py` — 删 clip_create worker
- `Flowcut/api/routes/reference_videos.py` — OSS key 前缀 `uploads/...`，删 classify 路由
- `Flowcut/api/routes/scripts.py` — 加 `POST /{script_id}/update-product`
- `Flowcut/tools/search_materials.py` — `product` 三段回退

**新建：**
- `SimpleClaw/tests/Flowcut/test_asr_payload.py`
- `SimpleClaw/tests/Flowcut/test_script_repo_product.py`
- `SimpleClaw/tests/Flowcut/test_scripts_route_update_product.py`
- `SimpleClaw/tests/Flowcut/test_search_materials_product_fallback.py`

**前端（最后一个任务统一处理）：**
- `flowcut_frontend/src/components/generate/steps/ClassifyModal.tsx` — 删除
- `flowcut_frontend/src/components/generate/steps/MatchingStep.tsx` — 不再调用 classify
- `flowcut_frontend/src/api/referenceVideos.ts` — 移除 classify API
- `flowcut_frontend/src/api/scripts.ts`（新建或扩展）— 新增 `updateProduct(scriptId, product)`

---

## Task 1: ASR `show_utterances` 修复

**Files:**
- Modify: `SimpleClaw/Flowcut/runtime/executors.py:117-140`（`_call_asr_websocket_with_words` 内的 `config_payload` 构造）
- Test: `SimpleClaw/tests/Flowcut/test_asr_payload.py`

### Step 1.1: 重构 — 把 ASR config payload 抽成独立函数（便于单测）

- [ ] Modify `Flowcut/runtime/executors.py`：在 `_call_asr_websocket_with_words` 之上加纯函数：

```python
def _build_asr_request_payload() -> bytes:
    """构造 ByteDance bigmodel ASR 请求 JSON payload。

    必须开启 show_utterances=True，否则 response 不会返回 utterances[].words[]，
    导致拆镜段无法切出 copy 字段。
    """
    return json.dumps({
        "user": {"uid": "flowcut"},
        "audio": {
            "format": "pcm", "rate": 16000, "bits": 16,
            "channel": 1, "codec": "raw",
        },
        "request": {
            "model_name": "bigmodel",
            "enable_punc": True,
            "enable_itn": True,
            "show_utterances": True,
        },
    }).encode()
```

并把 `_call_asr_websocket_with_words` 中原来的 `config_payload = json.dumps(...).encode()` 替换为 `config_payload = _build_asr_request_payload()`。

- [ ] **Step 1.2: 写失败测试**

Create `SimpleClaw/tests/Flowcut/__init__.py`（若不存在，空文件即可）

Create `SimpleClaw/tests/Flowcut/test_asr_payload.py`:

```python
"""ASR config payload 单测 —— 防止 show_utterances 开关再次回归。"""
import json
import pytest

from Flowcut.runtime.executors import _build_asr_request_payload


@pytest.mark.unit
def test_asr_payload_enables_show_utterances():
    raw = _build_asr_request_payload()
    obj = json.loads(raw)
    assert obj["request"]["show_utterances"] is True, (
        "show_utterances 必须开启，否则 ASR 不返回词级时间戳，拆镜段 copy 字段会为空"
    )


@pytest.mark.unit
def test_asr_payload_keeps_punc_and_itn():
    obj = json.loads(_build_asr_request_payload())
    assert obj["request"]["enable_punc"] is True
    assert obj["request"]["enable_itn"] is True


@pytest.mark.unit
def test_asr_payload_audio_format_unchanged():
    obj = json.loads(_build_asr_request_payload())
    audio = obj["audio"]
    assert audio["format"] == "pcm"
    assert audio["rate"] == 16000
    assert audio["channel"] == 1
```

- [ ] **Step 1.3: 运行测试，确认通过**

Run:
```bash
cd SimpleClaw && uv run pytest tests/Flowcut/test_asr_payload.py -v
```
Expected: 3 passed

- [ ] **Step 1.4: 提交**

```bash
cd /Users/shengxingou-1/电商自动化运营/E-commerceAutomation
git add SimpleClaw/Flowcut/runtime/executors.py SimpleClaw/tests/Flowcut/
git commit -m "fix(flowcut): ASR 启用 show_utterances，修复拆镜段 copy 为空"
```

---

## Task 2: Schema 迁移（fc_script.product + 孤儿状态修复）

**Files:**
- Modify: `SimpleClaw/Flowcut/storage/database.py`（`ensure_schema()` 末尾追加 2 段迁移）

### Step 2.1: 在 fc_script CREATE TABLE 中加 product 列

- [ ] Modify `Flowcut/storage/database.py:541-553` —— 在 `fc_script` 的 CREATE TABLE 里 `reference_video_id BIGINT NULL,` 这一行后新增一行：

```sql
            product             VARCHAR(128) NULL,
```

确保新部署能直接建出带 product 的表，老部署靠下一步迁移。

### Step 2.2: 追加 ALTER TABLE 迁移（探测 + ADD COLUMN）

- [ ] 在 `Flowcut/storage/database.py` 的 `ensure_schema()` 中（紧接现有 `_material_columns` 迁移块之后）追加：

```python
            # 迁移（2026-05-23）：fc_script 新增 product 列。
            await cur.execute(
                """
                SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME   = 'fc_script'
                  AND COLUMN_NAME  = 'product'
                """
            )
            row = await cur.fetchone()
            if row and row[0] == 0:
                await cur.execute(
                    "ALTER TABLE fc_script "
                    "ADD COLUMN product VARCHAR(128) NULL AFTER reference_video_id"
                )

            # 迁移（2026-05-23）：把旧流程的孤儿 ref_video 状态置为 FAILED。
            # 旧 AWAITING_CLASSIFICATION / DECOMPOSED 记录的 script_id 必然为 NULL
            # （旧流程只在 clip_create 后才生成 fc_script），新流程不再支持这两个状态。
            await cur.execute(
                """
                UPDATE fc_reference_video
                   SET status='FAILED'
                 WHERE status IN ('AWAITING_CLASSIFICATION','DECOMPOSED')
                   AND script_id IS NULL
                """
            )
```

### Step 2.3: 验证迁移幂等

- [ ] 启动一次 Flowcut server 触发 `ensure_schema()`：

```bash
cd SimpleClaw
uv run python -c "
import asyncio
from Flowcut.storage.database import Database, ensure_schema
import os
async def main():
    db = Database(
        host=os.environ['MYSQL_HOST'], port=int(os.getenv('MYSQL_PORT', 3306)),
        user=os.environ['MYSQL_USER'], password=os.environ['MYSQL_PASSWORD'],
        db=os.environ['MYSQL_DB'],
    )
    await db.connect()
    await ensure_schema(db)
    await ensure_schema(db)  # 第二次跑应当无异常
    print('ensure_schema OK (twice)')
    await db.close()
asyncio.run(main())
"
```
Expected: 输出 `ensure_schema OK (twice)`，无 SQL 异常。

- [ ] 验证列已存在：

```bash
mysql -h"$MYSQL_HOST" -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" "$MYSQL_DB" \
  -e "SHOW COLUMNS FROM fc_script LIKE 'product'"
```
Expected: 一行 `product | varchar(128) | YES | | NULL |`

### Step 2.4: 提交

- [ ] Commit:

```bash
git add SimpleClaw/Flowcut/storage/database.py
git commit -m "feat(flowcut): schema 增加 fc_script.product；旧分类态 ref_video 迁移为 FAILED"
```

---

## Task 3: ScriptRepository 支持 product 字段

**Files:**
- Modify: `SimpleClaw/Flowcut/storage/script_repo.py`
- Test: `SimpleClaw/tests/Flowcut/test_script_repo_product.py`

### Step 3.1: 写失败测试

- [ ] Create `SimpleClaw/tests/Flowcut/test_script_repo_product.py`:

```python
"""ScriptRepository.product 字段读写测试。"""
import os
import pytest

from Flowcut.storage.database import Database, ensure_schema
from Flowcut.storage.script_repo import ScriptRepository


@pytest.fixture
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
async def test_create_without_product_returns_none(repo: ScriptRepository):
    rec = await repo.create(
        tenant_key="t_test_no_product",
        source="decomposed",
        segments=[{"idx": 0, "visual": "v", "copy": "c"}],
    )
    assert rec["product"] is None


@pytest.mark.integration
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
async def test_update_product_missing_id_raises(repo: ScriptRepository):
    with pytest.raises(ValueError, match="not found"):
        await repo.update_product(999_999_999, "X")
```

### Step 3.2: 运行测试，确认失败

- [ ] Run:
```bash
cd SimpleClaw && uv run pytest tests/Flowcut/test_script_repo_product.py -v
```
Expected: 4 个用例失败（`create()` 不接受 `product` 参数；`product` 字段不存在；`update_product` 方法不存在）。

### Step 3.3: 改 ScriptRepository 加上 product 支持

- [ ] Modify `Flowcut/storage/script_repo.py`:

把 `_COLS` 改为：

```python
_COLS = [
    "id", "tenant_key", "source", "reference_video_id", "product",
    "segments_json", "status", "created_at", "updated_at",
]
```

把 `create()` 方法改为：

```python
    async def create(
        self,
        *,
        tenant_key: str,
        source: str,
        segments: list[dict],
        reference_video_id: int | None = None,
        product: str | None = None,
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
                        (tenant_key, source, reference_video_id, product,
                         segments_json, status, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, 'DRAFT', %s, %s)
                    """,
                    (tenant_key, source, reference_video_id, product,
                     segments_json, now, now),
                )
                script_id = cur.lastrowid
                await conn.commit()
        result = await self.get(script_id)
        assert result is not None
        return result
```

在类末尾追加：

```python
    async def update_product(self, script_id: int, product: str | None) -> None:
        """更新脚本绑定的产品；product=None 表示清空。"""
        record = await self.get(script_id)
        if record is None:
            raise ValueError(f"script {script_id} not found")
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE fc_script SET product=%s, updated_at=%s WHERE id=%s",
                    (product, _now(), script_id),
                )
                await conn.commit()
```

### Step 3.4: 运行测试，确认通过

- [ ] Run:
```bash
cd SimpleClaw && uv run pytest tests/Flowcut/test_script_repo_product.py -v
```
Expected: 4 passed

### Step 3.5: 提交

- [ ] Commit:

```bash
git add SimpleClaw/Flowcut/storage/script_repo.py SimpleClaw/tests/Flowcut/test_script_repo_product.py
git commit -m "feat(flowcut): ScriptRepository 支持 product 字段读写 + update_product"
```

---

## Task 4: scene_decompose executor 改为终态 READY、写 product、不写 scene_data_json

**Files:**
- Modify: `SimpleClaw/Flowcut/runtime/executors.py:563-720` （`make_scene_decompose_executor`）
- Modify: `SimpleClaw/Flowcut/storage/reference_video_repo.py` （`update_status` 调用方式不变；删除 `update_scene_data_and_product` 方法）

### Step 4.1: 改 scene_decompose 主体逻辑

- [ ] Modify `Flowcut/runtime/executors.py`，定位 `make_scene_decompose_executor` 中段（"# Stop here — wait for user to classify" 块）替换为：

把原来的：

```python
            # Stop here — wait for user to classify segments before creating clips
            await ref_video_repo.update_status(
                ref_video_id, "AWAITING_CLASSIFICATION", scene_data=aligned,
            )

            # === 产出 fc_script + 回填 script_id ===
            script_segments = [
                {
                    "idx": i,
                    "start_time": float(seg.get("start_time", 0)),
                    "end_time": float(seg.get("end_time", 0)),
                    "visual": seg.get("content", ""),
                    "copy": seg.get("copy", ""),
                }
                for i, seg in enumerate(aligned)
            ]
            script_record = await script_repo.create(
                tenant_key=tenant_key,
                source="decomposed",
                reference_video_id=ref_video_id,
                segments=script_segments,
            )
            await ref_video_repo.set_script_id(ref_video_id, script_record["id"])

            logger.info("scene_decompose done: ref_video=%d segments=%d script=%d awaiting classification",
                        ref_video_id, len(aligned), script_record["id"])

            return TaskExecutionResult.succeeded(
                summary=f"ref_video_id={ref_video_id} segments={len(aligned)} awaiting_classification",
                details={"ref_video_id": ref_video_id, "segment_count": len(aligned)},
            )
```

为：

```python
            # === 产出 fc_script（新流程：无中间分类态，直接终态 READY）===
            script_segments = [
                {
                    "idx": i,
                    "start_time": float(seg.get("start_time", 0)),
                    "end_time": float(seg.get("end_time", 0)),
                    "visual": seg.get("content", ""),
                    "copy": seg.get("copy", ""),
                    "category": seg.get("category", "产品展示"),
                }
                for i, seg in enumerate(aligned)
            ]
            script_record = await script_repo.create(
                tenant_key=tenant_key,
                source="decomposed",
                reference_video_id=ref_video_id,
                product=ref_video.get("product"),
                segments=script_segments,
            )
            await ref_video_repo.set_script_id(ref_video_id, script_record["id"])
            # 不再写 scene_data_json；段落数据以 fc_script.segments_json 为准
            await ref_video_repo.update_status(ref_video_id, "READY")

            logger.info(
                "scene_decompose done: ref_video=%d segments=%d script=%d READY",
                ref_video_id, len(aligned), script_record["id"],
            )

            return TaskExecutionResult.succeeded(
                summary=f"ref_video_id={ref_video_id} segments={len(aligned)} script={script_record['id']}",
                details={
                    "ref_video_id": ref_video_id,
                    "segment_count": len(aligned),
                    "script_id": script_record["id"],
                },
            )
```

### Step 4.2: 删除 ReferenceVideoRepository.update_scene_data_and_product

- [ ] Modify `Flowcut/storage/reference_video_repo.py`：定位 `update_scene_data_and_product` 方法（约第 87-105 行）整段删除。同时 `update_status` 的 `scene_data` 参数 + `scene_data_json` 写入分支保留不动（兼容老代码 / 单测）。

- [ ] 确认 grep 不再有调用：

```bash
cd /Users/shengxingou-1/电商自动化运营/E-commerceAutomation
grep -rn "update_scene_data_and_product" SimpleClaw/Flowcut SimpleClaw/tests || echo "no usage"
```
Expected: `no usage`（如有遗留引用，在下一个 task 6 删 classify 路由时一起清理；如这一步还有引用，先 grep 出来一并删）。

### Step 4.3: 提交

- [ ] Commit:

```bash
git add SimpleClaw/Flowcut/runtime/executors.py SimpleClaw/Flowcut/storage/reference_video_repo.py
git commit -m "refactor(flowcut): scene_decompose 直接产 fc_script(READY)，不再走分类中间态"
```

---

## Task 5: 删除 clip_create 整条流

**Files:**
- Modify: `SimpleClaw/Flowcut/runtime/executors.py` —— 删除 `make_clip_create_executor` 整个函数
- Modify: `SimpleClaw/Flowcut/runtime/streams.py` —— 删除 `CLIP_CREATE`
- Modify: `SimpleClaw/Flowcut/runtime/worker.py` —— 删除对应 import 与 worker 注册

### Step 5.1: 删 executor

- [ ] Modify `Flowcut/runtime/executors.py`：定位 `def make_clip_create_executor(...)`（约第 723 行起），删除整个函数体（直到下一个顶级 `def make_*_executor` 之前的最后一行 `return execute`）。

### Step 5.2: 删 stream 常量

- [ ] Modify `Flowcut/runtime/streams.py`：删除这一行：

```python
    CLIP_CREATE        = "flowcut:clip_create"         # 用户分类确认后批量创建子片段
```

### Step 5.3: 删 worker 注册

- [ ] Modify `Flowcut/runtime/worker.py`：

把 import 行：
```python
from Flowcut.runtime.executors import (
    make_clip_create_executor,
    make_export_package_executor,
    make_material_process_executor,
    make_scene_decompose_executor,
    make_vector_repair_executor,
)
```
中的 `make_clip_create_executor,` 一行删除。

定位 `make_workers()` 返回的列表中 `_make_worker(FlowcutTaskStream.CLIP_CREATE, {...})` 整个条目（包括其字典体内的 `"clip_create": make_clip_create_executor(...)` 段落），整段删除。docstring 里 "8 workers" 改为 "7 workers"，列表中去掉 `clip_create`。

### Step 5.4: 验证全仓无残留引用

- [ ] Run:
```bash
grep -rn "clip_create\|CLIP_CREATE\|make_clip_create_executor" SimpleClaw/Flowcut SimpleClaw/tests
```
Expected: 无输出（如有遗留，全部清理）。

### Step 5.5: 启动 server，确认 worker 数量正确

- [ ] Run:
```bash
cd SimpleClaw && timeout 8 uv run python -m uvicorn Flowcut.api.server:app --port 8001 2>&1 | grep -i worker | head -20
```
Expected: 启动日志显示 7 个 worker（不再有 `flowcut:clip_create`）。

### Step 5.6: 提交

- [ ] Commit:

```bash
git add SimpleClaw/Flowcut/runtime/
git commit -m "refactor(flowcut): 删除 clip_create executor/stream/worker（拆镜不再切片）"
```

---

## Task 6: 上传路由 OSS key 改 `uploads/...`、删 classify 路由

**Files:**
- Modify: `SimpleClaw/Flowcut/api/routes/reference_videos.py`

### Step 6.1: 删 classify 路由

- [ ] Modify `Flowcut/api/routes/reference_videos.py`：

整段删除 `ClassifySegment` 模型、`ClassifyRequest` 模型、以及 `@router.post("/{ref_video_id}/classify")` 装饰的 `classify_reference_video` 函数。

同时删除该文件开头不再使用的 `import json`、`import re`、`from Flowcut.runtime.streams import FlowcutTaskStream` 的 `CLIP_CREATE` 相关代码（保留 `SCENE_DECOMPOSE`），以及 `_sanitize_path_component` 若仅供 classify 使用则一并删除（grep 确认）：

```bash
grep -n "_sanitize_path_component\|^import json\|^import re" SimpleClaw/Flowcut/api/routes/reference_videos.py
```

若 `_sanitize_path_component` 已无其他用途则删函数；`json` / `re` 若不再使用则删 import。

### Step 6.2: 改 OSS key 前缀为 `uploads/...`

- [ ] Modify `Flowcut/api/routes/reference_videos.py` 中两处：

`upload_reference_video` 内：

```python
    filename = file.filename or "upload"
    oss_key = f"uploads/{tenant_key}/{int(time.time())}_{filename}"
```

`create_upload_token` 内：

```python
    oss_key = f"uploads/{body.tenant_key}/{int(time.time())}_{body.filename}"
```

注：移除 `product_dir` 局部变量，因为爆款视频不再按产品分目录。`product` 字段仍按原有方式传给 `ref_video_repo.create(...)`。

### Step 6.3: 手工冒烟 —— 起 server，传一个小视频

- [ ] Run（终端 1）：
```bash
cd SimpleClaw && uv run python -m uvicorn Flowcut.api.server:app --port 8001
```

- [ ] Run（终端 2）：
```bash
cd /tmp && ffmpeg -y -f lavfi -i testsrc=duration=3:size=320x240:rate=10 \
  -f lavfi -i sine=frequency=440:duration=3 -c:v libx264 -t 3 mini.mp4 \
  -loglevel error
curl -s -X POST http://localhost:8001/reference-videos/upload \
  -F "tenant_key=t_smoke" \
  -F "file=@mini.mp4"
```
Expected: 返回 JSON 包含 `"oss_key": "uploads/t_smoke/..."`、`"status": "queued"`。

- [ ] 停 server（Ctrl+C 终端 1）。

### Step 6.4: 提交

- [ ] Commit:

```bash
git add SimpleClaw/Flowcut/api/routes/reference_videos.py
git commit -m "feat(flowcut): 爆款视频 OSS 路径迁到 uploads/...; 删除 classify 路由"
```

---

## Task 7: 新增 `POST /flowcut/scripts/{id}/update-product` 路由

**Files:**
- Modify: `SimpleClaw/Flowcut/api/routes/scripts.py`
- Test: `SimpleClaw/tests/Flowcut/test_scripts_route_update_product.py`

### Step 7.1: 写失败测试

- [ ] Create `SimpleClaw/tests/Flowcut/test_scripts_route_update_product.py`:

```python
"""POST /flowcut/scripts/{id}/update-product 路由测试。"""
import os
import pytest
from fastapi.testclient import TestClient

from Flowcut.api.server import app
from Flowcut.storage.database import Database, ensure_schema
from Flowcut.storage.script_repo import ScriptRepository


@pytest.fixture
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
def test_update_product_clear_with_null():
    with TestClient(app) as client:
        # 路由应允许 product=null 清空
        # 用一个不存在的 ID 验证 404 行为
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
```

### Step 7.2: 运行测试，确认失败

- [ ] Run:
```bash
cd SimpleClaw && uv run pytest tests/Flowcut/test_scripts_route_update_product.py -v
```
Expected: 全部失败（路由不存在 → 404 / 405）。

### Step 7.3: 实现路由

- [ ] Modify `Flowcut/api/routes/scripts.py`：在文件末尾追加：

```python
from pydantic import BaseModel


class UpdateProductBody(BaseModel):
    product: str | None = None


@router.post("/{script_id}/update-product")
async def update_script_product(
    script_id: int, body: UpdateProductBody, request: Request,
) -> dict[str, Any]:
    """修改脚本绑定的产品。空字符串等同 null（清空）。"""
    c = _c(request)
    normalized = body.product.strip() if body.product else None
    if normalized == "":
        normalized = None
    try:
        await c.script_repo.update_product(script_id, normalized)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return {"ok": True, "script_id": script_id, "product": normalized}
```

### Step 7.4: 运行测试，确认通过

- [ ] Run:
```bash
cd SimpleClaw && uv run pytest tests/Flowcut/test_scripts_route_update_product.py -v
```
Expected: 3 passed

### Step 7.5: 提交

- [ ] Commit:

```bash
git add SimpleClaw/Flowcut/api/routes/scripts.py SimpleClaw/tests/Flowcut/test_scripts_route_update_product.py
git commit -m "feat(flowcut): POST /scripts/{id}/update-product 接口"
```

---

## Task 8: search_materials 工具 product 三段回退

**Files:**
- Modify: `SimpleClaw/Flowcut/tools/search_materials.py`
- Test: `SimpleClaw/tests/Flowcut/test_search_materials_product_fallback.py`

### Step 8.1: 写失败测试

- [ ] Create `SimpleClaw/tests/Flowcut/test_search_materials_product_fallback.py`:

```python
"""search_materials product 三段回退逻辑测试。

回退顺序：
  1. caller 显式传 product → 用 caller 的值
  2. 否则取 fc_script.product
  3. 都为空 → 直接报错（ToolResult.ok=False）
"""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from Flowcut.tools.search_materials import SearchMaterialsTool


def _make_tool(script_record: dict, matcher_should_be_called: bool = True):
    script_repo = MagicMock()
    script_repo.get = AsyncMock(return_value=script_record)
    material_repo = MagicMock()
    vector_store = MagicMock()
    embedding_service = MagicMock()
    return SearchMaterialsTool(
        material_repo=material_repo,
        script_repo=script_repo,
        vector_store=vector_store,
        embedding_service=embedding_service,
    )


@pytest.mark.unit
async def test_explicit_product_wins_over_script_product(monkeypatch):
    captured = {}

    async def fake_match(segments, *, tenant_key, product, **kw):
        captured["product"] = product
        return []

    monkeypatch.setattr(
        "Flowcut.tools.search_materials.match_segments_parallel", fake_match,
    )
    tool = _make_tool({
        "tenant_key": "t",
        "segments_json": json.dumps([{"idx": 0, "visual": "v", "copy": "c"}]),
        "product": "scriptproduct",
    })
    result = await tool.execute(script_id=1, product="explicit")
    assert result.ok
    assert captured["product"] == "explicit"


@pytest.mark.unit
async def test_falls_back_to_script_product_when_caller_omits(monkeypatch):
    captured = {}

    async def fake_match(segments, *, tenant_key, product, **kw):
        captured["product"] = product
        return []

    monkeypatch.setattr(
        "Flowcut.tools.search_materials.match_segments_parallel", fake_match,
    )
    tool = _make_tool({
        "tenant_key": "t",
        "segments_json": json.dumps([{"idx": 0, "visual": "v", "copy": "c"}]),
        "product": "scriptproduct",
    })
    result = await tool.execute(script_id=1, product="")
    assert result.ok
    assert captured["product"] == "scriptproduct"


@pytest.mark.unit
async def test_error_when_no_product_anywhere():
    tool = _make_tool({
        "tenant_key": "t",
        "segments_json": json.dumps([{"idx": 0, "visual": "v", "copy": "c"}]),
        "product": None,
    })
    result = await tool.execute(script_id=1, product="")
    assert not result.ok
    assert "请先" in result.content and "产品" in result.content
```

### Step 8.2: 运行测试，确认失败

- [ ] Run:
```bash
cd SimpleClaw && uv run pytest tests/Flowcut/test_search_materials_product_fallback.py -v
```
Expected: 全部失败（当前实现 `product` 是 `required`，且不会回退）。

### Step 8.3: 改 search_materials 实现回退

- [ ] Modify `Flowcut/tools/search_materials.py`：

把 `parameters` 字段中的 `"required": ["script_id", "product"]` 改成 `"required": ["script_id"]`，并把 `product` 描述改为：

```python
            "product": {
                "type": "string",
                "description": "当前产品名；省略或为空字符串时使用脚本绑定的 product，都为空则报错。",
            },
```

把 `execute` 签名和前置校验改为：

```python
    async def execute(
        self, script_id: int, product: str = "", **kwargs,
    ) -> ToolResult:
        script = await self._script_repo.get(script_id)
        if script is None:
            return ToolResult(content=f"脚本 {script_id} 不存在", ok=False)

        # product 三段回退：caller 显式值 > 脚本绑定 > 报错
        effective_product = (product or "").strip()
        if not effective_product:
            effective_product = (script.get("product") or "").strip()
        if not effective_product:
            return ToolResult(
                content="请先为脚本选择产品（或在工具调用时显式传 product）",
                ok=False,
            )

        raw_segments = script.get("segments_json") or script.get("segments")
        if not raw_segments:
            return ToolResult(content="脚本段为空，无法搜索", ok=False)

        segments: list[dict] = (
            json.loads(raw_segments) if isinstance(raw_segments, str) else raw_segments
        )

        results = await match_segments_parallel(
            segments,
            tenant_key=script["tenant_key"],
            product=effective_product,
            embedding_service=self._embedding_service,
            vector_store=self._vector_store,
            material_repo=self._material_repo,
        )
```

（后续 `lines` 拼装部分保持不变。）

### Step 8.4: 运行测试，确认通过

- [ ] Run:
```bash
cd SimpleClaw && uv run pytest tests/Flowcut/test_search_materials_product_fallback.py -v
```
Expected: 3 passed

### Step 8.5: 提交

- [ ] Commit:

```bash
git add SimpleClaw/Flowcut/tools/search_materials.py SimpleClaw/tests/Flowcut/test_search_materials_product_fallback.py
git commit -m "feat(flowcut): search_materials product 三段回退（caller > script > error）"
```

---

## Task 9: 端到端冒烟（无前端）

**Files:** 无新建文件，仅手动验证。

### Step 9.1: 起 server

- [ ] Run（终端 1）：
```bash
cd SimpleClaw && uv run python -m uvicorn Flowcut.api.server:app --port 8001
```

### Step 9.2: 上传带口播的真实视频（手头任一带人声的 mp4，5-20s）

- [ ] Run（终端 2）：
```bash
curl -s -X POST http://localhost:8001/reference-videos/upload \
  -F "tenant_key=t_e2e" \
  -F "file=@<你的本地视频.mp4>" | tee /tmp/upload.json
REF_ID=$(jq -r .ref_video_id /tmp/upload.json)
echo "ref_video_id=$REF_ID"
```

### Step 9.3: 轮询拆镜状态

- [ ] Run:
```bash
for i in $(seq 1 30); do
  status=$(curl -s "http://localhost:8001/reference-videos/$REF_ID" | jq -r .status)
  echo "[$i] status=$status"
  [ "$status" = "READY" ] && break
  [ "$status" = "FAILED" ] && { echo "FAILED"; exit 1; }
  sleep 5
done
```
Expected: 最终输出 `status=READY`，且**不出现** `AWAITING_CLASSIFICATION` 中间态。

### Step 9.4: 取脚本，确认 copy 非空

- [ ] Run:
```bash
SCRIPT_ID=$(curl -s "http://localhost:8001/reference-videos/$REF_ID" | jq -r .script_id)
echo "script_id=$SCRIPT_ID"
curl -s "http://localhost:8001/flowcut/scripts/$SCRIPT_ID" | jq '.segments[] | {idx, visual, copy}'
```
Expected: 至少有一段 segment 的 `copy` 字段是非空字符串（前提：视频本身含口播）。

### Step 9.5: 测改产品接口

- [ ] Run:
```bash
curl -s -X POST "http://localhost:8001/flowcut/scripts/$SCRIPT_ID/update-product" \
  -H 'Content-Type: application/json' \
  -d '{"product":"洗发水X"}' | jq
curl -s "http://localhost:8001/flowcut/scripts/$SCRIPT_ID" | jq .product
```
Expected: 第二条命令输出 `"洗发水X"`。

### Step 9.6: 停 server

- [ ] Ctrl+C 终端 1。

### Step 9.7: 提交（如有日志/小修补）

- [ ] 若 Step 9.1-9.6 跑通无修改，无需提交，直接进入 Task 10。

---

## Task 10: 前端配合改造

**Files:**
- Modify: `flowcut_frontend/src/api/referenceVideos.ts`（移除 classify）
- Create/Modify: `flowcut_frontend/src/api/scripts.ts`（新增 `updateProduct`）
- Delete: `flowcut_frontend/src/components/generate/steps/ClassifyModal.tsx`
- Modify: `flowcut_frontend/src/components/generate/steps/MatchingStep.tsx`（去掉 classify 调用）
- Modify: 上传弹窗（位于 `flowcut_frontend/src/components/material/UploadModal.tsx` 或 generate 流的上传入口），允许 `product` 为空

### Step 10.1: 先把后端不再支持的 classify API 从前端 API 层删掉

- [ ] Read `flowcut_frontend/src/api/referenceVideos.ts`，定位 `classify` 相关导出函数（搜索 `/classify`），整段删除。同时清理顶部该函数对应的 TypeScript 类型 `ClassifyRequest` / `ClassifySegment`（若仅 classify 使用）。

### Step 10.2: 删除 ClassifyModal 组件

- [ ] Run:
```bash
cd /Users/shengxingou-1/电商自动化运营/E-commerceAutomation
grep -rln "ClassifyModal" flowcut_frontend/src
```

记录所有引用文件。在每个引用文件里删掉对 ClassifyModal 的 import / 渲染 / 状态。然后：

```bash
rm flowcut_frontend/src/components/generate/steps/ClassifyModal.tsx
```

### Step 10.3: 调整 MatchingStep 流程

- [ ] Read `flowcut_frontend/src/components/generate/steps/MatchingStep.tsx`。

逻辑改动：原本应当在用户点击"开始匹配"前弹 ClassifyModal 让用户选 scene_role + product。新流程改为：

- 拆镜完成（status=READY）后直接展示 `segments[i].visual` + `segments[i].copy` 列表。
- 提供一个 `<Select>` 给用户挑产品（如果脚本已有 product 就 prefill），用户改后即调用 `POST /flowcut/scripts/{id}/update-product`。
- "匹配素材"按钮点击时调用 `search_materials`（已有的 match API），不再做 scene_role 分类。

具体改动以现状为准；如果该组件原本只在 ClassifyModal 通过后才渲染，去掉这一前置 gate 即可。

### Step 10.4: 加 scripts.updateProduct API

- [ ] Create or extend `flowcut_frontend/src/api/scripts.ts`：

```ts
import { apiClient } from './client'; // 项目现有 API 客户端导入约定，按实际调整

export interface UpdateProductResponse {
  ok: boolean;
  script_id: number;
  product: string | null;
}

export async function updateScriptProduct(
  scriptId: number,
  product: string | null,
): Promise<UpdateProductResponse> {
  const resp = await apiClient.post(
    `/flowcut/scripts/${scriptId}/update-product`,
    { product },
  );
  return resp.data;
}
```

如果 `apiClient` 名字 / 导出方式不同，按 `flowcut_frontend/src/api/match.ts` 或 `referenceVideos.ts` 的现有 pattern 对齐。

### Step 10.5: 上传爆款视频 product 可空

- [ ] 找到上传弹窗里的 product `<Form.Item>` 或 `<Input>`，移除 `rules={[{ required: true }]}`。如果有 placeholder "请选择产品"，改为 "可选 — 可在生成脚本后再选"。

### Step 10.6: 起前后端联调验证

- [ ] Run（终端 1）：`cd SimpleClaw && uv run python -m uvicorn Flowcut.api.server:app --port 8001`
- [ ] Run（终端 2）：`cd flowcut_frontend && npm run dev`
- [ ] 浏览器走一次：上传爆款视频不选产品 → 等拆镜 → 看到 visual+copy 列表 → 改产品 → 点匹配 → 看到候选素材。

### Step 10.7: 提交

- [ ] Commit:

```bash
cd /Users/shengxingou-1/电商自动化运营/E-commerceAutomation
git add flowcut_frontend/
git commit -m "feat(fe): 适配新拆镜流程 — product 可空 + 改产品按钮 + 移除分类弹窗"
```

---

## 收尾

- [ ] **End-of-plan check**：在主分支或当前 feature 分支跑一次：

```bash
cd SimpleClaw && uv run pytest tests/Flowcut/ -v
```
Expected: 所有新增 + 已有用例通过（标记为 `integration` 的需要本机 MySQL）。

- [ ] **可选清理**：grep 全仓确认没有 `AWAITING_CLASSIFICATION` / `DECOMPOSED` / `scene_data_and_product` 残留引用：

```bash
grep -rn "AWAITING_CLASSIFICATION\|DECOMPOSED\|update_scene_data_and_product\|ClassifyModal" \
  SimpleClaw/Flowcut flowcut_frontend/src 2>/dev/null
```
Expected: 无输出（或仅在测试历史 / 注释中说明性出现）。

---

## Spec 覆盖自查

| Spec 章节 | 覆盖 Task |
|-----------|-----------|
| §2.1 新流程 | Task 4 (executor 主体)、Task 6 (上传 OSS 路径) |
| §2.2 废弃项 | Task 5 (clip_create)、Task 6 (classify route) |
| §3 Schema | Task 2 (ADD COLUMN + 孤儿 UPDATE) |
| §4 OSS Key | Task 6 (`uploads/...` 前缀) |
| §5 ASR 修复 | Task 1 (show_utterances) |
| §6.1 上传时 product 可空 | Task 6（路由参数已是可选，OSS 路径不再依赖 product）+ Task 10.5（前端表单去 required） |
| §6.2 改产品接口 | Task 7 |
| §6.3 工具回退 | Task 8 |
| §7 受影响文件 | Task 3 (script_repo)、Task 4 (executor + ref_video_repo)、Task 5 (streams/worker)、Task 6 (routes)、Task 7 (scripts route)、Task 8 (search_materials)、Task 10 (前端) |
| §8 测试计划 | Task 1/3/7/8 单元 + 集成；Task 9 e2e |
