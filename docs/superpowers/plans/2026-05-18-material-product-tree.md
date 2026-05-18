# 素材库产品分层 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将前端素材库从平铺 category 过滤改造为「产品 → 场景角色」两级树形结构，与 OSS 路径和 MySQL 字段保持一致；并支持 zip 批量上传自动归类。

**Architecture:** 后端微调三处接口（重命名 tree、改 upload OSS 路径、新增 zip 上传）；前端引入独立 `productTreeStore` 管理树状态，新增左侧 `MaterialSidebar`、上传 `UploadModal` + `ZipPreview` 子组件，改造 `MaterialTab` 整体布局。MVP 仅视频生效。

**Tech Stack:** FastAPI / aiomysql / Volcengine TOS（后端）；React 19 + TypeScript + Ant Design 6 + Zustand（前端）

**Spec:** `docs/superpowers/specs/2026-05-18-material-product-tree-design.md`

---

## Phase A — 后端接口改造（4 tasks）

### Task 1: 重构 `/materials/tree-summary` → `/materials/tree`

**Files:**
- Modify: `SimpleClaw/Flowcut/api/routes/materials.py:278-313`
- Test: `SimpleClaw/tests/test_materials_tree_route.py` (new)

- [ ] **Step 1: 写失败测试**

Create `SimpleClaw/tests/test_materials_tree_route.py`:

```python
"""Tests for GET /materials/tree route."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def _build_tree(rows: list[dict]) -> list[dict]:
    """Pure function under test — extract & expose for unit testing."""
    from Flowcut.api.routes.materials import _build_material_tree
    return _build_material_tree(rows)


def test_build_tree_groups_by_product_then_scene_role() -> None:
    rows = [
        {"product": "雪莲洗液", "scene_role": "医生"},
        {"product": "雪莲洗液", "scene_role": "医生"},
        {"product": "雪莲洗液", "scene_role": "药材"},
        {"product": "妆前乳", "scene_role": "产品展示"},
    ]
    tree = _build_tree(rows)
    assert tree == [
        {"product": "妆前乳", "total_count": 1, "children": [
            {"scene_role": "产品展示", "count": 1},
        ]},
        {"product": "雪莲洗液", "total_count": 3, "children": [
            {"scene_role": "医生", "count": 2},
            {"scene_role": "药材", "count": 1},
        ]},
    ]


def test_build_tree_null_product_becomes_通用() -> None:
    rows = [{"product": None, "scene_role": None}]
    tree = _build_tree(rows)
    assert tree[0]["product"] == "通用"
    assert tree[0]["children"] == [{"scene_role": "未分类", "count": 1}]


def test_build_tree_empty_rows_returns_empty_list() -> None:
    assert _build_tree([]) == []
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd SimpleClaw && uv run pytest tests/test_materials_tree_route.py -v`
Expected: FAIL with `ImportError: cannot import name '_build_material_tree'`

- [ ] **Step 3: 提取 `_build_material_tree` 并改写路由**

Modify `SimpleClaw/Flowcut/api/routes/materials.py`, replace the existing `/tree-summary` route (lines 278-313):

```python
def _build_material_tree(rows: list[dict]) -> list[dict]:
    """按 product → scene_role 两级聚合素材行数。"""
    summary: dict[str, dict[str, int]] = {}
    for mat in rows:
        p = mat.get("product") or "通用"
        r = mat.get("scene_role") or "未分类"
        if p not in summary:
            summary[p] = {}
        summary[p][r] = summary[p].get(r, 0) + 1

    tree: list[dict] = []
    for product_name in sorted(summary.keys()):
        roles = summary[product_name]
        total = sum(roles.values())
        children = [
            {"scene_role": role, "count": cnt}
            for role, cnt in sorted(roles.items())
        ]
        tree.append({
            "product": product_name,
            "total_count": total,
            "children": children,
        })
    return tree


@router.get("/tree")
async def material_tree(request: Request, tenant_key: str):
    """返回素材库产品→场景角色两级树（结构化字段）。"""
    container: AppContainer = request.app.state.container
    rows = await container.material_repo.list_by_tenant(
        tenant_key, status="READY", limit=99999,
    )
    return _build_material_tree(rows)
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd SimpleClaw && uv run pytest tests/test_materials_tree_route.py -v`
Expected: 3 PASSED

- [ ] **Step 5: 提交**

```bash
cd /Users/shengxingou-1/电商自动化运营/E-commerceAutomation
git add SimpleClaw/Flowcut/api/routes/materials.py SimpleClaw/tests/test_materials_tree_route.py
git commit -m "feat(flowcut): /materials/tree 返回结构化字段（替换 tree-summary）"
```

---

### Task 2: `/materials/upload` 接收 product/scene_role + OSS 路径加 product 分层

**Files:**
- Modify: `SimpleClaw/Flowcut/api/routes/materials.py:77-137` (upload_material)
- Modify: `SimpleClaw/Flowcut/api/routes/materials.py:140-165` (create_upload_token)
- Modify: `SimpleClaw/Flowcut/api/routes/materials.py:200-267` (import_douyin)
- Test: `SimpleClaw/tests/test_materials_upload_oss_path.py` (new)

- [ ] **Step 1: 写失败测试**

Create `SimpleClaw/tests/test_materials_upload_oss_path.py`:

```python
"""Tests for upload OSS key product partitioning."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_make_upload_oss_key_with_product() -> None:
    from Flowcut.api.routes.materials import _make_upload_oss_key
    key = _make_upload_oss_key("t_001", "雪莲洗液", "clip.mp4", ts=1700000000)
    assert key == "materials/t_001/雪莲洗液/uploads/1700000000_clip.mp4"


def test_make_upload_oss_key_empty_product_uses_通用() -> None:
    from Flowcut.api.routes.materials import _make_upload_oss_key
    key = _make_upload_oss_key("t_001", None, "clip.mp4", ts=1700000000)
    assert key == "materials/t_001/通用/uploads/1700000000_clip.mp4"


def test_make_upload_oss_key_empty_string_product_uses_通用() -> None:
    from Flowcut.api.routes.materials import _make_upload_oss_key
    key = _make_upload_oss_key("t_001", "", "clip.mp4", ts=1700000000)
    assert key == "materials/t_001/通用/uploads/1700000000_clip.mp4"
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd SimpleClaw && uv run pytest tests/test_materials_upload_oss_path.py -v`
Expected: FAIL with `ImportError: cannot import name '_make_upload_oss_key'`

- [ ] **Step 3: 添加 helper + 修改三个 upload 路由**

In `SimpleClaw/Flowcut/api/routes/materials.py`, add helper after `_category_from_filename` (around line 55):

```python
def _make_upload_oss_key(
    tenant_key: str, product: str | None, filename: str, *, ts: int | None = None
) -> str:
    """生成上传素材的 OSS key，按产品分层。"""
    import time as _time
    product_dir = product if product else "通用"
    timestamp = ts if ts is not None else int(_time.time())
    return f"materials/{tenant_key}/{product_dir}/uploads/{timestamp}_{filename}"
```

Replace `upload_material` (lines 77-137):

```python
@router.post("/upload")
async def upload_material(
    request: Request,
    file: UploadFile = File(...),
    tenant_key: str = Form(...),
    product: str = Form(...),
    scene_role: str | None = Form(None),
):
    """接收浏览器上传的文件，服务端直传 TOS，返回 material_id。

    OSS key 按产品分层：materials/{tenant_key}/{product}/uploads/{ts}_{filename}
    """
    container: AppContainer = request.app.state.container

    filename = file.filename or "upload"
    oss_key = _make_upload_oss_key(tenant_key, product, filename)
    category = _category_from_filename(filename)

    content = await file.read()
    file_size = len(content)

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name
            tmp.write(content)

        oss_client = build_oss_client()
        oss_client.upload(tmp_path, oss_key)
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    material = await container.material_repo.create(
        tenant_key=tenant_key,
        oss_key=oss_key,
        oss_url=oss_key,
        name=filename,
        category=category,
        duration=0.0,
        file_size=file_size,
        product=product or None,
        scene_role=scene_role or None,
    )
    material_id = material["id"]

    envelope = TaskEnvelope(
        task_type="material_process",
        payload={
            "material_id": material_id,
            "oss_key": oss_key,
            "oss_url": oss_key,
        },
        stream=FlowcutTaskStream.MATERIAL_PROCESS,
        tenant_key=tenant_key,
        scope_key=f"material:{material_id}",
    )
    task_id = await container.runtime.submit_task(envelope)

    return {"material_id": material_id, "task_id": task_id, "oss_key": oss_key, "status": "queued"}
```

