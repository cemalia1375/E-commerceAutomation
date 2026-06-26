# 高光成片前贴叠加 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在跨集高光成片导出流程中新增「前贴」图层——用户上传全幅 PNG，选中后导出时由 ffmpeg 将其烧录到剪辑段画面上；数字人段不叠加。

**Architecture:** 复用 `fc_highlight_asset` 表（新增 `asset_type='preroll'`）；`fc_creative` 新增 `preroll_asset_id` 字段记录用户选择；executor 根据有无前贴 / 数字人走两条 ffmpeg 分支；前端复用现有素材库 Tab 模式和 SequentialPreview 叠加预览。

**Tech Stack:** Python/FastAPI/aiomysql，React 19/TypeScript/Ant Design 6，ffmpeg (`scale2ref` overlay 滤镜)。

## Global Constraints

- 前贴**只叠加在剪辑段**（clip），数字人段不叠加
- 不新建数据库表，复用 `fc_highlight_asset`，新增 `asset_type='preroll'`
- 无 `preroll_scope` 字段，前贴存在即表示叠加到剪辑段
- 仅影响 `continuous_cross_episode` 类型成片
- Python 测试使用 pytest + `@pytest.mark.unit`
- TypeScript 禁止 `any`；组件 props 用显式 interface/type
- 文件路径前缀：后端 `SimpleClaw/`，前端 `flowcut_frontend/src/`

---

### Task 1: 前端类型 + API 客户端

**Files:**
- Modify: `flowcut_frontend/src/types/index.ts:62`（`HighlightAssetType`）
- Modify: `flowcut_frontend/src/types/index.ts:118`（`Creative` 接口）
- Modify: `flowcut_frontend/src/api/qianchuan.ts:94`（`creativeFromBackend`）
- Modify: `flowcut_frontend/src/api/qianchuan.ts:192`（新增 `setCreativePreroll`）

**Interfaces:**
- Produces: `Creative.prerollAssetId: number | null | undefined`，`setCreativePreroll(creativeId, prerollAssetId)` 导出函数

- [ ] **Step 1: 扩展 `HighlightAssetType`**

文件 `flowcut_frontend/src/types/index.ts` 第 62 行：
```typescript
// 改前
export type HighlightAssetType = 'episode_source' | 'digital_human_connector'
// 改后
export type HighlightAssetType = 'episode_source' | 'digital_human_connector' | 'preroll'
```

- [ ] **Step 2: 在 `Creative` 接口加 `prerollAssetId` 字段**

找到约第 118 行 `connectorAssetId?: number | null`，在其正下方加一行：
```typescript
  connectorAssetId?: number | null
  prerollAssetId?: number | null
```

- [ ] **Step 3: 在 `creativeFromBackend()` 映射新字段**

文件 `flowcut_frontend/src/api/qianchuan.ts`，找到约第 94 行 `connectorAssetId:` 那行，在其正下方加一行：
```typescript
    connectorAssetId: (raw.connector_asset_id as number | null) ?? null,
    prerollAssetId: (raw.preroll_asset_id as number | null) ?? null,
```

- [ ] **Step 4: 在文件末尾 `setCreativeConnector` 之后新增 `setCreativePreroll`**

```typescript
export async function setCreativePreroll(
  creativeId: string | number,
  prerollAssetId: number | null,
): Promise<void> {
  await apiClient.patch(`/creatives/${creativeId}/preroll`, {
    preroll_asset_id: prerollAssetId,
  })
}
```

- [ ] **Step 5: TypeScript 类型检查**

```bash
cd flowcut_frontend && npx tsc --noEmit
```
预期：零类型错误

- [ ] **Step 6: Commit**

```bash
git add flowcut_frontend/src/types/index.ts flowcut_frontend/src/api/qianchuan.ts
git commit -m "feat: 前端类型和 API 客户端支持前贴 preroll_asset_id"
```

---

### Task 2: 数据库迁移 + CreativeRepo 新方法

**Files:**
- Modify: `SimpleClaw/Flowcut/storage/database.py`（`ensure_schema` 末尾）
- Modify: `SimpleClaw/Flowcut/storage/creative_repo.py:290`（`set_connector_asset` 之后）
- Create: `SimpleClaw/tests/test_creative_repo_preroll.py`

**Interfaces:**
- Produces: `CreativeRepository.set_preroll_asset(creative_id: int, preroll_asset_id: int | None) -> None`

- [ ] **Step 1: 写单测**

新建 `SimpleClaw/tests/test_creative_repo_preroll.py`：

