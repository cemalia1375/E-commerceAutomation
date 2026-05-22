# FlowCut 数据库选型 + 向量搜索设计

> 讨论日期：2026-05-18
> 状态：已实现（services/embedding.py + storage/vector_store.py + tools/search_materials.py）
> **变更记录：**
> - 2026-05-18 初始设计稿（双向量融合方案）
> - 2026-05-18 实现：OllamaEmbeddingService、VectorStore（Qdrant 双向量 named vectors）、SearchMaterialsTool 两阶段搜索
> - 待实现：embedding 修复任务（vector_repair executor）、前端素材库树形结构、Qdrant 生产部署

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
description      TEXT         NULL,        -- Gemini 多模态视觉描述（视觉向量来源）
                                           -- transcript 保留，存 ASR 语音文字（文本向量来源）
product          VARCHAR(128) NULL,        -- 产品名，用户上传时从已有列表选择或新建；NULL 表示通用素材
                                           -- 前端通过 AutoComplete 控件呈现：下拉列出该租户已有 product，
                                           -- 同时允许输入新值。后端提供 GET /products?tenant_key= 接口，
                                           -- SELECT DISTINCT product FROM fc_material WHERE tenant_key=?
                                           -- MVP 阶段不引入独立 fc_product 表，靠输入路径约束避免脏数据；
                                           -- 后续需要产品合并/别名时再升级建表
scene_role       VARCHAR(64)  NULL,        -- 场景角色，用户上传时选择（医生/药材/冲洗/产品展示/痛点/美好等）
                                           -- 取代原 category 字段成为主要分类维度
                                           -- category 字段保留，降为 Gemini 自动分类的辅助字段
vector_indexed   BOOLEAN      NOT NULL DEFAULT FALSE,
                                           -- Qdrant upsert 成功后回写 TRUE；修复任务只扫 FALSE 行
```

**两类素材的字段填充对比：**

| 字段 | 直接上传的素材 | 从爆款视频拆出的片段 |
|------|-------------|-------------------|
| `source_video_id` | NULL | fc_reference_video.id |
| `transcript` | ASR 语音转文字 | 可选（按需跑 ASR） |
| `description` | Gemini analyze_video() 输出 | Gemini segment content（拆镜时已有） |
| `product` | 上传时用户选择/新建 | 继承自父 fc_reference_video.product |
| `scene_role` | 上传时用户选择 | Gemini category 字段映射，用户可修改 |
| `scene_data_json` | 已移除 | 已移除（存在父表） |

**Embedding 双向量：** `description`（视觉向量）+ `transcript`（文本向量）分别 embed，作为 Qdrant 同一 point 上的两个 named vectors。查询时分别打分取 max 融合。transcript 为空（视觉素材、空镜、纯音乐片段）时仅写入 desc_vec，查询路径需兼容缺失向量的 point。详见 §六。

### 3.3 数据一致性规则

Qdrant 中素材点的生命周期跟随 MySQL `fc_material.status`：

| MySQL status | Qdrant 动作 |
|-------------|------------|
| PROCESSING | 不写入 |
| READY（且 description 非空） | upsert point |
| FAILED | 不写入 / 删除已有点 |
| 记录被删除 | 同步删除 Qdrant 点 |

Qdrant 写入失败不阻塞主流程，记 warning 日志。`fc_material.vector_indexed` 字段标记同步状态：

- executor 在 upsert Qdrant 成功后回写 `vector_indexed = TRUE`
- AppContainer 启动一个修复任务，每 10 分钟执行：
  ```sql
  SELECT id FROM fc_material
  WHERE status='READY' AND description IS NOT NULL AND vector_indexed = FALSE
  LIMIT 100
  ```
  对每条 embedding + upsert 重试，成功后回写 `vector_indexed = TRUE`
- 扫描成本为 O(失败数) 而非 O(全表)，加 `KEY idx_fc_material_pending_vector (vector_indexed, status)` 加速

素材被删除时，`material_repo.delete()` 同步调用 `vector_store.delete(material_id)`。

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

每个 point = 一条 `fc_material` 记录（status=READY，description 非空）。Collection 配置 [named vectors](https://qdrant.tech/documentation/concepts/vectors/#named-vectors)：`desc_vec`（视觉）和 `transcript_vec`（文本），均为 1024 维 cosine。

```json
{
  "id": 42,
  "vector": {
    "desc_vec": [0.023, -0.187, ...],
    "transcript_vec": [0.114, 0.302, ...]   // transcript 为空时该向量缺失
  },
  "payload": {
    "tenant_key": "t_001",
    "product": "雪莲洗液",
    "scene_role": "医生",
    "status": "READY",
    "has_transcript": true
  }
}
```

查询时始终携带 `tenant_key` filter 保证租户隔离，`product` filter 保证产品隔离，可选 `scene_role` filter 进一步收窄候选范围（MVP 不启用，预留接口）。

### 双向量融合策略

每个脚本段查询会触发两次 Qdrant search：

1. 用 `embed(seg.desc)` 查 `desc_vec` 取 top-N
2. 用 `embed(seg.desc)` 查 `transcript_vec` 取 top-N（transcript_vec 缺失的 point 自动跳过）

两组结果按 material_id 合并，每个 id 的最终得分 = `max(desc_score, transcript_score)`，按得分降序取 top-K 返回。

- **为什么 max 而非 weighted sum：** MVP 阶段不引入需要调参的权重。任一字段命中即认为该素材语义相关；视觉素材（无 transcript）只靠 desc_vec 评分，不会被惩罚
- **为什么不用 RRF：** RRF 适合多路异构召回；这里两路都是 cosine 相似度，量纲一致，直接 max 即可

### VectorStore 封装

新增 `Flowcut/storage/vector_store.py`：

```python
class VectorStore:
    async def upsert(
        self,
        material_id: int,
        desc_vector: list[float],
        transcript_vector: list[float] | None,   # 无 transcript 时传 None
        payload: dict,
    ) -> None: ...

    async def search(
        self,
        desc_query_vector: list[float],
        transcript_query_vector: list[float],    # 同 desc，复用 seg.desc 的 embedding
        tenant_key: str,
        product: str | None = None,              # None = 通用素材
        scene_role: str | None = None,           # MVP 不启用
        limit: int = 3,
    ) -> list[tuple[int, float]]: ...
    # 返回 [(material_id, fused_score), ...]，按 fused_score 降序
    # fused_score = max(desc_score, transcript_score)

    async def delete(self, material_id: int) -> None: ...
    async def ensure_collection(self) -> None: ...   # 启动时调用，collection 不存在则创建