Replace `create_upload_token` request model + body (lines 22-25 + 140-165):

```python
class UploadTokenRequest(BaseModel):
    tenant_key: str
    filename: str
    product: str
    scene_role: str | None = None


@router.post("/upload-token")
async def create_upload_token(body: UploadTokenRequest, request: Request):
    """返回 OSS presigned PUT URL 和预分配的 material_id。"""
    container: AppContainer = request.app.state.container

    oss_key = _make_upload_oss_key(body.tenant_key, body.product, body.filename)
    category = _category_from_filename(body.filename)

    material = await container.material_repo.create(
        tenant_key=body.tenant_key,
        oss_key=oss_key,
        oss_url=oss_key,
        name=body.filename,
        category=category,
        duration=0.0,
        file_size=0,
        product=body.product or None,
        scene_role=body.scene_role or None,
    )

    oss_client = build_oss_client()
    presigned_url = oss_client.presigned_put_url(oss_key)

    return {
        "material_id": material["id"],
        "presigned_url": presigned_url,
        "oss_key": oss_key,
    }
```

Replace `ImportDouyinRequest` + `import_douyin` OSS key generation (line 35-37 + line 224):

```python
class ImportDouyinRequest(BaseModel):
    share_url: str
    tenant_key: str
    product: str
    scene_role: str | None = None
```

In `import_douyin`, replace the OSS key generation line (around 224):

```python
        # 上传到 OSS（按产品分层）
        oss_key = _make_upload_oss_key(
            body.tenant_key, body.product, f"{aweme_id}.mp4"
        )
```

And in the `material_repo.create()` call within `import_douyin` (around line 237), add product/scene_role:

```python
    material = await container.material_repo.create(
        tenant_key=body.tenant_key,
        oss_key=oss_key,
        oss_url=oss_client.get_public_url(oss_key) or oss_key,
        name=info.title or f"douyin_{aweme_id}",
        category="video",
        duration=duration_s,
        file_size=file_size,
        product=body.product or None,
        scene_role=body.scene_role or None,
    )
```

- [ ] **Step 4: 运行单元测试通过**

Run: `cd SimpleClaw && uv run pytest tests/test_materials_upload_oss_path.py -v`
Expected: 3 PASSED

- [ ] **Step 5: 运行回归测试**

Run: `cd SimpleClaw && uv run pytest -m unit -v --no-header 2>&1 | tail -20`
Expected: No regressions.

- [ ] **Step 6: 提交**

```bash
git add SimpleClaw/Flowcut/api/routes/materials.py SimpleClaw/tests/test_materials_upload_oss_path.py
git commit -m "feat(flowcut): upload 路由接收 product/scene_role，OSS key 按产品分层"
```

---

### Task 3: 新增 `POST /materials/upload-zip`（解析预览）

**Files:**
- Create: `SimpleClaw/Flowcut/services/zip_parser.py`
- Modify: `SimpleClaw/Flowcut/api/routes/materials.py` (add new route)
- Modify: `SimpleClaw/Flowcut/api/container.py` (add zip_uploads dict)
- Test: `SimpleClaw/tests/test_zip_parser.py` (new)

- [ ] **Step 1: 写失败测试**

Create `SimpleClaw/tests/test_zip_parser.py`:

```python
"""Tests for zip upload parser."""
from __future__ import annotations

import io
import zipfile
import pytest

pytestmark = pytest.mark.unit


def _make_zip(entries: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in entries:
            z.writestr(name, content)
    return buf.getvalue()


def test_parse_zip_two_level_paths() -> None:
    from Flowcut.services.zip_parser import parse_zip_structure

    zip_bytes = _make_zip([
        ("雪莲洗液/医生/clip_01.mp4", b"fake"),
        ("雪莲洗液/医生/clip_02.mp4", b"fake"),
        ("雪莲洗液/药材/clip_03.mp4", b"fake"),
    ])
    existing = {"雪莲洗液": {"医生"}}

    preview = parse_zip_structure(zip_bytes, existing_tree=existing)

    assert preview == [
        {
            "product": "雪莲洗液",
            "scene_role": "医生",
            "files": ["clip_01.mp4", "clip_02.mp4"],
            "status": "existing",
        },
        {
            "product": "雪莲洗液",
            "scene_role": "药材",
            "files": ["clip_03.mp4"],
            "status": "new",
        },
    ]


def test_parse_zip_single_level_treated_as_product_only() -> None:
    from Flowcut.services.zip_parser import parse_zip_structure

    zip_bytes = _make_zip([("通用/clip.mp4", b"fake")])
    preview = parse_zip_structure(zip_bytes, existing_tree={"通用": set()})

    assert preview == [{
        "product": "通用",
        "scene_role": None,
        "files": ["clip.mp4"],
        "status": "existing",
    }]


def test_parse_zip_non_video_marked_ignored() -> None:
    from Flowcut.services.zip_parser import parse_zip_structure

    zip_bytes = _make_zip([
        ("雪莲洗液/医生/clip.mp4", b"fake"),
        ("readme.txt", b"hi"),
        ("a/b/c/too_deep.mp4", b"fake"),
    ])
    preview = parse_zip_structure(zip_bytes, existing_tree={})

    ignored = [p for p in preview if p["status"] == "ignored"]
    assert len(ignored) == 1
    assert set(ignored[0]["files"]) == {"readme.txt", "a/b/c/too_deep.mp4"}
```

- [ ] **Step 2: 运行测试，确认失败**

Run: `cd SimpleClaw && uv run pytest tests/test_zip_parser.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'Flowcut.services.zip_parser'`

- [ ] **Step 3: 实现 zip_parser**

Create `SimpleClaw/Flowcut/services/zip_parser.py`:

```python
"""Parse uploaded zip into product/scene_role preview structure."""
from __future__ import annotations

import io
import zipfile

_VIDEO_EXT = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv", ".wmv"}


def _is_video(name: str) -> bool:
    lower = name.lower()
    return any(lower.endswith(ext) for ext in _VIDEO_EXT)


def parse_zip_structure(
    zip_bytes: bytes,
    *,
    existing_tree: dict[str, set[str]],
) -> list[dict]:
    """解析 zip 内部目录结构，按 product/scene_role 分组返回预览。

    Args:
        zip_bytes: zip 文件原始字节
        existing_tree: 当前租户已有的 {product: {scene_role, ...}}

    Returns:
        List of {product, scene_role, files, status} 预览项。
        status: "existing" | "new" | "ignored"
    """
    grouped: dict[tuple[str | None, str | None], list[str]] = {}
    ignored: list[str] = []

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for name in z.namelist():
            if name.endswith("/"):
                continue  # skip directory entries
            parts = name.split("/")
            if not _is_video(name) or len(parts) > 3 or len(parts) < 2:
                ignored.append(name)
                continue
            if len(parts) == 2:
                product, filename = parts
                scene_role = None
            else:
                product, scene_role, filename = parts
            grouped.setdefault((product, scene_role), []).append(filename)

    preview: list[dict] = []
    for (product, scene_role), files in sorted(grouped.items()):
        if product in existing_tree:
            if scene_role is None or scene_role in existing_tree[product]:
                status = "existing"
            else:
                status = "new"
        else:
            status = "new"
        preview.append({
            "product": product,
            "scene_role": scene_role,
            "files": sorted(files),
            "status": status,
        })

    if ignored:
        preview.append({
            "product": None,
            "scene_role": None,
            "files": sorted(ignored),
            "status": "ignored",
        })

    return preview
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `cd SimpleClaw && uv run pytest tests/test_zip_parser.py -v`
Expected: 3 PASSED

- [ ] **Step 5: 在 AppContainer 添加 zip_uploads 缓存**

Modify `SimpleClaw/Flowcut/api/container.py`. After the dataclass field declarations (find the end of `@dataclass class AppContainer:` block, before any methods), add:

```python
    # zip 上传临时缓存 {upload_id: {"tenant_key": str, "extracted_dir": str, "preview": list, "created_at": float}}
    zip_uploads: dict[str, dict] = field(default_factory=dict)