```python
import pytest
from unittest.mock import AsyncMock, MagicMock


def _make_repo():
    mock_cur = AsyncMock()
    mock_conn = MagicMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)
    mock_conn.cursor = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=mock_cur),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    mock_db = MagicMock()
    mock_db.acquire = MagicMock(return_value=mock_conn)
    return mock_cur, mock_db


@pytest.mark.unit
@pytest.mark.asyncio
async def test_set_preroll_asset_sets_correct_id():
    mock_cur, mock_db = _make_repo()
    from Flowcut.storage.creative_repo import CreativeRepository

    repo = CreativeRepository(mock_db)
    await repo.set_preroll_asset(42, 7)

    call_args = mock_cur.execute.call_args[0]
    assert "preroll_asset_id" in call_args[0]
    assert call_args[1][0] == 7    # preroll_asset_id
    assert call_args[1][2] == 42   # creative_id


@pytest.mark.unit
@pytest.mark.asyncio
async def test_set_preroll_asset_clear_passes_none():
    mock_cur, mock_db = _make_repo()
    from Flowcut.storage.creative_repo import CreativeRepository

    repo = CreativeRepository(mock_db)
    await repo.set_preroll_asset(42, None)

    params = mock_cur.execute.call_args[0][1]
    assert params[0] is None
    assert params[2] == 42
```

- [ ] **Step 2: 跑单测（预期 ImportError，因方法尚未存在）**

```bash
cd SimpleClaw && uv run pytest tests/test_creative_repo_preroll.py -v
```
预期：`AttributeError: 'CreativeRepository' object has no attribute 'set_preroll_asset'`

- [ ] **Step 3: 在 `database.py` 追加 preroll 迁移**

打开 `SimpleClaw/Flowcut/storage/database.py`，找到最后一个 `fc_creative` 迁移块（`2026-06-12` 那段，约第 892 行以 `except Exception: pass` 结尾）。在该 except 块之后**紧接着**追加：

```python
            # 迁移（2026-06-25）：fc_creative 加前贴字段
            await cur.execute(
                """
                SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME   = 'fc_creative'
                  AND COLUMN_NAME  = 'preroll_asset_id'
                """,
            )
            _pr_row = await cur.fetchone()
            if _pr_row and _pr_row[0] == 0:
                await cur.execute(
                    "ALTER TABLE fc_creative"
                    " ADD COLUMN preroll_asset_id BIGINT NULL AFTER connector_asset_id"
                )
```

- [ ] **Step 4: 在 `creative_repo.py` 的 `set_connector_asset` 之后加新方法**

打开 `SimpleClaw/Flowcut/storage/creative_repo.py`，找到 `set_connector_asset` 方法（约第 281 行，方法体约 9 行），在其闭合之后紧接着加：

```python
    async def set_preroll_asset(
        self, creative_id: int, preroll_asset_id: int | None,
    ) -> None:
        """设置/清空成片要叠加的前贴素材（跨集高光用）。"""
        async with self._db.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "UPDATE fc_creative SET preroll_asset_id=%s, updated_at=%s WHERE id=%s",
                    (preroll_asset_id, _now(), creative_id),
                )
```

- [ ] **Step 5: 跑单测确认通过**

```bash
cd SimpleClaw && uv run pytest tests/test_creative_repo_preroll.py -v
```
预期：2 个 PASS

- [ ] **Step 6: Commit**

```bash
git add SimpleClaw/Flowcut/storage/database.py \
        SimpleClaw/Flowcut/storage/creative_repo.py \
        SimpleClaw/tests/test_creative_repo_preroll.py
git commit -m "feat: DB 迁移新增 preroll_asset_id 字段，CreativeRepo 新增 set_preroll_asset"
```

---

### Task 3: 后端路由 — 新端点 + 现有路由修改

**Files:**
- Modify: `SimpleClaw/Flowcut/api/routes/creatives.py`
- Create: `SimpleClaw/tests/test_highlight_export_routes.py`

**Interfaces:**
- Consumes: `CreativeRepository.set_preroll_asset()` (Task 2)，`HighlightAssetRepository.get()` (已有)
- Produces: `PATCH /creatives/{creative_id}/preroll` endpoint

- [ ] **Step 1: 写路由逻辑的单测**

新建 `SimpleClaw/tests/test_highlight_export_routes.py`：