```

注：查询时 `desc_query_vector` 和 `transcript_query_vector` 当前均使用同一个 `embed(seg.desc)`，因为脚本段的描述天然兼具视觉和语义信息。若后续脚本段增加独立的"台词"字段，可改为分别 embed。

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
desc_vec = embedding.embed(description)
transcript_vec = embedding.embed(transcript) if transcript else None
vector_store.upsert(material_id, desc_vec, transcript_vec, payload) ← Qdrant
material_repo.mark_vector_indexed(material_id)                       ← MySQL 回写 vector_indexed=TRUE
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
    desc_vec = embedding.embed(description)
    transcript_vec = embedding.embed(transcript) if transcript else None
    vector_store.upsert(material_id, desc_vec, transcript_vec, payload)
    material_repo.mark_vector_indexed(material_id)
```

---

## 七、SearchMaterialsTool 搜索流程

### 7.1 两阶段搜索策略

搜索不跨产品。每个脚本段按以下顺序召回，凑满 3 条为止：

```
阶段一（产品专属）：
  filter: tenant_key=t_001 AND product="雪莲洗液"
  对 desc_vec 和 transcript_vec 各 search top-3
  按 material_id 合并，融合 score = max(desc_score, transcript_score)
  保留 fused_score ≥ 0.70 的结果（阈值待 bge-m3 实测后调整）

阶段二（通用兜底，仅当阶段一结果 < 3 条时触发）：
  filter: tenant_key=t_001 AND product=NULL（通用素材）
  同样双向量 max 融合，补齐到 3 条，标注"通用素材"

禁止：不同产品之间的素材混搜
```

### 7.2 执行逻辑

```
输入：script_id，当前 product（从 session 上下文取）
  ↓
从 MySQL 取脚本段列表 segments = script["segments_json"]
  ↓
并行：每个 segment 独立执行两阶段搜索
  query_vec = embed(seg.desc)   # desc 和 transcript 查询使用同一向量
  ├─→ 阶段一：vector_store.search(query_vec, query_vec, product=current_product, limit=3)
  └─→ 阶段二（按需）：vector_store.search(query_vec, query_vec, product=None, limit=3-len(phase1))
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
| `Flowcut/storage/database.py` | 新增 fc_reference_video 表；fc_material 加 description / source_video_id / vector_indexed，移除 scene_data_json |
| `Flowcut/storage/material_repo.py` | 新增 description 字段支持；新增 `mark_vector_indexed()`、`list_pending_vector()`、`list_distinct_products()` |
| `Flowcut/runtime/executors.py` | material_process + scene_decompose executor 新增 Gemini + Qdrant 双向量 upsert；新增 vector_repair executor |
| `Flowcut/tools/search_materials.py` | 实现双向量 max 融合的语义搜索 |
| `Flowcut/api/container.py` | 新增 VectorStore + EmbeddingService 初始化；启动 vector_repair 周期任务（10 分钟） |
| `Flowcut/api/routes.py` | 新增 `GET /products?tenant_key=` 接口供前端 AutoComplete 使用 |
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

- **搜索准确率优化**：当前方案是产品分区 + 双向量（desc + transcript）max 融合 + cosine 相似度。后续可考虑：
  - 重排序（rerank）：取 top-N 后用 cross-encoder 重排
  - 混合检索：在 desc/transcript 向量之外加关键字 BM25 召回
  - 权重融合：从 max 改为 weighted sum，按字段调权重
  - 启用 scene_role filter 进一步收窄候选范围（接口已预留）
- **相似度阈值 0.70**：当前为经验值，待 bge-m3 上线后通过实测数据调整
- **product 治理升级**：MVP 用 AutoComplete + DISTINCT 查询约束输入。当出现产品别名/合并需求时升级为独立 `fc_product` 表
- **scene_role 自动识别**：爆款视频拆镜时 Gemini prompt 可尝试输出 scene_role 建议值，用户在前端确认或修改后再入库
- **embedding 服务扩容**：Ollama bge-m3 单实例适合 MVP；如果上传并发或搜索 QPS 上升，需评估切换到批量 embedding API 或多实例负载均衡
