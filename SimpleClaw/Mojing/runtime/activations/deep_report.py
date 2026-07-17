from __future__ import annotations

from Mojing.runtime.activations.models import ActivationRequest


def build_deep_report_completion_activation(
    *,
    tenant_key: str,
    source_session_key: str,
    task_id: str,
    report_id: str = "",
) -> ActivationRequest | None:
    tenant_key = str(tenant_key or "").strip()
    task_id = str(task_id or "").strip()
    if not tenant_key or not task_id:
        return None

    report_id = str(report_id or "").strip()
    return ActivationRequest(
        session_key=f"main:{tenant_key}",
        tenant_key=tenant_key,
        activation_kind="deep_report_completion",
        task_id=task_id,
        summary="深度分析报告已生成，可提醒用户查看结果",
        reminder_text=(
            "系统触发：深度分析报告已经生成完成。"
            "只做深度分析报告完成的状态确认，提醒用户可前往「我的报告」页面或深度分析报告会话查看结果。"
            "不要提图片分析、肌肤日记或其他任务的完成/处理中/失败状态，不要作阶段汇总。"
            "不要调用任何工具，不要创建新任务，不要主动追加新的分析流程。"
        ),
        source_session_key=source_session_key,
        business_ref_type="deep_report",
        business_ref_id=report_id or None,
        dedupe_key=f"deep_report_completion:{tenant_key}:{task_id}",
    )
