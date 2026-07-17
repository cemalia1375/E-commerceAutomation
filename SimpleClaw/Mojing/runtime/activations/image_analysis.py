from __future__ import annotations

import time

from Mojing.runtime.activations.models import ActivationRequest


def build_image_analysis_completion_activation(
    *,
    tenant_key: str,
    source_session_key: str,
    task_id: str,
    profile_id: str = "",
    expires_after_s: int = 600,
) -> ActivationRequest | None:
    tenant_key = str(tenant_key or "").strip()
    task_id = str(task_id or "").strip()
    if not tenant_key or not task_id:
        return None

    profile_id = str(profile_id or "").strip()
    expires_at_ms = int(time.time() * 1000) + max(60, int(expires_after_s)) * 1000
    return ActivationRequest(
        session_key=f"main:{tenant_key}",
        tenant_key=tenant_key,
        activation_kind="image_analysis_completion",
        task_id=task_id,
        summary="图片分析已完成，新的肤况信息已同步",
        reminder_text=(
            "系统触发：刚刚这次图片分析已经完成，新的肤况信息也同步好了。"
            "只做图片分析完成的状态确认，自然告诉用户图片分析好了。"
            "可以结合已同步到 USER.md 的 Learned Skin Profile，轻量同步一两点最重要的肤况内容。"
            "不要调用任何工具，不要创建新任务，不要主动追加新的分析或护理流程。"
        ),
        source_session_key=source_session_key,
        business_ref_type="tenant_skin_profile",
        business_ref_id=profile_id or None,
        expires_at_ms=expires_at_ms,
        dedupe_key=f"image_analysis_completion:{tenant_key}:{task_id}",
    )