```

(Note: `field` is already imported from `dataclasses` at line 5.)

- [ ] **Step 6: 添加 `/materials/upload-zip` 路由**

In `SimpleClaw/Flowcut/api/routes/materials.py`, add at the end (after the delete route):

```python
import shutil
import time as _time
import uuid
import zipfile as _zipfile


_ZIP_UPLOAD_TTL_S = 30 * 60  # 30 分钟


def _cleanup_expired_zip_uploads(container: AppContainer) -> None:
    """清理过期的 zip 上传临时目录。"""
    now = _time.time()
    expired = [
        uid for uid, data in container.zip_uploads.items()
        if now - data["created_at"] > _ZIP_UPLOAD_TTL_S
    ]
    for uid in expired:
        data = container.zip_uploads.pop(uid)
        shutil.rmtree(data["extracted_dir"], ignore_errors=True)


@router.post("/upload-zip")
async def upload_zip(
    request: Request,
    file: UploadFile = File(...),
    tenant_key: str = Form(...),
):
    """上传 zip 文件，解压并解析目录结构，返回预览。"""
    from Flowcut.services.zip_parser import parse_zip_structure

    container: AppContainer = request.app.state.container
    _cleanup_expired_zip_uploads(container)

    content = await file.read()

    # 解压到临时目录
    upload_id = uuid.uuid4().hex
    extracted_dir = tempfile.mkdtemp(prefix=f"flowcut_zip_{upload_id}_")
    try:
        with _zipfile.ZipFile(io.BytesIO(content)) as z:
            z.extractall(extracted_dir)
    except _zipfile.BadZipFile:
        shutil.rmtree(extracted_dir, ignore_errors=True)
        raise HTTPException(400, "无效的 zip 文件")

    # 读取已有 tree 用于匹配
    rows = await container.material_repo.list_by_tenant(
        tenant_key, status="READY", limit=99999,
    )
    existing_tree: dict[str, set[str]] = {}
    for mat in rows:
        p = mat.get("product")
        r = mat.get("scene_role")
        if not p:
            continue
        if p not in existing_tree:
            existing_tree[p] = set()
        if r:
            existing_tree[p].add(r)

    preview = parse_zip_structure(content, existing_tree=existing_tree)

    container.zip_uploads[upload_id] = {
        "tenant_key": tenant_key,
        "extracted_dir": extracted_dir,
        "preview": preview,
        "created_at": _time.time(),
    }

    return {"upload_id": upload_id, "preview": preview}
```

Also add `import io` near the top of the file if not present (check line 1-15 — `io` is not imported; the existing imports are `os`, `tempfile`, `time`). Add `import io` after `import os`.

- [ ] **Step 7: 手工验证（启动 server + curl）**

```bash
cd SimpleClaw
uv run python -m uvicorn Flowcut.api.server:app --reload --port 8001 &
sleep 3

# 制造一个测试 zip
python -c "
import zipfile
with zipfile.ZipFile('/tmp/test.zip', 'w') as z:
    z.writestr('雪莲洗液/医生/clip.mp4', b'fake')
    z.writestr('readme.txt', b'hi')
"

curl -X POST http://localhost:8001/materials/upload-zip \
  -F "tenant_key=t_test" \
  -F "file=@/tmp/test.zip"
```

Expected: JSON with `upload_id` and `preview` containing both 雪莲洗液/医生 entry and ignored readme.txt.

Stop server: `kill %1`

- [ ] **Step 8: 提交**

```bash
git add SimpleClaw/Flowcut/api/routes/materials.py SimpleClaw/Flowcut/api/container.py SimpleClaw/Flowcut/services/zip_parser.py SimpleClaw/tests/test_zip_parser.py
git commit -m "feat(flowcut): /materials/upload-zip 解析 zip 目录结构返回预览"
```

---

### Task 4: 新增 `POST /materials/upload-zip/confirm`

**Files:**
- Modify: `SimpleClaw/Flowcut/api/routes/materials.py` (add confirm route)

- [ ] **Step 1: 添加 confirm 路由**

In `SimpleClaw/Flowcut/api/routes/materials.py`, add after the `upload_zip` route:

```python
class ConfirmZipRequest(BaseModel):
    upload_id: str
    tenant_key: str


@router.post("/upload-zip/confirm")
async def confirm_upload_zip(body: ConfirmZipRequest, request: Request):
    """确认 zip 导入：批量上传 OSS、创建 material 记录、入队处理任务。"""
    container: AppContainer = request.app.state.container

    data = container.zip_uploads.get(body.upload_id)
    if data is None:
        raise HTTPException(404, "upload_id 不存在或已过期")
    if data["tenant_key"] != body.tenant_key:
        raise HTTPException(403, "租户不匹配")

    extracted_dir = data["extracted_dir"]
    preview = data["preview"]

    material_ids: list[int] = []
    oss_client = build_oss_client()

    try:
        for item in preview:
            if item["status"] == "ignored":
                continue
            product = item["product"]
            scene_role = item.get("scene_role")
            for filename in item["files"]:
                # 还原源文件路径（preview 里 filename 只是末段）
                if scene_role:
                    src = os.path.join(extracted_dir, product, scene_role, filename)
                else:
                    src = os.path.join(extracted_dir, product, filename)
                if not os.path.isfile(src):
                    continue

                oss_key = _make_upload_oss_key(body.tenant_key, product, filename)
                oss_client.upload(src, oss_key)
                file_size = os.path.getsize(src)
                category = _category_from_filename(filename)

                material = await container.material_repo.create(
                    tenant_key=body.tenant_key,
                    oss_key=oss_key,
                    oss_url=oss_key,
                    name=filename,
                    category=category,
                    duration=0.0,
                    file_size=file_size,
                    product=product,
                    scene_role=scene_role,
                )
                material_id = material["id"]
                material_ids.append(material_id)

                envelope = TaskEnvelope(
                    task_type="material_process",
                    payload={
                        "material_id": material_id,
                        "oss_key": oss_key,
                        "oss_url": oss_key,
                    },
                    stream=FlowcutTaskStream.MATERIAL_PROCESS,
                    tenant_key=body.tenant_key,
                    scope_key=f"material:{material_id}",
                )
                await container.runtime.submit_task(envelope)
    finally:
        shutil.rmtree(extracted_dir, ignore_errors=True)
        container.zip_uploads.pop(body.upload_id, None)

    return {"material_ids": material_ids}
```

- [ ] **Step 2: 手工验证完整 zip 流程**

```bash
cd SimpleClaw
uv run python -m uvicorn Flowcut.api.server:app --reload --port 8001 &
sleep 3

# 1) 上传 zip 拿 upload_id
RESP=$(curl -s -X POST http://localhost:8001/materials/upload-zip \
  -F "tenant_key=t_test" \
  -F "file=@/tmp/test.zip")
echo "$RESP"
UPLOAD_ID=$(echo "$RESP" | python -c "import sys, json; print(json.load(sys.stdin)['upload_id'])")

# 2) 确认导入
curl -X POST http://localhost:8001/materials/upload-zip/confirm \
  -H "Content-Type: application/json" \
  -d "{\"upload_id\": \"$UPLOAD_ID\", \"tenant_key\": \"t_test\"}"
