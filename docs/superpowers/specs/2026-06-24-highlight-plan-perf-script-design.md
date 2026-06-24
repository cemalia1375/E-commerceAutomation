# 高光规划性能测试脚本设计

**日期：** 2026-06-24  
**目标：** 找出「跨集高光切片规划」全流程的耗时瓶颈

---

## 背景

`create_cross_episode_highlights` 工具触发 `highlight_plan` 任务，
执行路径：`make_highlight_plan_executor`（`Flowcut/runtime/executors.py`）。
该 executor 包含多次 OSS 下载、ffmpeg 重编码、Gemini 多模态调用，
但目前没有任何计时埋点，无法判断瓶颈在哪一步。

---

## 方案

创建独立脚本 `Flowcut/scripts/perf_highlight_plan.py`：

- **不修改 production 代码**
- 直接 import 真实服务函数（`analyze_video`、`detect_scene_cuts`、`select_start_shots` 等）
- 复现 `make_highlight_plan_executor` 的三个 Stage，在每个阶段边界打时间戳
- 写入真实 DB（创建 creative 记录）+ 真实 OSS，与 B 方案等价
- 不走 TaskQueue，方便单次定向测量

### 运行方式

```bash
uv run python -m Flowcut.scripts.perf_highlight_plan <drama_name> [--candidates N]
```

---

## 计时粒度（粗粒度三段）

### Stage A：下载 + 归一化 + 合并 + Gemini 整体拆镜

取前 3 集（`START_SEARCH_EPISODES`），每集分别计时：

| 子步骤 | 字段 |
|--------|------|
| OSS 下载 | `download_s` |
| ffmpeg 归一化（重编码统一格式） | `normalize_s` |
| ffmpeg concat 合并 | `ffmpeg_merge_s` |
| Gemini `analyze_video`（并行）| `gemini_analyze_s` |
| `detect_scene_cuts`（并行）| `detect_scene_cuts_s` |

**注：** 归一化是每集完整重编码（libx264），是 Stage A 最可能的耗时大头。

### Stage B：select_start_shots

| 子步骤 | 字段 |
|--------|------|
| Gemini 文本判断起点 | `select_start_shots_s` |
| 产出候选数 | `candidates_picked` |

### Stage C：每个候选的 span 细拆 + 合成提交

对每个候选分别计时：

| 子步骤 | 字段 |
|--------|------|
| 下载新集（复用已下载集不重复） | `download_new_episodes_s` |
| ffmpeg cut + concat 拼 span | `ffmpeg_cut_concat_s` |
| Gemini `analyze_video` span（并行）| `gemini_span_analyze_s` |
| `detect_scene_cuts` span（并行）| `detect_scene_cuts_s` |
| `pick_end_boundary` | `pick_end_boundary_s` |
| `creative_repo.create_cross_episode_job` | `creative_repo_write_s` |
| `runtime.submit_task`（compose 入队）| `compose_submit_s` |

---

## 输出格式

脚本结束后写 JSON 文件：`perf_highlight_<drama>_<timestamp>.json`

```json
{
  "drama_name": "示例剧名",
  "num_candidates_requested": 3,
  "total_elapsed_s": 118.4,
  "stage_A": {
    "elapsed_s": 75.2,
    "per_episode": [
      {"episode_no": 1, "download_s": 8.1, "normalize_s": 12.3},
      {"episode_no": 2, "download_s": 7.5, "normalize_s": 11.9}
    ],
    "ffmpeg_merge_s": 0.4,
    "gemini_analyze_s": 28.6,
    "detect_scene_cuts_s": 2.1,
    "total_shots": 42
  },
  "stage_B": {
    "elapsed_s": 5.1,
    "select_start_shots_s": 5.1,
    "candidates_picked": 3
  },
  "stage_C": {
    "elapsed_s": 38.1,
    "candidates": [
      {
        "episode_no": 1,
        "local_start": 23.5,
        "download_new_episodes_s": 5.9,
        "ffmpeg_cut_concat_s": 3.1,
        "gemini_span_analyze_s": 20.1,
        "detect_scene_cuts_s": 1.7,
        "pick_end_boundary_s": 0.01,
        "creative_repo_write_s": 0.08,
        "compose_submit_s": 0.02,
        "creative_id": 42
      }
    ]
  }
}
```

---

## 依赖

脚本通过 `Flowcut/api/container.py` 的 `build_container()` 或直接实例化 repo/service，
复用现有的 DB pool、OSS client、Gemini client，无需额外配置。
环境变量要求与正常启动 Flowcut 服务一致（`.env` 文件）。
