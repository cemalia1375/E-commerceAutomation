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
    product       VARCHAR(128)  NULL,     -- 用户上传时选择的产品名，传递给子素材
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
source_video_id  BIGINT       NULL,        -- FK → fc_reference_video.id；直接上传的素材为 NULL
description      TEXT         NULL,        -- Gemini 多模态视觉描述（embedding 的唯一来源）
                                           -- transcript 保留，存 ASR 语音文字
product          VARCHAR(128) NULL,        -- 产品名，用户上传时手动选择；NULL 表示通用素材
scene_role       VARCHAR(64)  NULL,        -- 场景角色，用户上传时选择（医生/药材/冲洗/产品展示/痛点/美好等）
                                           -- 取代原 category 字段成为主要分类维度
                                           -- category 字段保留，降为 Gemini 自动分类的辅助字段
```

**两类素材的字段填充对比：**

| 字段 | 直接上传的素材 | 从爆款视频拆出的片段 |
|------|-------------|-------------------|
| `source_video_id` | NULL | fc_reference_video.id |
| `transcript` | ASR 语音转文字 | 可选（按需跑 ASR） |
| `description` | Gemini analyze_video() 输出 | Gemini segment content（拆镜时已有） |
| `product` | 上传时用户选择 | 继承自父 fc_reference_video.product |
| `scene_role` | 上传时用户选择 | Gemini category 字段映射，用户可修改 |
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

## 五、OSS 目录结构

产品名作为 OSS 路径的一级分区，人工可浏览，同时与 `fc_material.product` 字段对应。

```
materials/{tenant_key}/
  雪莲洗液/
    clips/                  ← 从爆款视频拆出的子片段
    uploads/                ← 直接上传的产品专属素材
  妆前乳/
    clips/
    uploads/
  通用/
    clips/
    uploads/                ← 跨产品可复用的素材（品牌片头、空镜等）
```

OSS key 格式：`materials/{tenant_key}/{product}/clips/{timestamp}_{idx}.mp4`

`product` 为空时写入 `通用/` 目录。

---

## 六、Qdrant 数据模型

### Collection：`fc_material_vectors`

每个 point = 一条 `fc_material` 记录（status=READY，description 非空）

```json
{
  "id": 42,
  "vector": [0.023, -0.187, ...],
  "payload": {
    "tenant_key": "t_001",
    "product": "雪莲洗液",
    "scene_role": "医生",
    "status": "READY"
  }
}
```

查询时始终携带 `tenant_key` filter 保证租户隔离，`product` filter 保证产品隔离，可选 `scene_role` filter 进一步收窄候选范围。

### VectorStore 封装

新增 `Flowcut/storage/vector_store.py`：

```python
class VectorStore:
    async def upsert(self, material_id: int, vector: list[float], payload: dict) -> None: ...
    async def search(
        self,
        query_vector: list[float],
        tenant_key: str,
        product: str | None = None,     # None = 通用素材
        scene_role: str | None = None,  # 可选，进一步收窄候选范围
        limit: int = 3,
    ) -> list[int]: ...   # 返回 material_id 列表，按相似度降序
    async def delete(self, material_id: int) -> None: ...
    async def ensure_collection(self) -> None: ...   # 启动时调用，collection 不存在则创建
```

---

## 八、写入路径变更

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

### 7.1 两阶段搜索策略

搜索不跨产品。每个脚本段按以下顺序召回，凑满 3 条为止：

```
阶段一（产品专属）：
  filter: tenant_key=t_001 AND product="雪莲洗液"
  取 top-3，保留相似度 ≥ 0.70 的结果

阶段二（通用兜底，仅当阶段一结果 < 3 条时触发）：
  filter: tenant_key=t_001 AND product=NULL（通用素材）
  补齐到 3 条，标注"通用素材"

禁止：不同产品之间的素材混搜
```

### 7.2 执行逻辑

```
输入：script_id，当前 product（从 session 上下文取）
  ↓
从 MySQL 取脚本段列表 segments = script["segments_json"]
  ↓
并行：每个 segment 独立执行两阶段搜索
  ├─→ 阶段一：embed(seg.desc) → Qdrant(product=current_product, limit=3)
  └─→ 阶段二（按需）：Qdrant(product=None, limit=3-len(phase1_results))
  ↓
收集所有候选 material_id → 批量从 MySQL 取完整记录
  ↓
组装三档结果返回 LLM
```

### 7.3 返回格式示例

```
脚本段 0「开场：产品外观特写」（5s）
  ✅ 最优  素材 #11 [3s] 口红管身特写，哑光质感   相似度 0.91  [雪莲洗液]
  ▸ 次优  素材 #28 [4s] 产品平铺展示，白底背景   相似度 0.84  [雪莲洗液]
  ○ 备选  素材 #03 [2s] 通用品牌片头，白底空镜   相似度 0.71  [通用]
```

### 7.4 边界情况处理

| 情况 | 处理方式 |
|------|---------|
| 两阶段均为 0 条结果 | 降级：按 category 从 MySQL 取该产品最新 3 条，标注"未找到语义匹配，按分类兜底" |
| description 为 NULL 的素材 | 不写入 Qdrant，不参与搜索 |
| Qdrant 服务不可用 | search_materials 返回失败，提示用户手动选择素材 |

---

## 九、新增文件清单

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

## 十、前端素材库展示

### 层级结构

素材库 Tab 呈现两级树：**产品 → 场景角色**，与本地文件夹组织逻辑一致。

```
雪莲洗液 (80)
  ├─ 医生 (45)
  ├─ 药材 (12)
  ├─ 冲洗 (23)
  └─ 产品展示 (8) (痛点/美好 等同理)
妆前乳 (30)
  └─ ...
通用 (15)
```

### 数据来源

前端不读 OSS 目录，完全从 MySQL 查询：

```sql
-- 树形汇总（左侧导航）
SELECT product, scene_role, COUNT(*) as cnt
FROM fc_material
WHERE tenant_key = ? AND status = 'READY'
GROUP BY product, scene_role
ORDER BY product, scene_role

-- 点击某个叶节点加载素材列表
GET /materials?product=雪莲洗液&scene_role=医生
```

### 实现复杂度

低。Ant Design `Tree` 组件直接支持两级数据，前端将 GROUP BY 结果转换为树节点即可。OSS 路径中包含 `product/scene_role` 路径信息，与 UI 层级保持视觉一致，但 UI 不依赖 OSS 路径构建。

---

## 十一、待进一步讨论

- **搜索准确率优化**：当前方案是产品分区 + scene_role 收窄 + 单字段 embed + cosine 相似度，后续可考虑重排序（rerank）、混合检索（向量 + 关键字 BM25）等策略进一步提升召回精度
- **product / scene_role 管理**：MVP 阶段均为自由文本；后续可增加 `fc_product` 表做枚举管理，支持产品别名、合并等操作；scene_role 可按产品维护一套预设标签
- **scene_role 自动识别**：爆款视频拆镜时 Gemini prompt 可尝试输出 scene_role 建议值，用户在前端确认或修改后再入库