```python
import pytest


@pytest.mark.unit
def test_download_fast_path_logic():
    """快路径只在 connector 和 preroll 都为空时才允许。"""
    def should_fast_path(row: dict) -> bool:
        return row.get("connector_asset_id") is None and row.get("preroll_asset_id") is None

    assert should_fast_path({"connector_asset_id": None, "preroll_asset_id": None}) is True
    assert should_fast_path({"connector_asset_id": 1,    "preroll_asset_id": None}) is False
    assert should_fast_path({"connector_asset_id": None, "preroll_asset_id": 5})    is False
    assert should_fast_path({"connector_asset_id": 1,    "preroll_asset_id": 5})    is False


@pytest.mark.unit
def test_export_precondition_logic():
    """只要 connector 或 preroll 任一有值，就可以导出。"""
    def can_export(row: dict) -> bool:
        return bool(row.get("connector_asset_id")) or bool(row.get("preroll_asset_id"))

    assert can_export({"connector_asset_id": None, "preroll_asset_id": None}) is False
    assert can_export({"connector_asset_id": 1,    "preroll_asset_id": None}) is True
    assert can_export({"connector_asset_id": None, "preroll_asset_id": 5})    is True
    assert can_export({"connector_asset_id": 1,    "preroll_asset_id": 5})    is True
```

- [ ] **Step 2: 跑单测（预期全 PASS，纯逻辑无需路由实现）**

```bash
cd SimpleClaw && uv run pytest tests/test_highlight_export_routes.py -v
```
预期：4 个 PASS

- [ ] **Step 3: 在 `creatives.py` 的 `ConnectorUpdate` 之后加 `PrerollUpdate` 模型**

打开 `SimpleClaw/Flowcut/api/routes/creatives.py`，找到 `class ConnectorUpdate(BaseModel):`（约第 443 行），在其后加：

```python
class PrerollUpdate(BaseModel):
    preroll_asset_id: int | None = None
```

- [ ] **Step 4: 添加 `PATCH /{creative_id}/preroll` 路由**

找到 `PATCH /{creative_id}/connector` 路由的处理函数（约第 451 行），在其闭合的 `return` 语句之后，紧接着插入新路由：

```python
@router.patch("/{creative_id}/preroll")
async def set_creative_preroll(
    creative_id: int,
    body: PrerollUpdate,
    request: Request,
    tenant_key: str = Depends(require_tenant),
):
    """持久化跨集高光要叠加的前贴素材（仅记录，叠加发生在导出时）。"""
    c = request.app.state.container
    row = await c.creative_repo.get(creative_id)
    if row is None or row.get("tenant_key") != tenant_key:
        raise HTTPException(404, f"creative {creative_id} not found")
    preroll_id = body.preroll_asset_id
    if preroll_id is not None:
        asset = await c.highlight_asset_repo.get(int(preroll_id))
        if (
            asset is None
            or asset.get("tenant_key") != tenant_key
            or asset.get("asset_type") != "preroll"
        ):
            raise HTTPException(422, "preroll_asset_id 不是有效的前贴素材")
    await c.creative_repo.set_preroll_asset(creative_id, preroll_id)
    return {"ok": True, "creative_id": creative_id, "preroll_asset_id": preroll_id}
```

- [ ] **Step 5: 修改 `download_creative` 路由，收紧快路径**

找到 `download_creative` 路由，找到其中直接生成 presigned URL 并返回 `RedirectResponse` 的部分。在生成 URL 之前，加入拦截判断：

```python
    # 有前贴或数字人时必须走 export-highlight，不能直接 302
    if row.get("connector_asset_id") is not None or row.get("preroll_asset_id") is not None:
        raise HTTPException(422, "该成片有前贴或数字人，请通过导出接口下载")
```

此判断放在 `oss_key = row.get("oss_key")` 非空校验之后、生成 presigned URL 之前。

- [ ] **Step 6: 修改 `export_highlight_creative` 路由，放宽前置校验**

找到该路由中这两行：
```python
    if row.get("connector_asset_id") is None:
        raise HTTPException(422, "未选择数字人；纯片请直接下载，无需拼接导出")
```
替换为：
```python
    if row.get("connector_asset_id") is None and row.get("preroll_asset_id") is None:
        raise HTTPException(422, "未选择数字人或前贴；纯片请直接下载，无需拼接导出")
```

- [ ] **Step 7: Commit**

```bash
git add SimpleClaw/Flowcut/api/routes/creatives.py \
        SimpleClaw/tests/test_highlight_export_routes.py
git commit -m "feat: 新增 PATCH /preroll 端点，收紧纯片下载快路径，放宽导出前置校验"
```

---

### Task 4: 后端 executor — ffmpeg overlay 两路分支

**Files:**
- Modify: `SimpleClaw/Flowcut/runtime/executors.py`
- Create: `SimpleClaw/tests/test_highlight_export_executor.py`

**Interfaces:**
- Produces: `_ffmpeg_normalize_with_overlay(source_path: str, overlay_path: str, output_path: str) -> None`

- [ ] **Step 1: 写单测**

