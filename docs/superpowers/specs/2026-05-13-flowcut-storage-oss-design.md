# Flowcut 存储层 + OSS + 素材处理 实现设计

> 2026-05-13，基于 Flowcut 业务层骨架的 TODO 填补。

## 一、范围

### 本轮实现

| 模块 | 内容 |
|------|------|
| `.env.example` | 新增 MYSQL + TOS + ASR 环境变量 |
| `requirements.txt` | 新增 `tos` 依赖 |
| `Flowcut/storage/oss_client.py` | **新建** — TOS SDK 封装（预签名 URL、删除、公开 URL） |
| `Flowcut/storage/material_repo.py` | 实现 7 个 CRUD 方法 |
| `Flowcut/storage/creative_repo.py` | 实现 6 个 CRUD 方法 |
| `Flowcut/storage/script_repo.py` | 实现 3 个 CRUD 方法 |
| `Flowcut/storage/qianchuan_repo.py` | 实现 5 个 CRUD 方法 |
| `Flowcut/api/routes/materials.py` | 实现 upload-token / process / CRUD 全部端点 |
| `Flowcut/runtime/executors.py` | 实现 MATERIAL_PROCESS executor（含 FFmpeg + ASR） |

### 不在本轮

- creative/script/qianchuan 的 REST 路由（仍 501）
- 其他 4 个 executor（仍 stub）
- `TaskContextProvider.collect_dynamic_context`（仍返回 []）

---

## 二、OSS 客户端

**文件**: `Flowcut/storage/oss_client.py`

- 封装 `tos.TosClientV2(ak, sk, endpoint, region)`
- 初始化参数从 `config.make_oss_config()` 读取
- 文件夹结构：
  - 素材: `materials/{tenant_key}/{timestamp}_{filename}` — 扁平（时间戳防同名覆盖）
  - 成片: `creatives/{tenant_key}/{creative_id}/` — 保留 creative_id 层级

### 接口

```python
class OSSClient:
    def __init__(self, endpoint, ak, sk, bucket, region)
    def presigned_put_url(key, expires=3600) -> str   # 前端直传
    def presigned_get_url(key, expires=3600) -> str   # 预览/下载
    def get_public_url(key) -> str                     # 构造公开访问 URL（前提：bucket 公开读）
    def delete_object(key) -> None
```

> **实现前确认：** TOS 的 Python SDK 包名是 `tos`，核实后再加到 `requirements.txt`。`get_public_url` 仅当 bucket 为公开读时可用；若 bucket 为私有，统一用 `presigned_get_url`。

---

## 三、素材上传全流程

### Step 1 — POST /materials/upload-token

```
请求体: { tenant_key, filename }
后端:
  1. timestamp = int(time.time())
     oss_key = f"materials/{tenant_key}/{timestamp}_{filename}"
  2. INSERT fc_material (status='PROCESSING', oss_key=oss_key)
  3. 调 oss_client.presigned_put_url(oss_key)
  4. 返回 { material_id, presigned_url, oss_key }
```

### Step 2 — POST /materials/{id}/process

```
后端:
  1. 查 material 存在且 status='PROCESSING'
  2. 入队 MATERIAL_PROCESS（task_id 写入 DB）
  3. 返回 { material_id, task_id, status: 'queued' }
```

### Step 3 — MATERIAL_PROCESS Executor

```
process(material_id):
  1. 查 material
  2. 后缀名判断文件类型:
     ├── 视频 (.mp4/.mov/.avi/.mkv/.webm/.flv)
     │   ├── FFmpeg 提取音轨 → 本地临时 wav
     │   ├── base64 编码本地 wav → 调 ASR → transcript
     │   ├── 清理临时文件
     │   └── 更新 status=READY, transcript=xxx
     ├── 音频 (.mp3/.wav/.aac/.flac/.ogg/.m4a)
     │   └── 更新 status=READY（跳过 ASR）
     └── 图片 (.jpg/.jpeg/.png/.gif/.webp/.bmp)
         └── 更新 status=READY（无 ASR）
  3. 错误处理:
     ├── FFmpeg 失败 → 更新 status=FAILED, err_msg="音轨提取失败: {detail}"
     ├── ASR 超时/返回错误码 → 更新 status=FAILED, err_msg="ASR 识别失败: {code} {msg}"
     └── 其他异常 → 更新 status=FAILED, err_msg="物料处理异常: {traceback}"
  4. 返回 TaskExecutionResult(status='succeeded'|'failed')
```

