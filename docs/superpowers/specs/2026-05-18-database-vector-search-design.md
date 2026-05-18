# FlowCut 数据库选型 + 向量搜索设计

> 讨论日期：2026-05-18
> 状态：已确认，待实现

---

## 一、背景与决策驱动

当前 FlowCut 使用 MySQL（aiomysql）存储所有数据。本次评估的触发点是 `SearchMaterialsTool` 需要实现：根据脚本段语义描述在素材库中搜索匹配片段。关键字匹配无法满足语义层面的素材召回需求。

**评估的三个方案：**

| 方案 | 描述 | 结论 |
|------|------|------|
| A | MySQL → PostgreSQL 全量迁移（含 pgvector） | 迁移成本高，Mojing 线上业务迁移有风险，收益不足以覆盖成本 |
| B | MySQL 保持不动 + 新增 Qdrant 向量服务 | **采用**，零迁移风险，现有代码不变，专门解决语义搜索 |
| C | MySQL + embedding 存列（numpy 暴力搜索） | 技术债，> 5000 条开始退化，排除 |

**最终选型：方案 B — MySQL（现有）+ Qdrant（新增）+ Ollama bge-m3（本地 Embedding）**

---

## 二、整体架构

```
┌─────────────────────────────────────────────────────────────┐
│  FlowCut 后端                                                │
│                                                             │
│  MySQL（现有，事实来源）     Qdrant（新增，搜索索引）          │
│  ─────────────────────      ────────────────────────        │
│  fc_reference_video         fc_material_vectors             │
│  fc_material                  point per material            │
│  fc_script                    vector = embed(description)   │
│  fc_creative                  payload = 过滤字段             │
│  fc_material_usage                                          │
│  fc_qianchuan_account        Ollama（本地）                  │
│  nb_* (Mojing 不变)          ─────────────                  │
│                              bge-m3 embedding model         │
└─────────────────────────────────────────────────────────────┘
```

**职责划分：**
- **MySQL** 是所有结构化数据的权威来源，状态机转换、关系查询均在此
- **Qdrant** 是 MySQL `fc_material` 的搜索索引，可随时从 MySQL 重建，不是核心数据
- **Ollama bge-m3** 提供本地中文语义 embedding，无 API 成本，无外部依赖

---

## 三、数据模型变更

### 3.1 新增 `fc_reference_video` 表

爆款视频（用于拆镜的输入）与可用素材语义不同，独立成表。

```sql
CREATE TABLE fc_reference_video (
    id            BIGINT        NOT NULL AUTO_INCREMENT,
    tenant_key    VARCHAR(255)  NOT NULL,
    oss_key       VARCHAR(512)  NOT NULL,
    oss_url       VARCHAR(1024) NOT NULL,
    thumbnail_url VARCHAR(1024) NULL,
    name          VARCHAR(255)  NOT NULL,
    duration      FLOAT         NOT NULL,
    file_size     BIGINT        NOT NULL,
    scene_data_json JSON        NULL,     -- 拆镜完整结果（Gemini + PySceneDetect 对齐后）
    status        VARCHAR(16)   NOT NULL DEFAULT 'PROCESSING',
                                          -- PROCESSING → DECOMPOSED / FAILED
    created_at    DATETIME      NOT NULL,
    updated_at    DATETIME      NOT NULL,
    PRIMARY KEY (id),
    KEY idx_fc_ref_tenant (tenant_key),
    KEY idx_fc_ref_status (status)
)
```

### 3.2 修改 `fc_material` 表

**移除：** `scene_data_json`（语义属于参考视频，子素材不需要）

**新增：**

```sql
source_video_id  BIGINT  NULL,   -- FK → fc_reference_video.id；直接上传的素材为 NULL
description      TEXT    NULL,   -- Gemini 多模态视觉描述（embedding 的唯一来源）
                                  -- transcript 保留，存 ASR 语音文字
```

**两类素材的字段填充对比：**

| 字段 | 直接上传的素材 | 从爆款视频拆出的片段 |
|------|-------------|-------------------|
| `source_video_id` | NULL | fc_reference_video.id |
| `transcript` | ASR 语音转文字 | 可选（按需跑 ASR） |
| `description` | Gemini analyze_video() 输出 | Gemini segment content（拆镜时已有） |
| `scene_data_json` | 已移除 | 已移除（存在父表） |

**Embed 唯一来源：`description` 字段。** 语义统一，视觉一致，不受 transcript 有无影响。

### 3.3 数据一致性规则

Qdrant 中素材点的生命周期跟随 MySQL `fc_material.status`：

| MySQL status | Qdrant 动作 |
|-------------|------------|
| PROCESSING | 不写入 |
| READY（且 description 非空） | upsert point |
| FAILED | 不写入 / 删除已有点 |
| 记录被删除 | 同步删除 Qdrant 点 |

Qdrant 写入失败不阻塞主流程，记 warning 日志。AppContainer 启动一个修复任务，每 10 分钟扫描 `status=READY AND description IS NOT NULL` 但 Qdrant 中无对应点的素材，补写进去。

---

## 四、Embedding 服务

### 4.1 模型选择

**Ollama 本地部署，模型：`bge-m3`**

选择理由：
- 素材 description 和脚本段均为中文，bge-m3 是多语言模型，中文语义理解质量最佳
- 本地运行，无 API 成本，无外部依赖，不影响 executor 吞吐
- 向量维度 1024，对 < 10 万条素材检索精度足够
- Ollama 标准 REST API，替换模型只需改配置

```bash
ollama pull bge-m3
```

### 4.2 EmbeddingService 接口