新建 `SimpleClaw/tests/test_highlight_export_executor.py`：

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.unit
def test_ffmpeg_normalize_with_overlay_uses_filter_complex():
    """_ffmpeg_normalize_with_overlay 应在 ffmpeg 参数里包含 filter_complex 和 overlay。"""
    from Flowcut.runtime.executors import _ffmpeg_normalize_with_overlay

    with patch("Flowcut.runtime.executors._run_ffmpeg") as mock_run:
        _ffmpeg_normalize_with_overlay("/tmp/clip.mp4", "/tmp/overlay.png", "/tmp/out.mp4")

    args = " ".join(mock_run.call_args[0][0])
    assert "-filter_complex" in args
    assert "overlay" in args
    assert "/tmp/clip.mp4" in args
    assert "/tmp/overlay.png" in args
    assert "/tmp/out.mp4" in args


@pytest.mark.unit
@pytest.mark.asyncio
async def test_executor_preroll_only_uses_overlay_no_concat():
    """有前贴无数字人：应调 overlay 归一化、不 concat，直接上传 clip。"""
    from Flowcut.runtime.executors import make_highlight_export_executor
    from simpleclaw.runtime.task_queue import TaskEnvelope

    creative = {
        "id": 1, "oss_key": "clip/1.mp4", "tenant_key": "t1",
        "connector_asset_id": None, "preroll_asset_id": 99,
        "source_drama_name": "测试剧",
    }
    preroll_asset = {"oss_key": "prerolls/logo.png", "asset_type": "preroll", "tenant_key": "t1"}
    creative_repo = AsyncMock()
    creative_repo.get.return_value = creative
    highlight_asset_repo = AsyncMock()
    highlight_asset_repo.get.return_value = preroll_asset
    oss_client = MagicMock()
    oss_client.presigned_get_url.return_value = "https://example.com/out.mp4"

    executor = make_highlight_export_executor(
        creative_repo=creative_repo,
        highlight_asset_repo=highlight_asset_repo,
        oss_client=oss_client,
    )
    envelope = TaskEnvelope(
        task_type="highlight_export", payload={"creative_id": 1}, tenant_key="t1"
    )

    with (
        patch("Flowcut.runtime.executors._ffmpeg_normalize_with_overlay") as mock_overlay,
        patch("Flowcut.runtime.executors._ffmpeg_normalize_clip") as mock_norm,
        patch("Flowcut.runtime.executors._ffmpeg_concat") as mock_concat,
        patch("Flowcut.runtime.executors._write_concat_list"),
        patch("tempfile.mkdtemp", return_value="/tmp/test_dir"),
        patch("shutil.rmtree"),
        patch("asyncio.get_running_loop") as mock_loop,
    ):
        mock_loop.return_value.run_in_executor = AsyncMock()
        result = await executor(envelope)

    assert result.ok, result.error
    mock_overlay.assert_called_once()
    mock_norm.assert_not_called()
    mock_concat.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_executor_preroll_plus_dh_overlays_clip_and_concats():
    """有前贴有数字人：应 overlay 归一化 clip、普通归一化 DH、concat。"""
    from Flowcut.runtime.executors import make_highlight_export_executor
    from simpleclaw.runtime.task_queue import TaskEnvelope

    creative = {
        "id": 2, "oss_key": "clip/2.mp4", "tenant_key": "t1",
        "connector_asset_id": 10, "preroll_asset_id": 99,
        "source_drama_name": "测试剧",
    }
    connector_asset = {
        "oss_key": "dh/connector.mp4", "oss_url": "",
        "asset_type": "digital_human_connector", "tenant_key": "t1",
    }
    preroll_asset = {"oss_key": "prerolls/logo.png", "asset_type": "preroll", "tenant_key": "t1"}

    creative_repo = AsyncMock()
    creative_repo.get.return_value = creative
    highlight_asset_repo = AsyncMock()

    async def get_asset(asset_id: int):
        return connector_asset if asset_id == 10 else preroll_asset

    highlight_asset_repo.get.side_effect = get_asset
    oss_client = MagicMock()
    oss_client.presigned_get_url.return_value = "https://example.com/out.mp4"

    executor = make_highlight_export_executor(
        creative_repo=creative_repo,
        highlight_asset_repo=highlight_asset_repo,
        oss_client=oss_client,
    )
    envelope = TaskEnvelope(
        task_type="highlight_export", payload={"creative_id": 2}, tenant_key="t1"
    )

    with (
        patch("Flowcut.runtime.executors._ffmpeg_normalize_with_overlay") as mock_overlay,
        patch("Flowcut.runtime.executors._ffmpeg_normalize_clip") as mock_norm,
        patch("Flowcut.runtime.executors._ffmpeg_concat") as mock_concat,
        patch("Flowcut.runtime.executors._write_concat_list"),
        patch("tempfile.mkdtemp", return_value="/tmp/test_dir"),
        patch("shutil.rmtree"),
        patch("asyncio.get_running_loop") as mock_loop,
    ):
        mock_loop.return_value.run_in_executor = AsyncMock()
        result = await executor(envelope)

    assert result.ok, result.error
    mock_overlay.assert_called_once()   # clip 段用 overlay 归一化
    mock_norm.assert_called_once()      # DH 用普通归一化
    mock_concat.assert_called_once()    # 拼接
