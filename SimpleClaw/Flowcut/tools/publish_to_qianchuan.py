"""上传成片到千川 + 创建全域推广计划。"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from simpleclaw.runtime.task_protocol import TaskEnvelope
from simpleclaw.tools.base import Tool

from Flowcut.runtime.streams import FlowcutTaskStream

if TYPE_CHECKING:
    from simpleclaw.runtime.services import RuntimeServices


class PublishToQianchuanTool(Tool):
    """将成片上传到千川广告平台并创建全域推广计划。"""

    name = "publish_to_qianchuan"
    description = (
        "将已审核通过的成片（status=READY）上传到抖音千川广告平台，"
        "并自动创建全域推广计划。任务异步执行，调用后立即返回 task_id，"
        "可用 check_task_status 查进度。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "creative_id": {
                "type": "integer",
                "description": "要上架的成片 ID（status=READY）",
            },
            "title": {
                "type": "string",
                "description": "千川广告标题",
            },
            "tenant_key": {
                "type": "string",
                "description": "租户 key（多账号场景区分用）",
            },
        },
        "required": ["creative_id", "title", "tenant_key"],
    }
    execution_mode = "durable"
    needs_followup = True

    def __init__(self, *, runtime: "RuntimeServices") -> None:
        self._runtime = runtime

    async def prepare_task(
        self,
        creative_id: int,
        title: str,
        tenant_key: str,
        **kwargs: Any,
    ) -> TaskEnvelope:
        """构造 QIANCHUAN_PUBLISH TaskEnvelope。

        校验放在 executor 里（需要 DB 访问）；这里只组装信封。

        scope_key 用 ``qc_publish:{creative_id}`` 保证同一成片不会被并发投放两次。
        """
        if not title.strip():
            raise ValueError("title 不能为空")

        task_id = f"qc-publish-{uuid.uuid4().hex[:12]}"
        return TaskEnvelope(
            task_id=task_id,
            task_type="qianchuan_publish",
            tenant_key=tenant_key,
            stream=FlowcutTaskStream.QIANCHUAN_PUBLISH,
            scope_key=f"qc_publish:{creative_id}",
            payload={
                "creative_id": creative_id,
                "title": title.strip(),
            },
        )
