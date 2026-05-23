# 爆款视频"只产脚本不切片"流程改造设计

- **日期**：2026-05-23
- **范围**：`SimpleClaw/Flowcut/` 后端 + `flowcut_frontend/` 相关交互
- **目标**：简化爆款视频处理链路，去掉子片段切片落库；修复脚本 `copy` 字段为空的 ASR bug；放宽产品选择时机。

---

## 1. 背景与动机

当前流程：
```
上传爆款视频 → 拆镜 → AWAITING_CLASSIFICATION
            → 用户分类 → clip_create_executor 切 OSS 落 fc_material 子片段
```

问题：
1. 拆镜衍生的子片段对真实匹配场景价值低，但占用 OSS、向量库和数据库容量。
2. 用户实际想要的是"每段镜头的视觉描述 + 口播文案"，而不是物理切片。
3. 当前 `scene_data[i].copy` 永远为空，前端拿不到口播。
4. 上传时必须选产品太僵化，用户希望生成脚本后还能改、或者匹配时再选。

---

## 2. 整体方案

### 2.1 新流程

```
POST /reference-videos/upload (product 可空)
   ↓ OSS 路径: uploads/{tenant_key}/{ts}_{filename}
   ↓ INSERT fc_reference_video (status=PROCESSING, product 可空)
   ↓ 投递 scene_decompose 任务
   ↓
scene_decompose_executor:
   ├─ Gemini 视觉拆段 (analyze_video)
   ├─ PySceneDetect 物理切点 → align_timestamps
   ├─ FFmpeg 抽音轨 + ByteDance ASR (开 show_utterances)
   ├─ 按段切 copy → segments = [{idx, start_time, end_time, visual, copy, category}]
   ├─ INSERT fc_script (source=decomposed, product 继承自 ref_video，可空)
   └─ UPDATE fc_reference_video SET status=READY, script_id=<new_id>
```

终态：`fc_reference_video` (元数据 + scene_data_json) + `fc_script` (visual + copy 数组)。不再产生子片段 `fc_material` 行。

### 2.2 废弃项

- 状态：`AWAITING_CLASSIFICATION`、`DECOMPOSED`
- 路由：`POST /reference-videos/{ref_video_id}/classify`
- 执行器：`make_clip_create_executor()` 及其注册
- 流：`FlowcutTaskStream.CLIP_CREATE` 及 `runtime/streams.py` 对应条目
- 由拆镜衍生的 `fc_material` 写入路径（zip / 单文件素材上传路径保留）

---

## 3. Schema 改动

| 表.字段 | 旧 | 新 | 说明 |
|---------|----|----|------|
| `fc_reference_video.status` | `PROCESSING / AWAITING_CLASSIFICATION / DECOMPOSED / FAILED` | `PROCESSING / READY / FAILED` | 拆完即终态 |
| `fc_reference_video.product` | NOT NULL | nullable | 允许"上传时不选产品" |
| `fc_script.product` | NOT NULL | nullable | 继承自 ref_video，允许后续 PATCH |
| `fc_material` | 拆镜会写子片段 | 拆镜不写 | 表本身保留，给 zip / 单文件素材上传用 |

### 存量数据迁移

- `UPDATE fc_reference_video SET status='READY' WHERE status IN ('AWAITING_CLASSIFICATION','DECOMPOSED')`
- 旧拆镜子片段 `fc_material` 行：**保留不删**（可能被 `fc_creative` 引用）；新代码不再生产这种行，自然沉淀。

迁移在 `storage/database.py:ensure_schema()` 中以幂等 `ALTER TABLE` + 一次性 UPDATE 完成。

---

## 4. OSS Key 规范

- 爆款视频上传新前缀：`uploads/{tenant_key}/{ts}_{filename}`
- 跟 `materials/...` 完全隔离，互不污染。
- 抽出的音轨仍写 `reference_videos/{tenant_key}/{ref_video_id}/audio.mp3`（保持不变）。

