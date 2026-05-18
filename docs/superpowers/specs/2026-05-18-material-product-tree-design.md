# FlowCut 素材库产品分层设计

> 讨论日期：2026-05-18
> 状态：设计完成，待实现

---

## 一、背景

当前素材页使用平铺 `FilterChips`（人物/产品/场景/氛围）过滤素材，与 OSS 存储结构（`materials/{tenant_key}/{product}/uploads/`）及数据库 `fc_material.product` + `fc_material.scene_role` 字段完全脱节。本次改造目标：前端素材库以「产品 → 场景角色」两级树状结构组织素材，与 OSS 路径和 MySQL 字段保持一致。

---

## 二、整体布局

采用**左侧树形侧边栏 + 右侧素材网格**布局：

```
┌─────────────────────────────────────────────────────┐
│  视频  │  图片  │  音频                              │  ← 现有 subTab
├──────────────┬──────────────────────────────────────┤
│ 产品          │  雪莲洗液 / 医生  · 45 个素材         │
│ ─────────    │  ┌─────┐ ┌─────┐ ┌─────┐ ┌──────┐  │
│ ▼ 雪莲洗液80 │  │     │ │     │ │     │ │  +   │  │
│   医生    45 │  └─────┘ └─────┘ └─────┘ └──────┘  │
│   药材    12 │                                      │
│   冲洗    23 │                                      │
│ ▶ 妆前乳  30 │                                      │
│   通用    15 │                                      │
└──────────────┴──────────────────────────────────────┘
```

- 点**产品节点**：加载该产品全部素材（activeSceneRole = null）
- 点**场景角色叶节点**：精确过滤到该产品 + 场景角色
- 无选中（默认进入）：加载全部素材
- **MVP 范围**：产品树仅对视频素材生效，图片/音频保持现有逻辑

---

## 三、组件结构

```
MaterialTab.tsx                ← 改造：加 Ant Design Sider/Content 布局
├── MaterialSidebar.tsx        ← 新增：树形导航
├── VideoLibrary.tsx           ← 改造：接入 productTreeStore，移除旧 FilterChips
│   └── UploadModal.tsx        ← 新增：单文件 + ZIP 上传弹窗
│       └── ZipPreview.tsx     ← 新增：ZIP 解析预览列表
├── ImageLibrary.tsx           ← 不动
├── AudioLibrary.tsx           ← 不动
└── MaterialDetailDrawer.tsx   ← 不动
```

---

## 四、Store 划分

### 4.1 新增 `productTreeStore.ts`

```typescript
interface ProductNode {
  product: string         // "雪莲洗液" | "通用"
  totalCount: number
  children: SceneRoleNode[]
}

interface SceneRoleNode {
  sceneRole: string       // "医生" | "药材"
  count: number
}

interface ProductTreeState {
  treeNodes: ProductNode[]
  activeProduct: string | null
  activeSceneRole: string | null
  isLoading: boolean

  fetchTree: (tenantKey: string) => Promise<void>
  selectNode: (product: string | null, sceneRole: string | null) => void
  refreshTree: (tenantKey: string) => Promise<void>  // zip 导入后调用
}
```

`MaterialSidebar` 在渲染 Ant Design Tree 时本地构造 `key` / `title`：
```typescript
// 产品节点 key = product 名称
// 场景角色节点 key = `${product}|${sceneRole}`
// title 由组件自行拼接，不依赖后端字符串
```

### 4.2 修改 `materialStore.ts`

- **移除**：`activeCategory`（旧 category 过滤废弃）
- **修改**：`fetchMaterials(tenantKey, product?, sceneRole?)` 新增可选过滤参数
- **新增**：`addMaterials(materials: Material[])` 支持 zip 批量导入后的 optimistic update

---

## 五、上传流程

### 5.1 单文件上传

1. 用户点击「上传」按钮，打开 `UploadModal`（单文件 Tab）
2. 填写：
   - **产品**（必填）：AutoComplete，数据来自 `GET /products?tenant_key=`；支持输入新产品名
   - **场景角色**（选填）：Select，选项为当前产品下已有 scene_role + 预置值（医生/药材/冲洗/产品展示/痛点/美好）；留空合法