```

- [ ] **Step 2: 跑单测，确认 `_ffmpeg_normalize_with_overlay` 不存在而失败**

```bash
cd SimpleClaw && uv run pytest tests/test_highlight_export_executor.py::test_ffmpeg_normalize_with_overlay_uses_filter_complex -v
```
预期：`ImportError` 或 `AttributeError`

- [ ] **Step 3: 在 `executors.py` 中，在 `_ffmpeg_normalize_clip` 之后添加 `_ffmpeg_normalize_with_overlay`**

打开 `SimpleClaw/Flowcut/runtime/executors.py`，找到 `_ffmpeg_normalize_clip` 函数（第 1404 行）的末尾（约第 1430 行），在其之后紧接着加：

```python
def _ffmpeg_normalize_with_overlay(source_path: str, overlay_path: str, output_path: str) -> None:
    """归一化视频并将 overlay_path（PNG）全幅叠加在整段画面上。

    使用 scale2ref 将前贴缩放为与视频相同的分辨率，再用 overlay=0:0 铺满。
    """
    _run_ffmpeg(
        [
            "-i", source_path,
            "-i", overlay_path,
            "-filter_complex",
            "[1:v]format=rgba[ovr];"
            "[0:v]scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1[base];"
            "[ovr][base]scale2ref[ovr_s][base2];"
            "[base2][ovr_s]overlay=0:0[outv]",
            "-map", "[outv]",
            "-map", "0:a?",
            "-r", "30",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "18",
            "-c:a", "aac",
            "-ar", "44100",
            "-ac", "2",
            "-movflags", "+faststart",
            output_path,
        ],
        timeout=1800,
    )
```

- [ ] **Step 4: 重写 `make_highlight_export_executor` 的 `execute` 函数体**

找到 `async def execute(task: TaskEnvelope) -> TaskExecutionResult:` 内部（第 1297 行起，到 `return execute` 前），将整个函数体替换为：

```python
    async def execute(task: TaskEnvelope) -> TaskExecutionResult:
        creative_id = int(task.payload["creative_id"])
        creative = await creative_repo.get(creative_id)
        if creative is None:
            return TaskExecutionResult.failed(error=f"creative_id={creative_id} not found")
        clip_key = str(creative.get("oss_key") or "")
        if not clip_key:
            return TaskExecutionResult.failed(error=f"creative_id={creative_id} 还没有 1 分钟片")

        connector_asset_id = creative.get("connector_asset_id")
        preroll_asset_id = creative.get("preroll_asset_id")

        if connector_asset_id is None and preroll_asset_id is None:
            return TaskExecutionResult.failed(error="未选择数字人或前贴，无法导出")

        connector = None
        if connector_asset_id is not None:
            connector = await highlight_asset_repo.get(int(connector_asset_id))
            if connector is None:
                return TaskExecutionResult.failed(
                    error=f"connector_asset_id={connector_asset_id} not found"
                )

        preroll = None
        if preroll_asset_id is not None:
            preroll = await highlight_asset_repo.get(int(preroll_asset_id))
            if preroll is None:
                return TaskExecutionResult.failed(error="前贴素材不存在，请重新选择")

        tmp_dir = tempfile.mkdtemp(prefix=f"flowcut_highlight_export_{creative_id}_")
        try:
            loop = asyncio.get_running_loop()
            clip_src = os.path.join(tmp_dir, "clip.mp4")
            await loop.run_in_executor(None, oss_client.download, clip_key, clip_src)

            # 剪辑段：有前贴则 overlay 归一化，否则普通归一化
            clip_processed = os.path.join(tmp_dir, "clip_out.mp4")
            if preroll is not None:
                preroll_src = os.path.join(tmp_dir, "preroll.png")
                await loop.run_in_executor(
                    None, oss_client.download,
                    str(preroll.get("oss_key") or ""), preroll_src,
                )
                _ffmpeg_normalize_with_overlay(clip_src, preroll_src, clip_processed)
            else:
                _ffmpeg_normalize_clip(clip_src, clip_processed)

            # 数字人段（如有）：普通归一化后 concat
            if connector is not None:
                dh_src = os.path.join(tmp_dir, "dh.mp4")
                dh_norm = os.path.join(tmp_dir, "dh_norm.mp4")
                await loop.run_in_executor(
                    None, oss_client.download,
                    str(connector.get("oss_key") or connector.get("oss_url") or ""), dh_src,
                )
                _ffmpeg_normalize_clip(dh_src, dh_norm)
                output_path = os.path.join(tmp_dir, "export.mp4")
                concat_list = os.path.join(tmp_dir, "concat.txt")
                _write_concat_list(concat_list, [clip_processed, dh_norm])
                _ffmpeg_concat(concat_list, output_path)
            else:
                output_path = clip_processed

            tenant_key = str(creative.get("tenant_key") or task.tenant_key or "flowcut")
            oss_key = f"creatives/{tenant_key}/export/{creative_id}/{uuid.uuid4().hex}.mp4"
            await loop.run_in_executor(None, oss_client.upload, output_path, oss_key)
            drama = str(creative.get("source_drama_name") or creative.get("source_asset_name") or "高光")
            suffix = "数字人" if connector is not None else "前贴"
            result_url = oss_client.presigned_get_url(
                oss_key, disposition_filename=f"{drama}_{creative_id}_{suffix}.mp4"
            )
            return TaskExecutionResult.succeeded(
                summary=f"highlight export composed creative_id={creative_id}",
                details={"creative_id": creative_id, "oss_key": oss_key, "result_url": result_url},
            )
        except Exception as exc:
            return TaskExecutionResult.failed(error=f"{type(exc).__name__}: {exc}")
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
```

- [ ] **Step 5: 跑单测确认通过**

```bash
cd SimpleClaw && uv run pytest tests/test_highlight_export_executor.py -v
```
预期：3 个 PASS

- [ ] **Step 6: Commit**

```bash
git add SimpleClaw/Flowcut/runtime/executors.py \
        SimpleClaw/tests/test_highlight_export_executor.py