```

Expected: `{"material_ids": [<some_int>]}`. Then verify with `GET /materials/tree?tenant_key=t_test`.

Stop server: `kill %1`

- [ ] **Step 3: 提交**

```bash
git add SimpleClaw/Flowcut/api/routes/materials.py
git commit -m "feat(flowcut): /materials/upload-zip/confirm 批量写入素材并入队"
```

---

## Phase B — 前端类型 & API 层（3 tasks）

### Task 5: 更新前端类型定义

**Files:**
- Modify: `flowcut_frontend/src/types/index.ts`

- [ ] **Step 1: 添加新类型，扩展 Material**

Modify `flowcut_frontend/src/types/index.ts`. After existing `Material` interface (lines 13-29), add fields and append new types at the end:

Update the `Material` interface to include:

```typescript
export interface Material {
  id: string
  ossKey: string
  ossUrl: string
  thumbnailUrl?: string
  previewUrl?: string
  name: string
  transcript?: string
  sceneData?: VideoScene[]
  category: MaterialCategory
  product?: string
  sceneRole?: string
  duration: number
  fileSize: number
  status: MaterialStatus
  usageCount: number
  createdAt: string
  type: MaterialType
}
```

Append at end of file:

```typescript
// 素材库产品分层树（与后端 GET /materials/tree 一致）
export interface SceneRoleNode {
  sceneRole: string
  count: number
}

export interface ProductNode {
  product: string
  totalCount: number
  children: SceneRoleNode[]
}

// zip 上传预览项（与后端 POST /materials/upload-zip 响应一致）
export type ZipPreviewStatus = 'existing' | 'new' | 'ignored'

export interface ZipPreviewItem {
  product: string | null
  sceneRole: string | null
  files: string[]
  status: ZipPreviewStatus
}

export interface ZipUploadResponse {
  uploadId: string
  preview: ZipPreviewItem[]
}
```

- [ ] **Step 2: 类型检查通过**

```bash
cd flowcut_frontend && npm run build 2>&1 | tail -10
```

Expected: build 失败但报错只与 `Material` 已有用法相关（接下来会改）；新类型本身无 error。

- [ ] **Step 3: 提交**

```bash
git add flowcut_frontend/src/types/index.ts
git commit -m "feat(frontend): 添加 ProductNode/SceneRoleNode/ZipPreviewItem 类型并扩展 Material"
```

---

### Task 6: 修改 `api/materials.ts` 增加 product/scene_role 支持 + zip 接口

**Files:**
- Modify: `flowcut_frontend/src/api/materials.ts`

- [ ] **Step 1: 改造 fromBackend、listMaterials、uploadMaterial，新增 zip 接口**

Replace `flowcut_frontend/src/api/materials.ts` (full content):

```typescript
import { apiClient } from './client'
import type {
  Material,
  VideoScene,
  ZipPreviewItem,
  ZipUploadResponse,
} from '../types'

function parseSceneData(raw: unknown): VideoScene[] | undefined {
  if (!raw) return undefined
  const arr = typeof raw === 'string' ? JSON.parse(raw) : raw
  if (!Array.isArray(arr)) return undefined
  return (arr as Record<string, unknown>[]).map((s) => ({
    startTime: s.start_time as number,
    endTime: s.end_time as number,
    content: s.content as string,
    category: (s.category as VideoScene['category']) ?? '产品展示',
  }))
}

function fromBackend(raw: Record<string, unknown>): Material {
  const fileType = raw.category as string
  const type: Material['type'] =
    fileType === 'image' ? 'image' : fileType === 'audio' ? 'audio' : 'video'
  return {
    id: String(raw.id),
    ossKey: raw.oss_key as string,
    ossUrl: raw.oss_url as string,
    thumbnailUrl: (raw.thumbnail_url as string | null) ?? undefined,
    previewUrl: (raw.preview_url as string | null) ?? undefined,
    name: raw.name as string,
    transcript: (raw.transcript as string | null) ?? undefined,
    sceneData: parseSceneData(raw.scene_data_json),
    category: (raw.category as Material['category']) || '产品展示',
    product: (raw.product as string | null) ?? undefined,
    sceneRole: (raw.scene_role as string | null) ?? undefined,
    duration: (raw.duration as number) ?? 0,
    fileSize: (raw.file_size as number) ?? 0,
    status: raw.status as Material['status'],
    usageCount: (raw.usage_count as number) ?? 0,
    createdAt: raw.created_at as string,
    type,
  }
}

export async function listMaterials(
  tenantKey: string,
  filters?: { product?: string; sceneRole?: string },
): Promise<Material[]> {
  const { data } = await apiClient.get<Record<string, unknown>[]>('/materials', {
    params: {
      tenant_key: tenantKey,
      product: filters?.product,
      scene_role: filters?.sceneRole,
    },
  })
  return data.map(fromBackend)
}

export async function getMaterial(materialId: number): Promise<Material> {
  const { data } = await apiClient.get<Record<string, unknown>>(`/materials/${materialId}`)
  return fromBackend(data)
}

export async function uploadMaterial(
  tenantKey: string,
  file: File,
  product: string,
  sceneRole?: string,
  onProgress?: (percent: number) => void,
): Promise<{ material_id: number; oss_key: string }> {
  const form = new FormData()
  form.append('tenant_key', tenantKey)
  form.append('file', file)
  form.append('product', product)
  if (sceneRole) form.append('scene_role', sceneRole)

  const { data } = await apiClient.post<{ material_id: number; oss_key: string }>(
    '/materials/upload',
    form,
    {
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: (e) => {
        if (onProgress && e.total) onProgress(Math.round((e.loaded / e.total) * 100))
      },
    },
  )
  return data
}

export async function processMaterial(materialId: number) {
  const { data } = await apiClient.post<{
    material_id: number
    task_id: string
    status: string
  }>(`/materials/${materialId}/process`)
  return data
}

export async function triggerDecompose(materialId: number) {
  const { data } = await apiClient.post<{
    material_id: number
    task_id: string
    status: string
  }>(`/materials/${materialId}/decompose`)
  return data
}

export async function deleteMaterial(materialId: string) {
  await apiClient.delete(`/materials/${materialId}`)
}

export async function pollMaterial(
  materialId: number,
  condition: (m: Material) => boolean,
  intervalMs = 2500,
  timeoutMs = 180_000,
): Promise<Material> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const m = await getMaterial(materialId)
    if (condition(m)) return m
    if (m.status === 'FAILED') throw new Error(`素材处理失败（id=${materialId}）`)
    await new Promise((r) => setTimeout(r, intervalMs))
  }
  throw new Error('等待超时（3 分钟）')
}

// ── ZIP 批量上传 ──────────────────────────────────────────────

function fromBackendZipItem(raw: Record<string, unknown>): ZipPreviewItem {
  return {
    product: (raw.product as string | null) ?? null,
    sceneRole: (raw.scene_role as string | null) ?? null,
    files: (raw.files as string[]) ?? [],
    status: raw.status as ZipPreviewItem['status'],
  }
}