3. 若侧边栏已选中节点，打开 Modal 时自动预填对应产品/场景角色
4. 点「开始上传」→ `POST /materials/upload`（带 product + scene_role）
5. Optimistic 添加卡片，轮询状态至 READY，完成后调 `productTreeStore.fetchTree()` 刷新计数

OSS 写入路径：`materials/{tenant_key}/{product}/uploads/{timestamp}_{filename}`
`scene_role` 不影响 OSS 路径，只存 `fc_material.scene_role`。

### 5.2 ZIP 批量上传

ZIP 内部目录格式：`{product}/{scene_role}/{file.mp4}`，支持一层（只有产品）或两层（产品+场景角色）。

流程：
1. 用户在 UploadModal 切换到「ZIP 批量」Tab，选择 .zip 文件
2. `POST /materials/upload-zip` → 后端解压、解析目录结构、生成 `upload_id`
3. 前端渲染 `ZipPreview`：每个目录节点标注「已有」/「新建」/「已忽略」（非视频文件或层级错误）
4. 用户点「确认导入」→ `POST /materials/upload-zip/confirm { upload_id, tenant_key }`
5. 后端批量写 OSS + `fc_material` 记录，提交 MATERIAL_PROCESS 任务
6. 前端调 `productTreeStore.fetchTree()` 刷新（新建节点出现在树中）

归类规则：
- 目录名与已有 product/scene_role 匹配 → 直接放入
- 目录名未匹配 → 自动新建节点
- 非视频文件、层级超过两层 → 跳过，在预览中标注「已忽略」

---

## 六、后端接口变更

> **实现状态说明（2026-05-18 核查）**

| 方法 | 路径 | 状态 | 说明 |
|------|------|------|------|
| GET | `/materials` | ✅ 已实现 | 已有 `product` / `scene_role` 可选查询参数 |
| GET | `/materials/products` | ✅ 已实现 | 返回 distinct 产品列表，供 AutoComplete 用 |
| GET | `/materials/tree` | ❌ 需修改 | 原为 `/materials/tree-summary`（已实现但响应格式需改为结构化字段，同时重命名接口） |
| POST | `/materials/upload` | ❌ 需修改 | OSS key 缺少 product 分层；不接受 `product` / `scene_role` Form 字段 |
| POST | `/materials/upload-zip` | ❌ 需新增 | 解析 zip 目录结构，返回预览 |
| POST | `/materials/upload-zip/confirm` | ❌ 需新增 | 确认导入，触发批量写入 |

### GET /materials/tree 响应格式（结构化字段）

原 `/materials/tree-summary` 重命名为 `/materials/tree`，响应格式同步改为结构化字段：

```json
[
  {
    "product": "雪莲洗液",
    "total_count": 80,
    "children": [
      { "scene_role": "医生", "count": 45 },
      { "scene_role": "药材", "count": 12 }
    ]
  },
  { "product": "通用", "total_count": 15, "children": [] }
]
```

后端改动：将 `tree-summary` 路由的 `key/title` 拼接逻辑替换为直接返回 `product` / `scene_role` / `count` 字段，并在 `__init__.py` 中更新路由注册路径。

### POST /materials/upload 需要的修改

当前实现（需改）：
```python
oss_key = f"materials/{tenant_key}/{int(time.time())}_{filename}"
# 不接受 product / scene_role
```

改后：
```python
# Form 新增参数
product: str = Form(...)         # 必填
scene_role: str | None = Form(None)  # 选填

# OSS key 加入 product 分层
product_dir = product or "通用"
oss_key = f"materials/{tenant_key}/{product_dir}/uploads/{int(time.time())}_{filename}"

# material_repo.create() 传入 product 和 scene_role
material = await container.material_repo.create(
    ...,
    product=product,
    scene_role=scene_role,
)
```

同步需修改：`/materials/upload-token`（presigned PUT 方案）和 `/materials/import-douyin` 两个接口的 OSS key 生成逻辑，补充 `product` 参数。