git commit -m "feat: executor 新增 overlay 分支，前贴叠加在剪辑段，数字人段不叠加"
```

---

### Task 5: 前贴库 Tab（HighlightAssetLibrary）

**Files:**
- Modify: `flowcut_frontend/src/components/material/HighlightAssetLibrary.tsx`

**Interfaces:**
- Consumes: `HighlightAssetType` (Task 1)，`listHighlightAssets`, `uploadHighlightAsset` (已有)

- [ ] **Step 1: 扩展 `ViewMode` 类型（第 27 行）**

```typescript
// 改前
type ViewMode = 'episode_source' | 'digital_human_connector'
// 改后
type ViewMode = 'episode_source' | 'digital_human_connector' | 'preroll'
```

- [ ] **Step 2: 修改 `groupAssets` 支持 preroll 平铺**

将 `groupAssets` 函数改为：

```typescript
function groupAssets(assets: HighlightAsset[], mode: ViewMode) {
  if (mode === 'preroll') {
    return [['前贴', assets]] as [string, HighlightAsset[]][]
  }
  const groups: Record<string, HighlightAsset[]> = {}
  for (const asset of assets) {
    const key =
      mode === 'episode_source'
        ? asset.dramaName || '未命名剧集'
        : asset.connectorRole || '通用数字人'
    if (!groups[key]) groups[key] = []
    groups[key].push(asset)
  }
  return Object.entries(groups).sort(([a], [b]) => a.localeCompare(b, 'zh-Hans-CN'))
}
```

- [ ] **Step 3: 修改 `canUpload`（preroll 只需有文件）**

```typescript
// 改前
  const canUpload =
    files.length > 0 &&
    (mode === 'digital_human_connector' || dramaName.trim().length > 0)
// 改后
  const canUpload =
    files.length > 0 &&
    (mode === 'digital_human_connector' || mode === 'preroll' || dramaName.trim().length > 0)
```

- [ ] **Step 4: 在 Segmented 加第三项「前贴库」**

找到 Segmented 的 `options` prop，改为三项：

```typescript
          options={[
            { label: '原片库', value: 'episode_source' },
            { label: '数字人库', value: 'digital_human_connector' },
            { label: '前贴库', value: 'preroll' },
          ]}
```

- [ ] **Step 5: 修改上传区域 — 三路分支、image accept、按钮文字**

找到上传区域的 `{mode === 'episode_source' ? (...) : (...)}` 结构，改为三路：

```typescript
          {mode === 'episode_source' ? (
            <>
              <Input
                placeholder="AI 漫剧名称"
                value={dramaName}
                onChange={(e) => setDramaName(e.target.value)}
                style={{ width: 180 }}
                size="small"
              />
              <InputNumber
                placeholder="起始集数"
                min={1}
                value={episodeNo}
                onChange={(v) => setEpisodeNo(v ?? null)}
                style={{ width: 110 }}
                size="small"
              />
            </>
          ) : mode === 'digital_human_connector' ? (
            <Select
              value={connectorRole}
              onChange={setConnectorRole}
              options={[
                { label: '通用数字人', value: '通用数字人' },
                { label: '开场推荐', value: '开场推荐' },
                { label: '产品转化', value: '产品转化' },
                { label: '剧情承接', value: '剧情承接' },
              ]}
              style={{ width: 150 }}
              size="small"
            />
          ) : null}