export async function uploadZip(
  tenantKey: string,
  file: File,
): Promise<ZipUploadResponse> {
  const form = new FormData()
  form.append('tenant_key', tenantKey)
  form.append('file', file)
  const { data } = await apiClient.post<{
    upload_id: string
    preview: Record<string, unknown>[]
  }>('/materials/upload-zip', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
  return {
    uploadId: data.upload_id,
    preview: data.preview.map(fromBackendZipItem),
  }
}

export async function confirmZip(
  uploadId: string,
  tenantKey: string,
): Promise<{ materialIds: number[] }> {
  const { data } = await apiClient.post<{ material_ids: number[] }>(
    '/materials/upload-zip/confirm',
    { upload_id: uploadId, tenant_key: tenantKey },
  )
  return { materialIds: data.material_ids }
}
```

- [ ] **Step 2: 类型检查（API 层应该独立通过）**

```bash
cd flowcut_frontend && npx tsc --noEmit src/api/materials.ts 2>&1 | head -20
```

Expected: no error in this file (errors in dependent files OK for now).

- [ ] **Step 3: 提交**

```bash
git add flowcut_frontend/src/api/materials.ts
git commit -m "feat(frontend): materials API 支持 product/scene_role + zip 上传"
```

---

### Task 7: 创建 `api/products.ts`

**Files:**
- Create: `flowcut_frontend/src/api/products.ts`

- [ ] **Step 1: 实现 getProductTree + getProducts**

Create `flowcut_frontend/src/api/products.ts`:

```typescript
import { apiClient } from './client'
import type { ProductNode, SceneRoleNode } from '../types'

function fromBackendSceneRole(raw: Record<string, unknown>): SceneRoleNode {
  return {
    sceneRole: raw.scene_role as string,
    count: raw.count as number,
  }
}

function fromBackendProduct(raw: Record<string, unknown>): ProductNode {
  return {
    product: raw.product as string,
    totalCount: raw.total_count as number,
    children: ((raw.children as Record<string, unknown>[]) ?? []).map(
      fromBackendSceneRole,
    ),
  }
}

export async function getProductTree(tenantKey: string): Promise<ProductNode[]> {
  const { data } = await apiClient.get<Record<string, unknown>[]>(
    '/materials/tree',
    { params: { tenant_key: tenantKey } },
  )
  return data.map(fromBackendProduct)
}

export async function getProducts(tenantKey: string): Promise<string[]> {
  const { data } = await apiClient.get<{ products: string[] }>(
    '/materials/products',
    { params: { tenant_key: tenantKey } },
  )
  return data.products
}
```

- [ ] **Step 2: 类型检查**

```bash
cd flowcut_frontend && npx tsc --noEmit 2>&1 | grep "products.ts" || echo "no errors in products.ts"
```

Expected: no errors in products.ts.

- [ ] **Step 3: 提交**

```bash
git add flowcut_frontend/src/api/products.ts
git commit -m "feat(frontend): 新增 products API（getProductTree + getProducts）"
```

---

## Phase C — 前端 Store（2 tasks）

### Task 8: 新建 `productTreeStore.ts`

**Files:**
- Create: `flowcut_frontend/src/stores/productTreeStore.ts`

- [ ] **Step 1: 实现 productTreeStore**

Create `flowcut_frontend/src/stores/productTreeStore.ts`:

```typescript
import { create } from 'zustand'
import type { ProductNode } from '../types'
import { getProductTree } from '../api/products'

interface ProductTreeState {
  treeNodes: ProductNode[]
  activeProduct: string | null
  activeSceneRole: string | null
  isLoading: boolean
  error: string | null

  fetchTree: (tenantKey: string) => Promise<void>
  selectNode: (product: string | null, sceneRole: string | null) => void
  refreshTree: (tenantKey: string) => Promise<void>
}

export const useProductTreeStore = create<ProductTreeState>((set) => ({
  treeNodes: [],
  activeProduct: null,
  activeSceneRole: null,
  isLoading: false,
  error: null,

  fetchTree: async (tenantKey) => {
    set({ isLoading: true, error: null })
    try {
      const treeNodes = await getProductTree(tenantKey)
      set({ treeNodes, isLoading: false })
    } catch (err) {
      const msg = err instanceof Error ? err.message : '加载产品树失败'
      set({ error: msg, isLoading: false })
    }
  },

  selectNode: (product, sceneRole) => {
    set({ activeProduct: product, activeSceneRole: sceneRole })
  },

  refreshTree: async (tenantKey) => {
    try {
      const treeNodes = await getProductTree(tenantKey)
      set({ treeNodes })
    } catch (err) {
      const msg = err instanceof Error ? err.message : '刷新产品树失败'
      set({ error: msg })
    }
  },
}))
```

- [ ] **Step 2: 类型检查**

```bash
cd flowcut_frontend && npx tsc --noEmit 2>&1 | grep "productTreeStore" || echo "no errors"
```

Expected: no errors in productTreeStore.

- [ ] **Step 3: 提交**

```bash
git add flowcut_frontend/src/stores/productTreeStore.ts
git commit -m "feat(frontend): 新增 productTreeStore（产品树状态管理）"
```

---

### Task 9: 改造 `materialStore.ts`

**Files:**
- Modify: `flowcut_frontend/src/stores/materialStore.ts`

- [ ] **Step 1: 移除 activeCategory，接入 product/sceneRole 过滤**

Replace `flowcut_frontend/src/stores/materialStore.ts` (full):

```typescript
import { create } from 'zustand'
import type { Material, MaterialType } from '../types'
import { listMaterials } from '../api/materials'

interface MaterialState {
  materials: Material[]
  isLoading: boolean
  error: string | null
  activeSubTab: MaterialType

  setSubTab: (tab: MaterialType) => void
  filteredMaterials: () => Material[]
  audioMaterials: () => Material[]
  fetchMaterials: (
    tenantKey: string,
    filters?: { product?: string; sceneRole?: string },
  ) => Promise<void>
  addMaterial: (material: Material) => void
  addMaterials: (materials: Material[]) => void
}

export const useMaterialStore = create<MaterialState>((set, get) => ({
  materials: [],
  isLoading: false,
  error: null,
  activeSubTab: 'video',

  setSubTab: (tab) => set({ activeSubTab: tab }),

  filteredMaterials: () => {
    const { materials, activeSubTab } = get()
    return materials.filter((m) => m.type === activeSubTab)
  },

  audioMaterials: () => get().materials.filter((m) => m.type === 'audio'),

  fetchMaterials: async (tenantKey, filters) => {
    set({ isLoading: true, error: null })
    try {
      const materials = await listMaterials(tenantKey, filters)
      set({ materials, isLoading: false })
    } catch (err) {
      const msg = err instanceof Error ? err.message : '加载失败'
      set({ error: msg, isLoading: false })
    }
  },

  addMaterial: (material) =>
    set((s) => ({ materials: [material, ...s.materials] })),

  addMaterials: (materials) =>
    set((s) => ({ materials: [...materials, ...s.materials] })),
}))
```

- [ ] **Step 2: 类型检查**

```bash
cd flowcut_frontend && npx tsc --noEmit 2>&1 | grep "materialStore" || echo "no errors"
```

Expected: no errors in materialStore.

- [ ] **Step 3: 提交**

```bash
git add flowcut_frontend/src/stores/materialStore.ts
git commit -m "refactor(frontend): materialStore 移除 activeCategory，新增 product/sceneRole 过滤"
```

---

## Phase D — 前端新组件（3 tasks）

### Task 10: 创建 `MaterialSidebar.tsx`

**Files:**
- Create: `flowcut_frontend/src/components/material/MaterialSidebar.tsx`
- Create: `flowcut_frontend/src/components/material/MaterialSidebar.module.css`

- [ ] **Step 1: 实现侧边栏组件**

Create `flowcut_frontend/src/components/material/MaterialSidebar.module.css`:

```css
.sidebar {
  width: 220px;
  flex-shrink: 0;
  border-right: 1px solid #e6e6e6;
  background: #fafafa;
  padding: 12px 8px;
  overflow-y: auto;
}

.header {
  font-size: 11px;
  color: #999;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  padding: 4px 8px 10px;
}

.empty {
  padding: 24px 8px;
  color: #999;
  font-size: 12px;
  text-align: center;
}
```

Create `flowcut_frontend/src/components/material/MaterialSidebar.tsx`:

```tsx
import { useMemo } from 'react'
import { Tree } from 'antd'
import type { TreeDataNode } from 'antd'
import { useProductTreeStore } from '../../stores/productTreeStore'
import { useMaterialStore } from '../../stores/materialStore'
import type { ProductNode } from '../../types'
import styles from './MaterialSidebar.module.css'

const TENANT_KEY = 'flowcut'

function buildAntdTree(nodes: ProductNode[]): TreeDataNode[] {
  return nodes.map((n) => ({
    key: n.product,
    title: `${n.product} (${n.totalCount})`,
    children: n.children.map((c) => ({
      key: `${n.product}|${c.sceneRole}`,
      title: `${c.sceneRole} (${c.count})`,
      isLeaf: true,
    })),
  }))
}

export default function MaterialSidebar() {
  const { treeNodes, activeProduct, activeSceneRole, selectNode } =
    useProductTreeStore()
  const fetchMaterials = useMaterialStore((s) => s.fetchMaterials)

  const treeData = useMemo(() => buildAntdTree(treeNodes), [treeNodes])

  const selectedKeys = useMemo(() => {
    if (activeProduct && activeSceneRole) {
      return [`${activeProduct}|${activeSceneRole}`]
    }
    if (activeProduct) return [activeProduct]
    return []
  }, [activeProduct, activeSceneRole])

  const handleSelect = (keys: React.Key[]) => {
    if (keys.length === 0) {
      selectNode(null, null)
      fetchMaterials(TENANT_KEY)
      return
    }
    const key = String(keys[0])
    if (key.includes('|')) {
      const [product, sceneRole] = key.split('|')
      selectNode(product, sceneRole)
      fetchMaterials(TENANT_KEY, { product, sceneRole })
    } else {
      selectNode(key, null)
      fetchMaterials(TENANT_KEY, { product: key })
    }
  }

  return (
    <aside className={styles.sidebar}>
      <div className={styles.header}>产品</div>
      {treeData.length === 0 ? (
        <div className={styles.empty}>暂无产品，上传素材后自动出现</div>
      ) : (
        <Tree
          treeData={treeData}
          selectedKeys={selectedKeys}
          onSelect={handleSelect}
          defaultExpandAll
          blockNode
        />
      )}
    </aside>
  )
}
```

- [ ] **Step 2: 类型检查**

```bash
cd flowcut_frontend && npx tsc --noEmit 2>&1 | grep "MaterialSidebar" || echo "no errors"
```

Expected: no errors in MaterialSidebar.

- [ ] **Step 3: 提交**

```bash
git add flowcut_frontend/src/components/material/MaterialSidebar.tsx flowcut_frontend/src/components/material/MaterialSidebar.module.css
git commit -m "feat(frontend): 新增 MaterialSidebar 产品树侧边栏组件"
```

---

### Task 11: 创建 `ZipPreview.tsx`

**Files:**
- Create: `flowcut_frontend/src/components/material/ZipPreview.tsx`
- Create: `flowcut_frontend/src/components/material/ZipPreview.module.css`

- [ ] **Step 1: 实现 ZIP 预览列表**

Create `flowcut_frontend/src/components/material/ZipPreview.module.css`:

```css
.container {
  border: 1px solid #e6e6e6;
  border-radius: 6px;
  max-height: 240px;
  overflow-y: auto;
}

.group {
  border-top: 1px solid #f0f0f0;
}
.group:first-child {
  border-top: none;
}

.groupHeader {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  font-size: 12px;
  font-weight: 500;
}
.statusExisting { background: #e8f0fe; color: #2563eb; }
.statusNew { background: #fffbe6; color: #d46b08; }
.statusIgnored { background: #fafafa; color: #999; }

.badge {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 3px;
}
.badgeExisting { background: #dcffe4; color: #22863a; }
.badgeNew { background: #fff3cd; color: #d46b08; }
.badgeIgnored { background: #f0f0f0; color: #999; }

.files {
  padding: 4px 10px 8px 22px;
  font-size: 11px;
  color: #555;
  line-height: 1.8;
}

.summary {
  background: #f0f9ff;
  border: 1px solid #bae0ff;
  border-radius: 4px;
  padding: 6px 10px;
  font-size: 11px;
  color: #0958d9;
  margin-top: 8px;
}
```

Create `flowcut_frontend/src/components/material/ZipPreview.tsx`:

```tsx
import type { ZipPreviewItem } from '../../types'
import styles from './ZipPreview.module.css'

interface ZipPreviewProps {
  preview: ZipPreviewItem[]
}

function pathLabel(item: ZipPreviewItem): string {
  if (item.status === 'ignored') return '已忽略文件'
  if (item.sceneRole) return `${item.product}/${item.sceneRole}/`
  return `${item.product}/`
}

function statusBadge(status: ZipPreviewItem['status']): { label: string; cls: string } {
  if (status === 'existing') return { label: '已有', cls: styles.badgeExisting }
  if (status === 'new') return { label: '新建', cls: styles.badgeNew }
  return { label: '已忽略', cls: styles.badgeIgnored }
}

function groupCls(status: ZipPreviewItem['status']): string {
  if (status === 'existing') return styles.statusExisting
  if (status === 'new') return styles.statusNew
  return styles.statusIgnored
}

export default function ZipPreview({ preview }: ZipPreviewProps) {
  const totalFiles = preview
    .filter((p) => p.status !== 'ignored')
    .reduce((acc, p) => acc + p.files.length, 0)
  const newNodes = preview.filter((p) => p.status === 'new').length
  const existingNodes = preview.filter((p) => p.status === 'existing').length

  return (
    <>
      <div className={styles.container}>
        {preview.map((item, idx) => {
          const badge = statusBadge(item.status)
          return (
            <div key={idx} className={styles.group}>
              <div className={`${styles.groupHeader} ${groupCls(item.status)}`}>
                <span>{pathLabel(item)}</span>
                <span className={`${styles.badge} ${badge.cls}`}>{badge.label}</span>
              </div>
              <div className={styles.files}>{item.files.join(' · ')}</div>
            </div>
          )
        })}
      </div>
      <div className={styles.summary}>
        共 {totalFiles} 个文件，{existingNodes} 个已有节点，{newNodes} 个新建节点
      </div>
    </>
  )
}
```

- [ ] **Step 2: 类型检查**

```bash
cd flowcut_frontend && npx tsc --noEmit 2>&1 | grep "ZipPreview" || echo "no errors"
```

Expected: no errors.

- [ ] **Step 3: 提交**

```bash
git add flowcut_frontend/src/components/material/ZipPreview.tsx flowcut_frontend/src/components/material/ZipPreview.module.css
git commit -m "feat(frontend): 新增 ZipPreview 组件"
```

---

### Task 12: 创建 `UploadModal.tsx`

**Files:**
- Create: `flowcut_frontend/src/components/material/UploadModal.tsx`

- [ ] **Step 1: 实现上传弹窗**

Create `flowcut_frontend/src/components/material/UploadModal.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { Modal, Tabs, AutoComplete, Select, Upload, message } from 'antd'
import { InboxOutlined } from '@ant-design/icons'
import type { UploadFile } from 'antd'
import { useProductTreeStore } from '../../stores/productTreeStore'
import { getProducts } from '../../api/products'
import {
  uploadMaterial,
  processMaterial,
  uploadZip,
  confirmZip,
} from '../../api/materials'
import type { ZipPreviewItem } from '../../types'
import ZipPreview from './ZipPreview'

const TENANT_KEY = 'flowcut'

const PRESET_SCENE_ROLES = ['医生', '药材', '冲洗', '产品展示', '痛点', '美好']

interface UploadModalProps {
  open: boolean
  onClose: () => void
  onSuccess: () => void
}

export default function UploadModal({ open, onClose, onSuccess }: UploadModalProps) {
  const { activeProduct, activeSceneRole } = useProductTreeStore()
  const refreshTree = useProductTreeStore((s) => s.refreshTree)

  const [tab, setTab] = useState<'single' | 'zip'>('single')
  const [productOptions, setProductOptions] = useState<{ value: string }[]>([])
  const [product, setProduct] = useState<string>('')
  const [sceneRole, setSceneRole] = useState<string | undefined>(undefined)
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)

  const [zipFile, setZipFile] = useState<File | null>(null)
  const [uploadId, setUploadId] = useState<string | null>(null)
  const [zipPreview, setZipPreview] = useState<ZipPreviewItem[] | null>(null)

  useEffect(() => {
    if (!open) return
    getProducts(TENANT_KEY).then((products) => {
      setProductOptions(products.map((p) => ({ value: p })))
    })
    setProduct(activeProduct ?? '')
    setSceneRole(activeSceneRole ?? undefined)
    setFile(null)
    setZipFile(null)
    setUploadId(null)
    setZipPreview(null)
    setTab('single')
  }, [open, activeProduct, activeSceneRole])

  const handleSingleUpload = async () => {
    if (!file) {
      message.warning('请选择文件')
      return
    }
    if (!product.trim()) {
      message.warning('请填写产品')
      return
    }
    setBusy(true)
    try {
      const { material_id } = await uploadMaterial(
        TENANT_KEY,
        file,
        product.trim(),
        sceneRole,
      )
      await processMaterial(material_id)
      message.success('上传成功，正在处理…')
      await refreshTree(TENANT_KEY)
      onSuccess()
      onClose()
    } catch (err) {
      const msg = err instanceof Error ? err.message : '上传失败'
      message.error(msg)
    } finally {
      setBusy(false)
    }
  }

  const handleZipParse = async (selected: File) => {
    setZipFile(selected)
    setBusy(true)
    try {
      const resp = await uploadZip(TENANT_KEY, selected)
      setUploadId(resp.uploadId)
      setZipPreview(resp.preview)
    } catch (err) {
      const msg = err instanceof Error ? err.message : '解析失败'
      message.error(msg)
    } finally {
      setBusy(false)
    }
  }

  const handleZipConfirm = async () => {
    if (!uploadId) return
    setBusy(true)
    try {
      const { materialIds } = await confirmZip(uploadId, TENANT_KEY)
      message.success(`已导入 ${materialIds.length} 个素材`)
      await refreshTree(TENANT_KEY)
      onSuccess()
      onClose()
    } catch (err) {
      const msg = err instanceof Error ? err.message : '导入失败'
      message.error(msg)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      title="上传素材"
      open={open}
      onCancel={onClose}
      confirmLoading={busy}
      onOk={tab === 'single' ? handleSingleUpload : handleZipConfirm}
      okText={tab === 'single' ? '开始上传' : '确认导入'}
      okButtonProps={{ disabled: tab === 'zip' && !zipPreview }}
      destroyOnClose
    >
      <Tabs
        activeKey={tab}
        onChange={(k) => setTab(k as 'single' | 'zip')}
        items={[
          {
            key: 'single',
            label: '单文件',
            children: (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                <Upload.Dragger
                  multiple={false}
                  beforeUpload={(f) => {
                    setFile(f)
                    return false
                  }}
                  fileList={file ? ([{ uid: '-1', name: file.name, status: 'done' } as UploadFile]) : []}
                  onRemove={() => setFile(null)}
                >
                  <p style={{ margin: 0 }}>
                    <InboxOutlined style={{ fontSize: 28, color: '#2563eb' }} />
                  </p>
                  <p style={{ margin: '4px 0', fontSize: 13 }}>拖拽或点击选择视频文件</p>
                </Upload.Dragger>
                <div>
                  <div style={{ fontSize: 12, marginBottom: 4 }}>
                    产品 <span style={{ color: '#e53e3e' }}>*</span>
                  </div>
                  <AutoComplete
                    style={{ width: '100%' }}
                    options={productOptions}
                    value={product}
                    onChange={(v) => setProduct(v)}
                    placeholder="从已有产品选择或输入新产品名"
                    filterOption={(input, option) =>
                      (option?.value as string).toLowerCase().includes(input.toLowerCase())
                    }
                  />
                </div>
                <div>
                  <div style={{ fontSize: 12, marginBottom: 4 }}>
                    场景角色 <span style={{ color: '#999', fontSize: 11 }}>（可选）</span>
                  </div>
                  <Select
                    style={{ width: '100%' }}
                    value={sceneRole}
                    onChange={(v) => setSceneRole(v)}
                    options={PRESET_SCENE_ROLES.map((r) => ({ value: r, label: r }))}
                    allowClear
                    placeholder="留空表示归入该产品根节点"
                  />
                </div>
              </div>
            ),
          },
          {
            key: 'zip',
            label: 'ZIP 批量',
            children: (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                <Upload.Dragger
                  multiple={false}
                  accept=".zip"
                  beforeUpload={(f) => {
                    handleZipParse(f)
                    return false
                  }}
                  fileList={zipFile ? ([{ uid: '-1', name: zipFile.name, status: 'done' } as UploadFile]) : []}
                  onRemove={() => {
                    setZipFile(null)
                    setUploadId(null)
                    setZipPreview(null)
                  }}
                >
                  <p style={{ margin: 0 }}>
                    <InboxOutlined style={{ fontSize: 28, color: '#2563eb' }} />
                  </p>
                  <p style={{ margin: '4px 0', fontSize: 13 }}>拖拽或点击选择 .zip 文件</p>
                  <p style={{ margin: 0, fontSize: 11, color: '#999' }}>
                    内部目录：{'{产品}/{场景角色}/{文件}'}
                  </p>
                </Upload.Dragger>
                {zipPreview && <ZipPreview preview={zipPreview} />}
              </div>
            ),
          },
        ]}
      />
    </Modal>
  )
}
```

- [ ] **Step 2: 类型检查**

```bash
cd flowcut_frontend && npx tsc --noEmit 2>&1 | grep "UploadModal" || echo "no errors"
```

Expected: no errors.

- [ ] **Step 3: 提交**

```bash
git add flowcut_frontend/src/components/material/UploadModal.tsx
git commit -m "feat(frontend): 新增 UploadModal（单文件 + ZIP 批量上传）"
```

---

## Phase E — 前端集成（2 tasks）

### Task 13: 改造 `MaterialTab.tsx` 集成侧边栏

**Files:**
- Modify: `flowcut_frontend/src/components/material/MaterialTab.tsx`
- Modify: `flowcut_frontend/src/components/material/MaterialTab.module.css`

- [ ] **Step 1: 改 CSS 加左右布局**

Modify `flowcut_frontend/src/components/material/MaterialTab.module.css`. First read the existing file:

```bash
cat flowcut_frontend/src/components/material/MaterialTab.module.css
```

Then add (or merge with existing) — append at end:

```css
.layout {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.body {
  display: flex;
  flex: 1;
  min-height: 0;
}

.content {
  flex: 1;
  min-width: 0;
  overflow: auto;
}
```

- [ ] **Step 2: 改 MaterialTab.tsx 接入 Sidebar 并 fetchTree**

Replace `flowcut_frontend/src/components/material/MaterialTab.tsx`:

```tsx
import { useEffect } from 'react'
import { useMaterialStore } from '../../stores/materialStore'
import { useProductTreeStore } from '../../stores/productTreeStore'
import type { MaterialType } from '../../types'
import VideoLibrary from './VideoLibrary'
import ImageLibrary from './ImageLibrary'
import AudioLibrary from './AudioLibrary'
import MaterialDetailDrawer from './MaterialDetailDrawer'
import MaterialSidebar from './MaterialSidebar'
import styles from './MaterialTab.module.css'

const TENANT_KEY = 'flowcut'

const SUB_TABS: { key: MaterialType; label: string }[] = [
  { key: 'video', label: '视频' },
  { key: 'image', label: '图片' },
  { key: 'audio', label: '音频' },
]

const LIB_MAP: Record<MaterialType, React.ComponentType> = {
  video: VideoLibrary,
  image: ImageLibrary,
  audio: AudioLibrary,
}

export default function MaterialTab() {
  const { activeSubTab, setSubTab } = useMaterialStore()
  const fetchTree = useProductTreeStore((s) => s.fetchTree)
  const Lib = LIB_MAP[activeSubTab]

  useEffect(() => {
    fetchTree(TENANT_KEY)
  }, [fetchTree])

  return (
    <div className={`${styles.tab} ${styles.layout}`}>
      <div className={styles.subBar}>
        {SUB_TABS.map((t) => (
          <button
            key={t.key}
            className={`${styles.stb} ${activeSubTab === t.key ? styles.active : ''}`}
            onClick={() => setSubTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className={styles.body}>
        {activeSubTab === 'video' && <MaterialSidebar />}
        <div className={styles.content}>
          <Lib />
        </div>
      </div>
      <MaterialDetailDrawer />
    </div>
  )
}
```

- [ ] **Step 3: 类型检查**

```bash
cd flowcut_frontend && npx tsc --noEmit 2>&1 | grep "MaterialTab" || echo "no errors"
```

Expected: no errors.

- [ ] **Step 4: 提交**

```bash
git add flowcut_frontend/src/components/material/MaterialTab.tsx flowcut_frontend/src/components/material/MaterialTab.module.css
git commit -m "feat(frontend): MaterialTab 集成 MaterialSidebar 与左右布局"
```

---

### Task 14: 改造 `VideoLibrary.tsx`

**Files:**
- Modify: `flowcut_frontend/src/components/material/VideoLibrary.tsx`

- [ ] **Step 1: 移除旧 FilterChips，接入 productTreeStore + UploadModal**

Replace `flowcut_frontend/src/components/material/VideoLibrary.tsx`:

```tsx
import { useEffect, useState } from 'react'
import { useMaterialStore } from '../../stores/materialStore'
import { useProductTreeStore } from '../../stores/productTreeStore'
import { useDetailDrawerStore } from '../../stores/detailDrawerStore'
import DateGroup from '../common/DateGroup'
import MaterialCard from './MaterialCard'
import UploadCard from './UploadCard'
import UploadModal from './UploadModal'
import styles from './Library.module.css'
import type { Material } from '../../types'

const TENANT_KEY = 'flowcut'

function groupByDate(materials: Material[]) {
  const groups: Record<string, Material[]> = {}
  for (const m of materials) {
    const d = m.createdAt.split('T')[0]
    const label = d === new Date().toISOString().split('T')[0] ? '今天' : d
    if (!groups[label]) groups[label] = []
    groups[label].push(m)
  }
  return groups
}

export default function VideoLibrary() {
  const { filteredMaterials, fetchMaterials, isLoading } = useMaterialStore()
  const { activeProduct, activeSceneRole } = useProductTreeStore()
  const { openMaterialDetail } = useDetailDrawerStore()
  const [modalOpen, setModalOpen] = useState(false)

  useEffect(() => {
    fetchMaterials(TENANT_KEY, {
      product: activeProduct ?? undefined,
      sceneRole: activeSceneRole ?? undefined,
    })
  }, [fetchMaterials, activeProduct, activeSceneRole])

  const materials = filteredMaterials()
  const groups = groupByDate(materials)

  const breadcrumb = activeProduct
    ? `${activeProduct}${activeSceneRole ? ` / ${activeSceneRole}` : ''} · ${materials.length} 个素材`
    : `全部 · ${materials.length} 个素材`

  return (
    <div className={styles.layout}>
      <div className={styles.topBar}>
        <div style={{ fontSize: 13, color: '#555', fontWeight: 500 }}>{breadcrumb}</div>
        <div className={styles.spacer} />
        <button className={styles.uploadBtn} onClick={() => setModalOpen(true)}>
          ↑ 上传素材
        </button>
      </div>
      {isLoading && <div style={{ padding: '24px', color: '#999' }}>加载中…</div>}
      {!isLoading && (
        <div className={styles.grid}>
          {Object.entries(groups).map(([label, items], gi) => (
            <DateGroup key={label} label={label}>
              <div className={styles.cardGrid}>
                {gi === 0 && <UploadCard onClick={() => setModalOpen(true)} />}
                {items.map((m) => (
                  <MaterialCard key={m.id} material={m} onClick={openMaterialDetail} />
                ))}
              </div>
            </DateGroup>
          ))}
          {Object.keys(groups).length === 0 && (
            <DateGroup label="今天">
              <div className={styles.cardGrid}>
                <UploadCard onClick={() => setModalOpen(true)} />
              </div>
            </DateGroup>
          )}
        </div>
      )}
      <UploadModal
        open={modalOpen}
        onClose={() => setModalOpen(false)}
        onSuccess={() =>
          fetchMaterials(TENANT_KEY, {
            product: activeProduct ?? undefined,
            sceneRole: activeSceneRole ?? undefined,
          })
        }
      />
    </div>
  )
}
```

- [ ] **Step 2: 全量类型检查**

```bash
cd flowcut_frontend && npm run build 2>&1 | tail -15
```

Expected: build succeeds with no errors. If errors point to other files using `activeCategory` (e.g., ImageLibrary, AudioLibrary), update them in place to remove the field.

- [ ] **Step 3: 浏览器手工验证**

```bash
# Terminal 1: 后端
cd SimpleClaw
uv run python -m uvicorn Flowcut.api.server:app --reload --port 8001

# Terminal 2: 前端
cd flowcut_frontend
npm run dev
```

打开浏览器至 dev URL，进入素材 Tab，验证：

- [ ] 左侧出现产品树（即使为空也显示"暂无产品"提示）
- [ ] 点击右上「上传素材」按钮打开 Modal
- [ ] 单文件 Tab：选文件 → 输入产品名 → 选场景角色 → 上传成功后侧边栏出现新产品节点
- [ ] 单文件 Tab：在已有产品节点选中状态下打开 Modal，产品和场景角色自动预填
- [ ] ZIP Tab：上传一个含 `产品/场景角色/file.mp4` 的 zip，看到预览，确认导入后侧边栏更新
- [ ] 点击左侧产品节点 → 右侧只显示该产品素材
- [ ] 点击场景角色叶节点 → 右侧只显示该产品+场景角色素材
- [ ] 图片/音频 Tab 切换时侧边栏隐藏（保持现有逻辑）

- [ ] **Step 4: 提交**

```bash
git add flowcut_frontend/src/components/material/VideoLibrary.tsx
git commit -m "feat(frontend): VideoLibrary 接入 productTreeStore + UploadModal"
```

---

## Self-Review 自检结果

**Spec 覆盖：**
- ✅ 整体布局（左侧树 + 右侧网格）→ Task 13
- ✅ 组件结构（MaterialSidebar/UploadModal/ZipPreview）→ Tasks 10/11/12
- ✅ productTreeStore + materialStore 改造 → Tasks 8/9
- ✅ 单文件上传流程（含侧边栏预填）→ Task 12 (UploadModal) + Task 14 验证
- ✅ ZIP 上传流程（parse + confirm 两步）→ Tasks 3/4 (backend) + Task 12 (frontend)
- ✅ 后端接口变更（`/materials/tree` 改名 + 响应格式、`/upload` 加字段 + OSS 路径、`/upload-zip` + `/confirm`）→ Tasks 1/2/3/4

**类型一致性：**
- `ProductNode.totalCount` 在 types/store/api/sidebar 中名称一致
- `SceneRoleNode.sceneRole` / `.count` 一致
- `ZipPreviewItem.sceneRole` (前端) ↔ `scene_role` (后端) 有 `fromBackendZipItem` 映射
- `uploadMaterial(tenantKey, file, product, sceneRole?)` 签名在 API 和 UploadModal 调用处一致
- `confirmZip` 返回 `{ materialIds }` (camelCase)，前端用法一致

**Placeholder 扫描：** 无 TODO/TBD，所有代码块都是完整可粘贴的。

**潜在风险点：**
- `_make_upload_oss_key` 中的中文产品名进入 OSS key — Volcengine TOS SDK 应能正确处理 URL 编码，如出现问题需在 helper 内 urlencode 包一层
- ZIP 临时目录 TTL 30 分钟，未来若上传非常大的 zip 文件需考虑磁盘配额
- 中文文件名/中文产品名在 zip 内部编码可能不一致（zip 默认 CP437 vs UTF-8），如在测试中出现乱码需用 `zipfile.ZipFile(..., metadata_encoding='utf-8')`

---
