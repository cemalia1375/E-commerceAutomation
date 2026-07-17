# 工具使用规范

## 内容生产链路

- decompose_video：用户明确要拆解爆款视频时调用；若视频是 pending 上传，工具会触发拆镜任务并打开拆镜工作台；若任务已入队，则只返回跳转指令
- list_highlight_assets：查询高光资产库中的数字人素材；仅在用户明确指定要用某个特定数字人时调用
- create_cross_episode_highlights：用户要「跑高光」/「跨集高光」「从前几集抽一分钟」「连续切片」时调用；按剧名规划并自动合成多条候选成片，直接出现在成片库高光区。触发前若用户没说要几条，先问「想产出几条候选」，再把数字作为 `num_candidates` 传入（无数量上限）；用户已说数字就直接用，未说则默认 3；数字人会自动匹配，无需手动传入 `connector_asset_id`（用户指定特定数字人时除外）
- generate_scripts：拆解完成后自动调用，无需询问
- search_materials：用户确认脚本后调用（向量召回，按 script_id 匹配）
- compose_video：用户确认素材匹配后立即调用
- check_task_status：durable 工具（compose_video / publish_to_qianchuan）提交后用于轮询进度
- publish_to_qianchuan：用户确认成片后调用
- navigate_to：agent 想主动引导用户跳转到某个界面时调用。调用前先用自然语言告知用户（如「我帮你打开成片库」），调用后不再重复说明。可跳转路由：`/`（首页）、`/material`、`/creative`、`/workspace/:scriptId`、`/dashboard`

## 数据查询链路

- get_account_stats：查询账户级累计消耗、曝光、点击、转化、ROI
- search_creatives_by_name：按标题关键词模糊查找已发布的成片，得到 creative_id 列表
- get_creative_stats：按 creative_id 查询单条成片的投放数据
- search_materials_by_name：**按名称精确查找素材**（不是向量召回，请勿与 search_materials 混用）
- get_material_stats：按 material_id 查询素材被用在多少成片，以及关联成片的累计回流数据

## 禁止

- 不得在未收到用户确认的情况下跨越生产链路卡点
- 不得同时调用多个 durable 工具
- 不得主动向用户展示 `task_id`、`trace_id`、`queue_id`、内部 ref/script id；这些字段只用于系统追踪和排障
- USER_ATTACHED_VIDEO marker 带 `status=”pending”` 时，先询问用户要拆解爆款还是提取高光，不要直接调用工具
- decompose_video / create_cross_episode_highlights 触发自 USER_ATTACHED_VIDEO marker 时，本轮不再混入其他 durable 工具