---

## 5. ASR 词级时间戳修复

**位置**：`Flowcut/runtime/executors.py:135-139`，`_call_asr_websocket_with_words` 的 config_payload。

**改动**：在 `request` 字段中加入 `"show_utterances": true`。

修复后 ASR 返回 `result.utterances[].words[]`，现有 `_slice_words_for_segment` 解析逻辑已匹配此格式，无需改动。

**验证方法**：本地用一个 5s 中文口播视频跑 `scene_decompose`，确认 `fc_script.segments_json[i].copy` 不再为空。

---

## 6. 产品选择（方案 C：两端都允许）

### 6.1 上传时（可选）

- `POST /reference-videos/upload` 和 `POST /reference-videos/presigned-upload` 的 `product` Form/Body 字段：**改为可选**，不传或传空字符串均接受。
- 写入 `fc_reference_video.product` 时：空字符串视为 NULL。

### 6.2 拆镜后改产品（新增接口）

```
POST /scripts/{script_id}/update-product
Body: {"product": "<产品名>"}
```

后端执行 `UPDATE fc_script SET product=? WHERE id=?`。无业务副作用。

### 6.3 素材匹配时（`search_materials` 工具）

调用约定（在工具入口处实现）：

1. 若工具调用方显式传 `product` → 直接用。
2. 否则从 `fc_script.product` 取默认。
3. 若两者都没有 → 工具返回错误 `请先为脚本选择产品`，让上层 Agent 引导用户选。

---

## 7. 受影响文件清单

**后端：**
- `Flowcut/storage/database.py` — schema 迁移 + 状态枚举更新
- `Flowcut/storage/reference_video_repo.py` — 移除 `update_scene_data_and_product`（如不再使用）
- `Flowcut/storage/script_repo.py` — 新增 `update_product()` 方法
- `Flowcut/runtime/executors.py` — `_call_asr_websocket_with_words` 加 `show_utterances`；`make_scene_decompose_executor` 终态改 `READY`；删除 `make_clip_create_executor`
- `Flowcut/runtime/streams.py` — 删除 `CLIP_CREATE` 枚举
- `Flowcut/runtime/worker.py` / `make_workers()` — 删除 clip_create worker 注册
- `Flowcut/api/routes/reference_videos.py` — OSS key 改 `uploads/...`；`product` 字段改可选；删除 `POST /{ref_video_id}/classify` 路由
- `Flowcut/api/routes/scripts.py`（新建或扩展）— 新增 `POST /scripts/{id}/update-product`
- `Flowcut/tools/search_materials.py` — 实现 product 三段式回退
- `Flowcut/tools/decompose_video.py` — 不再触发 clip_create 后续步骤（如有相关引用）

**前端：**
- `flowcut_frontend/src/stores/referenceVideoStore.ts`（或类似）— 上传弹窗 product 改为可选；移除"分类确认"步骤
- 脚本详情页 — 新增"修改产品"按钮，对接 `/scripts/{id}/update-product`

---

## 8. 测试计划

| 类型 | 用例 |
|------|------|
| unit | `_slice_words_for_segment` 在词级数据存在时切片正确 |
| unit | `search_materials` product 三段式回退逻辑 |
| integration | 上传爆款视频（不带 product）→ 拆镜 → fc_script.product 为 NULL、segments 含非空 copy |
| integration | `POST /scripts/{id}/update-product` 成功更新 |
| integration | schema 迁移幂等性：重复跑 ensure_schema 不报错 |
| e2e | 完整链路：上传无 product → 拆镜 → 改 product → 触发 search_materials → 返回素材列表 |

---

## 9. 非目标

- 不改 `fc_creative` 表结构和成片流程。
- 不改 zip / 单文件素材上传流程。
- 不删除 OSS 上已有的子片段文件（手动清理另议）。
- 不改千川发布相关逻辑。
