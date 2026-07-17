from __future__ import annotations

from Mojing.runtime.activations.models import ActivationRequest


def build_skin_diary_completion_activation(
    *,
    tenant_key: str,
    source_session_key: str,
    task_id: str,
    result_id: str,
) -> ActivationRequest:
    return ActivationRequest(
        session_key=f"main:{tenant_key}",
        tenant_key=tenant_key,
        activation_kind="skin_diary_completion",
        task_id=task_id,
        summary="肌肤日记已生成，可提醒用户查看今日护肤计划",
        reminder_text=(
            "系统触发：肌肤日记已经生成完成。"
            "只做肌肤日记完成的状态确认，提醒用户可前往肌肤日记页面查看今日护肤计划或这次更新结果。"
            "不要提图片分析、深度分析报告或其他任务的完成/处理中/失败状态，不要作阶段汇总。"
            "不要调用任何工具，不要创建新任务，不要主动追加新的护理计划生成流程。"
        ),
        source_session_key=source_session_key,
        business_ref_type="skin_diary_result",
        business_ref_id=result_id,
        dedupe_key=f"skin_diary_completion:{tenant_key}:{task_id}",
    )