新增 `Flowcut/services/embedding.py`：

```python
class EmbeddingService(Protocol):
    async def embed(self, text: str) -> list[float]: ...

class OllamaEmbeddingService:
    def __init__(self, base_url: str, model: str) -> None: ...
    async def embed(self, text: str) -> list[float]: ...
    # 调用 POST {base_url}/api/embeddings，返回 data["embedding"]
```

### 4.3 环境变量

```
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_EMBEDDING_MODEL=bge-m3
QDRANT_URL=http://localhost:6333
```

---

## 五、Qdrant 数据模型

### Collection：`fc_material_vectors`

每个 point = 一条 `fc_material` 记录（status=READY，description 非空）

```json
{
  "id": 42,
  "vector": [0.023, -0.187, ...],
  "payload": {
    "tenant_key": "t_001",
    "category": "产品展示",
    "status": "READY",
    "source_video_id": 10
  }
}
```

查询时始终携带 `tenant_key` filter，保证租户隔离。

### VectorStore 封装

新增 `Flowcut/storage/vector_store.py`：

```python
class VectorStore:
    async def upsert(self, material_id: int, vector: list[float], payload: dict) -> None: ...
    async def search(
        self,
        query_vector: list[float],
        tenant_key: str,
        category: str | None = None,
        limit: int = 3,
    ) -> list[int]: ...   # 返回 material_id 列表，按相似度降序
    async def delete(self, material_id: int) -> None: ...
    async def ensure_collection(self) -> None: ...   # 启动时调用，collection 不存在则创建
```

---

## 六、写入路径变更

### 6.1 make_material_process_executor（直接上传的素材）

新增步骤：ASR 和 Gemini 并行执行，两者完成后写入 MySQL，再 upsert Qdrant。

```
下载视频
  ├─→ FFmpeg 提取音频 → ASR → transcript
  └─→ Gemini analyze_video() → segments
        description = " ".join(seg["content"] for seg in segments)
        # 直接上传的素材通常是单个场景片段，多段时拼接描述
        # 拼接后长度通常 < 300 字，不影响 bge-m3 embedding 质量

update_status(READY, transcript=..., description=...)   ← MySQL
embedding.embed(description) → vector_store.upsert()   ← Qdrant
```

### 6.2 make_scene_decompose_executor（爆款视频拆镜）

拆镜结果写入 `fc_reference_video`（而非原 material 记录），子片段创建 `fc_material` 并 upsert Qdrant。

```
Gemini analyze_video() + PySceneDetect → aligned segments

# 父记录
fc_reference_video.update(scene_data=aligned, status=DECOMPOSED)

# 每个子片段（并行）
_process_segment_clip():
    FFmpeg 切条 → OSS 上传
    fc_material.create(source_video_id=parent_id, description=seg.content, ...)
    update_status(READY, description=content, thumbnail_url=...)
    embedding.embed(description) → vector_store.upsert()
```

---

## 七、SearchMaterialsTool 搜索流程

### 7.1 执行逻辑

```
输入：script_id
  ↓
从 MySQL 取脚本段列表 segments = script["segments_json"]
  ↓
并行：每个 segment 独立 embed + 查 Qdrant（带 tenant_key + category filter）
  ↓
收集所有候选 material_id → 批量从 MySQL 取完整记录
  ↓
组装三档结果（最优 / 次优 / 备选）返回 LLM
```

### 7.2 返回格式示例

```
脚本段 0「开场：产品外观特写」（5s）
  ✅ 最优  素材 #11 [3s] 口红管身特写，哑光质感  相似度 0.91
  ▸ 次优  素材 #28 [4s] 产品平铺展示，白底背景  相似度 0.84
  ○ 备选  素材 #33 [2s] 包装特写，高光打光      相似度 0.79
```

### 7.3 边界情况处理

| 情况 | 处理方式 |
|------|---------|
| 某段 Qdrant 返回 0 条结果 | 降级：按 category 从 MySQL 取最新 3 条，标注"未找到语义匹配，按分类兜底" |
| description 为 NULL 的素材 | 不写入 Qdrant，不参与搜索 |
| Qdrant 服务不可用 | search_materials 返回失败，提示用户手动选择素材 |

---

## 八、新增文件清单

| 文件 | 说明 |
|------|------|
| `Flowcut/storage/vector_store.py` | VectorStore（Qdrant 封装） |
| `Flowcut/services/embedding.py` | EmbeddingService + OllamaEmbeddingService |

**修改的文件：**

| 文件 | 变更 |
|------|------|
| `Flowcut/storage/database.py` | 新增 fc_reference_video 表；fc_material 加 description / source_video_id，移除 scene_data_json |
| `Flowcut/storage/material_repo.py` | 新增 description 字段支持 |
| `Flowcut/runtime/executors.py` | material_process + scene_decompose executor 新增 Gemini + Qdrant upsert |
| `Flowcut/tools/search_materials.py` | 实现语义搜索逻辑 |
| `Flowcut/api/container.py` | 新增 VectorStore + EmbeddingService 初始化 |
| `Flowcut/config.py` | 新增 OLLAMA_* / QDRANT_URL 配置项 |

**不动的文件：**
- `Flowcut/storage/creative_repo.py`、`script_repo.py`、`session_store.py` 等
- `Mojing/` 全部代码
- `simpleclaw/` 全部代码

---

## 九、待进一步讨论

- **搜索准确率优化**：当前方案是单字段 embed + cosine 相似度，后续可考虑重排序（rerank）、混合检索（向量 + 关键字 BM25）等策略提升召回精度