ASR 调用方式：
- 端点: `POST https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash`
- 鉴权: Header 传 `X-Api-App-Key` + `X-Api-Access-Key`
- 请求体: `{ "audio": { "data": "<base64 编码音频>" }, "user": {"uid": "<AppKey>"}, "request": {"model_name": "bigmodel"} }`（同步返回识别结果）
- 环境变量: `FLOWCUT_ASR_APP_KEY` / `FLOWCUT_ASR_ACCESS_KEY`
- 注意: 极速版要求音频 ≤ 100MB / ≤ 2h，超限应考虑走标准版 submit/query 模式

---

## 四、Repository 实现

全部遵循 `task_repo.py` 的 SQL 模式：
- `async with self._db.acquire() as conn` + `async with conn.cursor() as cur`
- 时间戳用 `_now()` 辅助函数
- JSON 字段用 `json.dumps(..., ensure_ascii=False)` 序列化

### material_repo (7 methods)
- `create` — INSERT 返回 lastrowid
- `get` — SELECT by id
- `list_by_tenant` — SELECT with optional category/status filters + LIMIT/OFFSET
- `update_status` — UPDATE status + optional name/transcript/thumbnail_url/preview_url
- `update` — UPDATE name/category
- `delete` — DELETE by id
- `increment_usage` — UPDATE usage_count = usage_count + 1

### creative_repo (6 methods)
- `create` — INSERT, optional script_id
- `get` — SELECT by id
- `list_by_tenant` — SELECT with LIMIT/OFFSET
- `update_status` — UPDATE status + optional oss fields
- `update_label` — UPDATE label
- `update_qianchuan_ids` — UPDATE qianchuan_material_id/campaign_id

### script_repo (3 methods)
- `create` — INSERT, segments_json = json.dumps(segments)
- `get` — SELECT + json.loads(segments_json)
- `list_by_session` — SELECT by tenant_key + session_key

### qianchuan_repo (5 methods)
- `upsert_account` — INSERT ... ON DUPLICATE KEY UPDATE
- `get_account` — SELECT by tenant + advertiser
- `list_accounts` — SELECT by tenant
- `update_tokens` — UPDATE access_token + expires_at
- `update_campaign_id` — UPDATE campaign_id

---

## 五、REST 端点（materials 路由）

| 方法 | 路径 | 功能 |
|------|------|------|
| `POST` | `/materials/upload-token` | 返回 presigned URL + material_id |
| `POST` | `/materials/{id}/process` | 入队 MATERIAL_PROCESS |
| `GET` | `/materials` | 列表（支持 category/status 过滤 + 分页） |
| `GET` | `/materials/{id}` | 单条详情 |
| `PATCH` | `/materials/{id}` | 手动修改 name/category |
| `DELETE` | `/materials/{id}` | 删除 |

---

## 六、环境变量

```env
# MySQL（已有 Docker 容器）
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=
MYSQL_DB=flowcut

# TOS OSS
FLOWCUT_OSS_ENDPOINT=
FLOWCUT_OSS_ACCESS_KEY_ID=
FLOWCUT_OSS_ACCESS_KEY_SECRET=
FLOWCUT_OSS_BUCKET=
FLOWCUT_OSS_REGION=

# 火山引擎 ASR（录音文件极速版）
FLOWCUT_ASR_APP_KEY=
FLOWCUT_ASR_ACCESS_KEY=
```

---

## 七、实现顺序

1. `.env.example` + `requirements.txt` 更新
2. `oss_client.py` 新建
3. 4 个 repo 实现（material → creative → script → qianchuan）
4. `routes/materials.py` 补全
5. `executors.py` MATERIAL_PROCESS executor
6. 验证：服务启动 → 调用 upload-token → process → 查 material 状态变更
