"""FlowCut 后台任务流定义。"""
from __future__ import annotations


class FlowcutTaskStream:
    """FlowCut 后台任务流名称常量。

    每条流对应一个独立的 TaskWorker，职责单一互不干扰。
    """
    MATERIAL_PROCESS         = "flowcut:material_process"          # 素材 ASR + 命名 + 缩略图
    SCENE_DECOMPOSE          = "flowcut:scene_decompose"           # 爆款视频拆镜（Gemini）
    VIDEO_COMPOSE            = "flowcut:video_compose"             # FFmpeg 拼片 + 评估 Agent 循环
    QIANCHUAN_PUBLISH        = "flowcut:qianchuan_publish"         # 素材上传千川 + 创建计划
    QIANCHUAN_SYNC           = "flowcut:qianchuan_sync"            # T+1 数据回流（定时）
    VECTOR_REPAIR            = "flowcut:vector_repair"             # 向量索引修复（扫描未索引行）
    EXPORT_PACKAGE           = "flowcut:export_package"            # 素材打包导出（zip）

    # ── 旧路径（单体 executor，保留向后兼容）──
    HIGHLIGHT_PLAN           = "flowcut:highlight_plan"            # 跨集高光切片规划（单体）

    # ── 新路径（可治理的批量管道）──
    HIGHLIGHT_BATCH          = "flowcut:highlight_batch"           # 编排器（状态机）
    HIGHLIGHT_EPISODE_PREPARE = "flowcut:highlight_episode_prepare" # 单集下载+归一化
    HIGHLIGHT_MERGE_DECOMPOSE = "flowcut:highlight_merge_decompose" # 合并+粗拆镜
    HIGHLIGHT_START_SELECT    = "flowcut:highlight_start_select"    # Gemini 选起点+校验
    HIGHLIGHT_SPAN_PLAN       = "flowcut:highlight_span_plan"       # 单候选细拆+规划
