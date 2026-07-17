from __future__ import annotations

from Mojing.runtime.activations.models import ActivationRequest
from Mojing.runtime.task_types import MojingTaskType


def build_runtime_task_failure_activation(
    *,
    tenant_key: str,
    source_session_key: str,
    task_id: str,
    task_type: str,
    error: str = "",
    business_ref_type: str | None = None,
    business_ref_id: str | None = None,
) -> ActivationRequest | None:
    tenant_key = str(tenant_key or "").strip()
    task_id = str(task_id or "").strip()
    task_type = str(task_type or "").strip()
    if not tenant_key or not task_id:
        return None

    summary, reminder_text = _failure_copy(task_type, error)
    if not reminder_text:
        return None

    return ActivationRequest(
        session_key=f"main:{tenant_key}",
        tenant_key=tenant_key,
        activation_kind=f"{task_type}_failure",
        task_id=task_id,
        summary=summary,
        reminder_text=reminder_text,
        source_session_key=source_session_key,
        business_ref_type=business_ref_type,
        business_ref_id=business_ref_id,
        dedupe_key=f"runtime_task_failure:{tenant_key}:{task_id}",
    )


def _failure_copy(task_type: str, error: str) -> tuple[str, str]:
    del error
    if task_type == MojingTaskType.SKIN_DIARY_GENERATION:
        return (
            "肌肤日记生成失败，可告知用户当前状态",
            (
                "系统触发：刚刚这次肌肤日记没有生成成功。"
                "只做肌肤日记失败的状态确认，自然告诉用户这次没有成功；可以说明用户如果需要，之后可以再让你重新生成。"
                "不要提图片分析、深度分析报告或其他任务的完成/处理中/失败状态，不要作阶段汇总。"
                "不要调用任何工具，不要创建新任务，不要说已经完成。"
            ),
        )
    if task_type == MojingTaskType.IMAGE_ANALYSIS:
        return (
            "图片分析失败，可告知用户当前状态",
            (
                "系统触发：刚刚这次图片分析断掉了，没有拿到结果。"
                "只做图片分析失败的状态确认，自然告诉用户这次没有成功；可以说明用户如果愿意，之后可以用刚才那张照片重新分析一次。"
                "不要提肌肤日记、深度分析报告或其他任务的完成/处理中/失败状态，不要作阶段汇总。"
                "只有当照片明显不可用或用户想换图时，才引导重新上传清晰正脸照。"
                "不要调用任何工具，不要创建新任务，不要说还在分析中。"
            ),
        )
    if task_type == MojingTaskType.DEEP_RESEARCH:
        return (
            "深度分析报告生成失败，可告知用户当前状态",
            (
                "系统触发：刚刚这次深度分析报告没有生成成功。"
                "只做深度分析报告失败的状态确认，自然告诉用户这次没有成功；可以说明用户如果需要，之后可以再让你重新生成。"
                "不要提图片分析、肌肤日记或其他任务的完成/处理中/失败状态，不要作阶段汇总。"
                "不要调用任何工具，不要创建新任务，不要说还在生成中，也不要编造报告内容。"
            ),
        )
    if task_type == MojingTaskType.CABINET_PRODUCT_RESEARCH:
        return (
            "产品资料调研失败，可告知用户当前状态",
            (
                "系统触发：刚刚这次产品资料调研没有整理成功。"
                "只做产品调研失败的状态确认，自然告诉用户这次没有拿到完整资料；可以说明用户如果需要，之后可以让你重新调研一次。"
                "不要说还在调研中，不要编造产品成分、功效或适配结论，不要说已经入柜。"
                "不要调用任何工具，不要创建新任务，不要提图片分析、肌肤日记或深度分析报告状态。"
            ),
        )
    return "", ""