### POST /materials/upload-zip 响应

```json
{
  "upload_id": "tmp_abc123",
  "preview": [
    {
      "product": "雪莲洗液",
      "scene_role": "医生",
      "files": ["clip_01.mp4", "clip_02.mp4"],
      "status": "existing"
    },
    {
      "product": "雪莲洗液",
      "scene_role": "痛点",
      "files": ["clip_03.mp4"],
      "status": "new"
    },
    {
      "product": null,
      "scene_role": null,
      "files": ["readme.txt"],
      "status": "ignored"
    }
  ]
}
```

---

## 七、前端文件变更清单

### 阶段一：类型 & API 层

| 动作 | 文件 | 变更内容 |
|------|------|---------|
| 修改 | `src/types/index.ts` | `Material` 新增 `product?: string`、`sceneRole?: string`；新增 `ProductNode`、`SceneRoleNode`、`ZipPreviewItem` 类型 |
| 修改 | `src/api/materials.ts` | `uploadMaterial()` 加 product/sceneRole；`listMaterials()` 加可选过滤参数；新增 `uploadZip()`、`confirmZip()` |
| 新增 | `src/api/products.ts` | `getProductTree(tenantKey)` → `ProductNode[]`（调 `/materials/tree`）；`getProducts(tenantKey)` → `string[]`（调 `/materials/products`） |

### 阶段二：Store 层

| 动作 | 文件 | 变更内容 |
|------|------|---------|
| 新增 | `src/stores/productTreeStore.ts` | 完整实现，见 §四 |
| 修改 | `src/stores/materialStore.ts` | 移除 `activeCategory`；`fetchMaterials` 加过滤参数；新增 `addMaterials()` |

### 阶段三：新组件

| 动作 | 文件 | 变更内容 |
|------|------|---------|
| 新增 | `src/components/material/MaterialSidebar.tsx` | Ant Design Tree；节点点击调 `productTreeStore.selectNode()`；数量 badge |
| 新增 | `src/components/material/MaterialSidebar.module.css` | 侧边栏样式 |
| 新增 | `src/components/material/UploadModal.tsx` | 单文件 Tab + ZIP Tab；内嵌 ZipPreview |
| 新增 | `src/components/material/ZipPreview.tsx` | 渲染 ZipPreviewItem 列表，标注状态 |

### 阶段四：改造现有组件

| 动作 | 文件 | 变更内容 |
|------|------|---------|
| 修改 | `src/components/material/MaterialTab.tsx` | 加 Ant Design Sider+Content 布局；挂载 MaterialSidebar；onMount 调 `productTreeStore.fetchTree()` |
| 修改 | `src/components/material/VideoLibrary.tsx` | 移除 FilterChips（旧 category）；从 productTreeStore 读 activeProduct/activeSceneRole；上传按钮改为打开 UploadModal |
| 修改 | `src/components/material/MaterialTab.module.css` | 布局从 flex-column 改为 flex-row；侧边栏宽度 200px |

### 不动的文件

- `MaterialCard.tsx` / `MaterialDetailDrawer.tsx`
- `ImageLibrary.tsx` / `AudioLibrary.tsx`（图片/音频暂不接入产品树）
- `FilterChips.tsx` / `DateGroup.tsx`（通用组件保留）
- `UploadCard.tsx`（可复用为拖拽区底层）
- `creativeStore.ts` / `generateStore.ts` / `detailDrawerStore.ts`

---

## 八、待进一步讨论

- **图片/音频接入产品树**：MVP 只对视频生效，后续迭代可将 ImageLibrary / AudioLibrary 接入同一套 productTreeStore
- **产品节点排序**：当前按 SQL ORDER BY product 字母序；如需自定义排序（如按使用频率、手动拖拽），需后续设计
- **scene_role 预置值维护**：目前为前端硬编码列表（医生/药材/冲洗/产品展示/痛点/美好），后续可改为后端配置
- **ZIP 临时文件清理**：`upload_id` 对应的解压临时目录，后端需设定 TTL（建议 30 分钟）自动清理未 confirm 的上传