```

找到 `<Upload>` 组件，将 `accept` 改为：
```typescript
            accept={mode === 'preroll' ? 'image/*' : 'video/*'}
```

找到上传按钮的文字，改为：
```typescript
              <Button size="small" icon={<UploadOutlined />}>
                {mode === 'preroll' ? '选择图片' : '选择视频'}
              </Button>
```

- [ ] **Step 6: 在卡片 thumbWrap 里，preroll 用 `<img>` 替换 `<video>`**

找到 `renderSection` 内 `<div className={styles.thumbWrap}>` 块，替换为：

```typescript
            <div className={styles.thumbWrap}>
              {asset.assetType === 'preroll' ? (
                <img
                  className={styles.thumb}
                  src={asset.ossUrl}
                  alt={asset.name}
                  style={{ objectFit: 'contain', background: '#f0f0f0' }}
                />
              ) : (
                <video
                  className={styles.thumb}
                  src={asset.ossUrl}
                  controls
                  preload="metadata"
                  onLoadedMetadata={(e) => {
                    const dur = e.currentTarget.duration
                    if (Number.isFinite(dur) && dur > 0) {
                      setDurations((prev) => ({ ...prev, [asset.id]: dur }))
                    }
                  }}
                />
              )}
              {asset.assetType !== 'preroll' && (
                <span className={styles.duration}>
                  {formatDuration(durations[asset.id] ?? asset.duration)}
                </span>
              )}
            </div>
```

- [ ] **Step 7: TypeScript 检查**

```bash
cd flowcut_frontend && npx tsc --noEmit
```
预期：零类型错误

- [ ] **Step 8: Commit**

```bash
git add flowcut_frontend/src/components/material/HighlightAssetLibrary.tsx
git commit -m "feat: HighlightAssetLibrary 新增前贴库 Tab，支持图片上传和平铺展示"
```

---

### Task 6: 成品库 — 前贴选择器 + SequentialPreview 叠加预览

**Files:**
- Modify: `flowcut_frontend/src/components/creative/HighlightCreativeLibrary.tsx`

**Interfaces:**
- Consumes: `Creative.prerollAssetId` (Task 1)，`setCreativePreroll()` (Task 1)，`HighlightAsset` (已有)，`listHighlightAssets` (已有)

- [ ] **Step 1: 在顶部 import 中加入 `setCreativePreroll`**

找到从 `../../api/qianchuan` 的 import，在其中加入 `setCreativePreroll`：

```typescript
import {
  // ...已有的...
  setCreativeConnector,
  setCreativePreroll,   // ← 新增
  // ...其余...
} from '../../api/qianchuan'
```

- [ ] **Step 2: 添加前贴 state 和 `prerollOf` 辅助函数**

在 `dhChoice` state 声明（约第 143 行）之后，紧接着加：

```typescript
  const [prerollAssets, setPrerollAssets] = useState<HighlightAsset[]>([])
  const [prerollChoice, setPrerollChoice] = useState<Record<string, number | null | undefined>>({})
```

在 `connectorOf` 函数之后，紧接着加：

```typescript
  const prerollOf = (creative: Creative): number | null =>
    creative.id in prerollChoice
      ? (prerollChoice[creative.id] ?? null)
      : (creative.prerollAssetId ?? null)
```

- [ ] **Step 3: 在 `useEffect` 里加载前贴素材列表**

找到 `useEffect` 内加载 `digitalHumans` 的 `void (async () => {...})()` 块，在其后紧接着加：

```typescript
    void (async () => {
      try {
        setPrerollAssets(
          await listHighlightAssets(getTenantKey(), { assetType: 'preroll' }),
        )
      } catch {
        // 前贴素材拉取失败不阻断主流程
      }
    })()
```

- [ ] **Step 4: 添加 `handleSelectPreroll` 函数**

在 `handleSelectConnector` 函数（约第 278 行）之后，紧接着加：

```typescript
  const handleSelectPreroll = async (creative: Creative, prerollId: number | null) => {
    setPrerollChoice((prev) => ({ ...prev, [creative.id]: prerollId }))
    try {
      await setCreativePreroll(creative.id, prerollId)
      await refetch()
    } catch (err) {
      message.error(err instanceof Error ? err.message : '保存前贴选择失败')
    }
  }
