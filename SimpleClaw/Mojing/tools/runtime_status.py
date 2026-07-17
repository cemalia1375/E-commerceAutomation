"""Runtime-status query tool for Mojing async business tasks."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from simpleclaw.tools.base import Tool, ToolResult
from Mojing.harness.readiness.base import ACTIVE_STATUSES, normalize_status, stringify_time
from Mojing.runtime.task_types import MojingTaskType


class CheckRuntimeStatusTool(Tool):
    """Read recent async task progress for the current user."""

    name = "check_runtime_status"
    description = "查询当前用户最近异步任务的进度或结果状态。这是只读工具，不会触发新任务。"
    parameters = {"type": "object", "properties": {}, "required": ["target"]}

    execution_mode = "inline"
    needs_followup = True
    tool_category = "sync_read"

    def __init__(
        self,
        *,
        runtime_task_repo,
        image_repo=None,
        include_deep_report: bool = True,
        include_skin_diary: bool = True,
    ) -> None:
        self._runtime_task_repo = runtime_task_repo
        self._image_repo = image_repo
        self._tenant_key = "__default__"
        self._session_key = ""
        self._targets = _runtime_status_targets(
            include_deep_report=include_deep_report,
            include_skin_diary=include_skin_diary,
        )
        self._allowed_targets = set(self._targets)
        self.description = _runtime_status_description(
            include_deep_report=include_deep_report,
            include_skin_diary=include_skin_diary,
        )
        self.parameters = _runtime_status_parameters(self._targets)

    def set_context(self, *, tenant_key: str = "", session_key: str = "", **_: Any) -> None:
        if tenant_key:
            self._tenant_key = tenant_key
        if session_key:
            self._session_key = session_key

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        target = str(params.get("target") or "").strip()
        if target not in self._allowed_targets:
            return [f"target must be one of {'/'.join(self._targets)}"]
        return []

    async def execute(self, target: str = "all_recent") -> ToolResult:
        tenant_key = str(self._tenant_key or "").strip()
        target = str(target or "all_recent").strip()
        if target not in self._allowed_targets:
            return _json_result({
                "ok": False,
                "target": target,
                "status": "unsupported_target",
                "user_visible_summary": "我现在还不能查询这个状态。",
                "model_guidance": "按当前阶段可见能力回答，不要编造任务结果。",
            }, ok=False)
        if not tenant_key or tenant_key == "__default__":
            return _json_result({
                "ok": False,
                "target": target,
                "status": "missing_context",
                "user_visible_summary": "我现在没法定位到你的任务状态。",
                "model_guidance": "请简短说明暂时查不到状态，不要编造任务结果。",
            }, ok=False)

        if target == "all_recent":
            payload = await self._all_recent_status(tenant_key)
        else:
            payload = await self._target_status(tenant_key, target)
        return _json_result(payload)

    async def _target_status(self, tenant_key: str, target: str) -> dict[str, Any]:
        task_type = _target_task_type(target)
        latest_task = await self._latest_task(tenant_key, task_type)
        if latest_task is None:
            return {
                "ok": True,
                "target": target,
                "status": "empty",
                "latest_task": None,
                "user_visible_summary": _empty_summary(target),
                "model_guidance": _empty_guidance(target),
            }

        status = _status_of(latest_task) or "unknown"
        return {
            "ok": True,
            "target": target,
            "status": status,
            "latest_task": _task_view(latest_task),
            "user_visible_summary": _task_status_summary(task_type, status),
            "model_guidance": _task_status_guidance(task_type, status),
        }

    async def _all_recent_status(self, tenant_key: str) -> dict[str, Any]:
        tasks = []
        if self._runtime_task_repo is not None:
            tasks = await self._runtime_task_repo.list_recent(tenant_key=tenant_key, limit=8)
        business_tasks = [_runtime_business_task_view(task) for task in tasks if _is_business_task(task)]
        business_tasks = [task for task in business_tasks if task]
        focus_task = business_tasks[0] if business_tasks else None
        return {
            "ok": True,
            "target": "all_recent",
            "status": "available" if business_tasks else "empty",
            "focus_task": focus_task,
            "recent_tasks": business_tasks[:3],
            "user_visible_summary": _all_recent_user_summary(focus_task),
            "model_guidance": (
                _task_status_guidance(
                    str((focus_task or {}).get("task_type") or "").strip(),
                    normalize_status((focus_task or {}).get("status")),
                ) if focus_task else
                "告诉用户暂时没有查到正在处理或刚完成的任务，不要编造结果。"
            ),
        }

    async def _latest_task(self, tenant_key: str, task_type: str) -> dict[str, Any] | None:
        if self._runtime_task_repo is None:
            return None
        return await self._runtime_task_repo.find_latest_task_for(
            tenant_key=tenant_key,
            task_type=str(task_type),
        )

def _runtime_status_targets(*, include_deep_report: bool, include_skin_diary: bool) -> list[str]:
    targets = ["image_analysis", "cabinet_product_research"]
    if include_deep_report:
        targets.append("deep_report")
    if include_skin_diary:
        targets.append("skin_diary")
    targets.append("all_recent")
    return targets


def _runtime_status_description(*, include_deep_report: bool, include_skin_diary: bool) -> str:
    subjects = ["图片分析", "产品资料调研"]
    if include_deep_report:
        subjects.append("深度报告")
    if include_skin_diary:
        subjects.append("肌肤日记")
    subject_text = "、".join(subjects)
    return (
        "查询当前用户最近异步任务的进度或结果状态。"
        f"当用户询问{subject_text}是否完成、失败、还要多久或在哪里看时使用。"
        "当用户说好了没、现在完成了吗、生成了吗、同步了吗、记录好了吗、出来了吗、进度怎么样时必须使用。"
        "不要根据历史对话、记忆或上一轮进行态话术猜状态。"
        "这是只读工具，只基于 runtime task 事实回答，不会触发新任务。"
    )


def _runtime_status_parameters(targets: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "enum": list(targets),
                "description": "要查询的异步任务类型。",
            }
        },
        "required": ["target"],
    }


def _json_result(payload: dict[str, Any], *, ok: bool = True) -> ToolResult:
    return ToolResult(content=json.dumps(payload, ensure_ascii=False, default=str), ok=ok)


def _status_of(task: dict[str, Any] | None) -> str:
    return normalize_status((task or {}).get("status"))


def _task_view(task: dict[str, Any] | None) -> dict[str, Any] | None:
    if not task:
        return None
    payload = dict(task.get("payload") or {})
    view = {
        "task_id": task.get("task_id"),
        "task_type": task.get("task_type"),
        "status": task.get("status"),
        "scope_key": task.get("scope_key"),
        "business_ref_type": task.get("business_ref_type"),
        "business_ref_id": task.get("business_ref_id"),
        "created_at": _fmt(task.get("created_at")),
        "updated_at": _fmt(task.get("updated_at")),
        "completed_at": _fmt(task.get("completed_at")),
        "last_error": task.get("last_error"),
    }
    if str(task.get("task_type") or "").strip() == MojingTaskType.IMAGE_ANALYSIS:
        view["image_analysis_ref"] = {
            "job_id": payload.get("job_id"),
            "image_id": payload.get("image_id"),
            "source": payload.get("source"),
        }
    return view


def _is_business_task(task: dict[str, Any] | None) -> bool:
    if not task:
        return False
    task_type = str(task.get("task_type") or "").strip()
    return task_type not in {"postprocess", "structured_memory", "obligation_extract", "memory_extract"}


def _runtime_business_task_view(task: dict[str, Any] | None) -> dict[str, Any] | None:
    if not task:
        return None
    return {
        "task_type": task.get("task_type"),
        "status": normalize_status(task.get("status")),
        "last_error": task.get("last_error"),
    }


def _target_task_type(target: str) -> str:
    if target == "image_analysis":
        return MojingTaskType.IMAGE_ANALYSIS
    if target == "cabinet_product_research":
        return MojingTaskType.CABINET_PRODUCT_RESEARCH
    if target == "deep_report":
        return MojingTaskType.DEEP_RESEARCH
    if target == "skin_diary":
        return MojingTaskType.SKIN_DIARY_GENERATION
    raise ValueError(f"unsupported runtime-status target: {target}")


def _empty_summary(target: str) -> str:
    if target == "image_analysis":
        return "还没有查到图片分析任务。"
    if target == "cabinet_product_research":
        return "还没有查到产品资料调研任务。"
    if target == "deep_report":
        return "还没有查到深度报告任务。"
    if target == "skin_diary":
        return "还没有查到肌肤日记任务。"
    return "我这边暂时没查到最近的后台任务。"


def _empty_guidance(target: str) -> str:
    if target == "image_analysis":
        return "说明当前没有图片分析任务；如果用户想看肤况，请温柔引导上传清晰正脸照。"
    if target == "cabinet_product_research":
        return "说明当前没有查到产品资料调研任务；不要编造产品资料，也不要因为用户问进度就重新触发。"
    if target == "deep_report":
        return "说明当前没有查到深度报告任务；不要编造报告结果，不要因为用户问进度就重新触发。"
    if target == "skin_diary":
        return "说明当前没有查到肌肤日记任务；不要编造日记结果，不要因为用户问进度就重新触发。"
    return "告诉用户暂时没有查到正在处理或刚完成的任务，不要编造结果。"


def _all_recent_user_summary(focus_task: dict[str, Any] | None) -> str:
    if not focus_task:
        return "我这边暂时没查到最近的后台任务。"
    task_type = str(focus_task.get("task_type") or "").strip()
    status = normalize_status(focus_task.get("status"))
    return _task_status_summary(task_type, status)


def _task_status_summary(task_type: str, status: str) -> str:
    task_type = str(task_type or "").strip()
    status = normalize_status(status)
    if task_type == MojingTaskType.CABINET_PRODUCT_RESEARCH:
        if status in ACTIVE_STATUSES:
            return "产品资料调研还在处理中。"
        if status == "failed":
            return "刚刚这次产品资料调研没有成功，没有拿到结果。"
        if status == "succeeded":
            return "产品资料调研已经完成。"
    if task_type == MojingTaskType.IMAGE_ANALYSIS:
        if status in ACTIVE_STATUSES:
            return "图片分析还在处理中。"
        if status == "failed":
            return "刚刚这次图片分析没有成功，没有拿到结果。"
        if status == "succeeded":
            return "图片分析已经完成。"
    if task_type == MojingTaskType.DEEP_RESEARCH:
        if status in ACTIVE_STATUSES:
            return "深度报告还在生成中。"
        if status == "failed":
            return "刚刚这次深度报告生成没有成功，没有拿到结果。"
        if status == "succeeded":
            return "深度报告已经生成。"
    if task_type == MojingTaskType.SKIN_DIARY_GENERATION:
        if status in ACTIVE_STATUSES:
            return "肌肤日记还在生成中。"
        if status == "failed":
            return "刚刚这次肌肤日记生成没有成功，没有生成新版日记。"
        if status == "succeeded":
            return "肌肤日记已经生成。"
    if task_type == MojingTaskType.SUBAGENT_DISPATCH:
        if status in ACTIVE_STATUSES:
            return "相关助手任务还在处理中。"
        if status == "failed":
            return "刚刚这次助手处理没有成功，没有拿到结果。"
        if status == "succeeded":
            return "相关助手任务已经完成。"
    return "我查到了最近任务。"


def _task_status_guidance(task_type: str, status: str) -> str:
    task_type = str(task_type or "").strip()
    status = normalize_status(status)
    if status in ACTIVE_STATUSES:
        return "告诉用户任务还在处理中，不要编造结果，也不要重复触发同类任务。"
    if status == "failed":
        if task_type == MojingTaskType.CABINET_PRODUCT_RESEARCH:
            return "告诉用户刚刚产品资料调研没有成功，没有拿到结果；不要说还在处理中，不要编造产品结论；如果用户还想继续查这款产品，轻问是否要重新试一次。"
        if task_type == MojingTaskType.IMAGE_ANALYSIS:
            return (
                "告诉用户刚刚图片分析断掉了，没有拿到结果；不要说还在分析中，不要编造肤况结论；"
                "如果她愿意，可以用刚才那张照片重新分析一次。只有照片明显不可用或用户想换图时，才引导重新上传清晰正脸照。"
            )
        if task_type == MojingTaskType.DEEP_RESEARCH:
            return "告诉用户刚刚深度报告生成没有成功，没有拿到结果；不要说还在生成中，不要编造报告内容；如果用户还需要，轻问是否要重新生成一次。"
        if task_type == MojingTaskType.SKIN_DIARY_GENERATION:
            return "告诉用户刚刚肌肤日记生成没有成功，没有生成新版日记；不要说还在生成中，不要编造日记内容；如果用户还需要，轻问是否要重新生成一次。"
        return "告诉用户刚刚这次处理没有成功，没有拿到结果；不要说还在进行中，不要编造结果；如果用户还需要，轻问是否要重新试一次。"
    if status == "succeeded":
        if task_type == MojingTaskType.CABINET_PRODUCT_RESEARCH:
            return "说明产品资料调研已经完成；如果当前还在聊这款产品，后续是否入柜应交给护肤柜场景流程处理。"
        if task_type == MojingTaskType.IMAGE_ANALYSIS:
            return "说明这条图片分析任务已经完成；不要把它当作最新图片状态，也不要据此判断肌肤日记或深度报告已经完成。"
        if task_type == MojingTaskType.DEEP_RESEARCH:
            return "说明深度报告已经生成；可引导用户去深度分析报告会话或报告页查看。"
        if task_type == MojingTaskType.SKIN_DIARY_GENERATION:
            return "说明肌肤日记已经生成；可引导用户去肌肤日记页面查看。"
        if task_type == MojingTaskType.SUBAGENT_DISPATCH:
            return "说明相关助手任务已经完成；根据当前语境自然衔接下一步，不要编造未发生的业务动作。"
        return "根据任务已完成的事实简短回答，不要暴露内部字段。"
    return "根据 status 简短回答，不要暴露内部字段。"


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return stringify_time(value) or str(value)