```

- [ ] **Step 5: 修改 `handleExport`，有前贴时也走异步导出**

找到 `handleExport` 内的快路径判断：
```typescript
      if (connectorOf(creative) == null) {
```
改为：
```typescript
      if (connectorOf(creative) == null && prerollOf(creative) == null) {
```

- [ ] **Step 6: 在 `renderCrossEpisodeCreative` 里的数字人 section 之后插入前贴 section**

找到数字人 section 的闭合 `</section>` 标签（约第 537 行），在其**之后**插入：

```tsx
            <section className={styles.crossPanel}>
              <div className={styles.sectionTitle}>前贴</div>
              <Select
                size="small"
                className={styles.crossSelect}
                style={{ width: '100%' }}
                value={prerollOf(creative) ?? 0}
                disabled={busy}
                onChange={(v) => handleSelectPreroll(creative, v === 0 ? null : (v as number))}
                options={[
                  { label: '不使用前贴', value: 0 },
                  ...prerollAssets.map((p) => ({ label: p.name, value: p.id })),
                ]}
              />
              {(() => {
                const asset = prerollAssets.find((p) => p.id === prerollOf(creative))
                return asset ? (
                  <img
                    src={asset.ossUrl}
                    alt={asset.name}
                    style={{ width: '100%', marginTop: 8, objectFit: 'contain', maxHeight: 120, background: '#f0f0f0' }}
                  />
                ) : null
              })()}
            </section>
```

- [ ] **Step 7: 给 `SequentialPreview` 加 `onSegmentChange` 回调 prop**

找到 `SequentialPreview` 函数（第 35 行），在 props 类型里加：

```typescript
function SequentialPreview({
  urls,
  labels,
  className,
  onSegmentChange,
}: {
  urls: string[]
  labels?: string[]
  className?: string
  onSegmentChange?: (idx: number) => void
}) {
```

在 `setIdx(0)` 的 `useEffect` 里，同步通知：
```typescript
  useEffect(() => { setIdx(0); onSegmentChange?.(0) }, [joined])
```

在 `onEnded` handler 里：
```typescript
      onEnded={() => {
        setIdx((i) => {
          const next = i < clips.length - 1 ? i + 1 : i
          onSegmentChange?.(next)
          return next
        })
      }}
```

在 dot 按钮的 `onClick` 里：
```typescript
              onClick={() => { setIdx(i); onSegmentChange?.(i) }}
```

- [ ] **Step 8: 在 `SequentialPreview` 之后新增 `PrerollOverlayPreview` 组件，并替换调用处**

在 `SequentialPreview` 函数的闭合 `}` 之后，紧接着加：

```typescript
function PrerollOverlayPreview({
  clipUrl,
  dhUrl,
  prerollUrl,
  videoClassName,
}: {
  clipUrl: string
  dhUrl: string
  prerollUrl: string | null
  videoClassName?: string
}) {
  const urls = [clipUrl, dhUrl].filter(Boolean)
  const labels = ['剪辑', '数字人'].slice(0, urls.length)
  const [segIdx, setSegIdx] = useState(0)
  const isClipSegment = segIdx === 0
  return (
    <div style={{ position: 'relative', display: 'inline-block', width: '100%' }}>
      <SequentialPreview
        urls={urls}
        labels={labels}
        className={videoClassName}
        onSegmentChange={setSegIdx}
      />
      {prerollUrl && isClipSegment && (
        <img
          src={prerollUrl}
          alt="前贴预览"
          style={{
            position: 'absolute',
            top: 0,
            left: 0,
            width: '100%',
            height: '100%',
            objectFit: 'fill',
            pointerEvents: 'none',
          }}
        />
      )}
    </div>
  )
}
```

在 `renderCrossEpisodeCreative` 里，找到 `<SequentialPreview ... />` 的调用（约第 542 行），将整个 `{clipUrl ? (...) : (...)}` 块替换为：

```tsx
            {clipUrl ? (
              <PrerollOverlayPreview
                clipUrl={clipUrl}
                dhUrl={dhAsset?.ossUrl ?? ''}
                prerollUrl={
                  prerollOf(creative) != null
                    ? (prerollAssets.find((p) => p.id === prerollOf(creative))?.ossUrl ?? null)
                    : null
                }
                videoClassName={styles.crossVideo}
              />
            ) : (
              <div className={styles.crossPlaceholder}>待生成</div>
            )}
```

- [ ] **Step 9: TypeScript 检查**

```bash
cd flowcut_frontend && npx tsc --noEmit
```
预期：零类型错误

- [ ] **Step 10: Commit**

```bash
git add flowcut_frontend/src/components/creative/HighlightCreativeLibrary.tsx
git commit -m "feat: 成品库添加前贴选择器，SequentialPreview 叠加前贴图层预览"
```
