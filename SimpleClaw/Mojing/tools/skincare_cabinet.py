"""护肤柜产品调研与落柜工具。"""

from __future__ import annotations

from typing import Any

from loguru import logger

from simpleclaw.runtime.task_protocol import TaskEnvelope
from simpleclaw.tools.base import Tool, ToolResult
from Mojing.runtime.streams import MojingTaskStream
from Mojing.runtime.task_types import MojingTaskType
from Mojing.runtime.tool_results import json_tool_result, tool_no_change, tool_submitted
from Mojing.storage.image_repo import ImageRepository
from Mojing.storage.runtime_task_repo import RuntimeTaskRepository
from Mojing.storage.skincare_cabinet_repo import SkincareCabinetRepository


def _normalize_usage_status(value: str | None) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text in {"using", "unopened", "finished"}:
        return text
    aliases = {
        "opened": "using",
        "open": "using",
        "used": "using",
    }
    return aliases.get(text)


def _tenant_user_id(tenant_key: str) -> str:
    text = str(tenant_key or "").strip()
    if not text:
        raise ValueError("tenant_key is required")
    return text


def _research_scope_key(*, tenant_key: str, brand: str, product_name: str) -> str:
    return f"{MojingTaskType.CABINET_PRODUCT_RESEARCH}:{tenant_key}:{brand}:{product_name}"


class ResearchSkincareProductTool(Tool):
    """按已确认的品牌/产品名发起产品资料导入调研。"""

    name = "research_skincare_product"
    description = (
        "当某款产品的身份已经明确，并且你已经判断需要继续查这款产品资料时调用。"
        "优先在 lookup_skincare_cabinet_product_status 返回 not_found，"
        "且用户明确表示希望继续调研这款产品时调用。"
        "如果只是确认了产品，或者用户只是回答了开封/使用状态，不要直接调用本工具。"
        "本工具只会异步发起资料调研并以未落柜状态保存；submitted/queued/wait_external 仅表示任务已开始，不表示资料已完成，也不表示现在可以入柜。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "brand": {"type": "string", "description": "用户已确认的产品品牌"},
            "product_name": {"type": "string", "description": "用户已确认的产品名称"},
            "usage_status": {
                "type": "string",
                "enum": ["using", "unopened", "finished"],
                "description": "用户当前这支产品的状态；未确认时可省略",
            },
        },
        "required": ["brand", "product_name"],
    }

    needs_followup = True
    execution_mode = "durable"
    tool_category = "async_task"
    durable_action = "submitted"

    def __init__(
        self,
        *,
        image_repo: ImageRepository,
    ) -> None:
        self._image_repo = image_repo
        self._tenant_key = "__default__"
        self._session_key = "cli:direct"
        self._origin_session_key = ""
        self._media: list[str] = []

    def set_context(
        self,
        *,
        tenant_key: str = "",
        session_key: str = "",
        origin_session_key: str = "",
        media: list[str] | None = None,
        **_: Any,
    ) -> None:
        if tenant_key:
            self._tenant_key = tenant_key
        if session_key:
            self._session_key = session_key
        self._origin_session_key = str(origin_session_key or "").strip()
        self._media = list(media or [])

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not str(params.get("brand") or "").strip():
            errors.append("brand is required")
        if not str(params.get("product_name") or "").strip():
            errors.append("product_name is required")
        usage = params.get("usage_status")
        if usage is not None and _normalize_usage_status(str(usage)) is None:
            errors.append("usage_status must be one of using/unopened/finished")
        return errors

    async def prepare_task(self, *, brand: str, product_name: str, usage_status: str | None = None) -> TaskEnvelope | ToolResult:
        try:
            user_id = _tenant_user_id(self._tenant_key)
        except ValueError as exc:
            return ToolResult(content=f"Error: {exc}", ok=False)
        if user_id == "__default__":
            return ToolResult(
                content=(
                    "Error: research_skincare_product missing tenant context; "
                    "durable product research was not submitted."
                ),
                ok=False,
            )
        if not str(self._session_key or "").strip() or self._session_key == "cli:direct":
            return ToolResult(
                content=(
                    "Error: research_skincare_product missing session context; "
                    "durable product research was not submitted."
                ),
                ok=False,
            )

        image_url = ""
        if self._media:
            image_url = str(self._media[-1] or "").strip()
        if not image_url:
            image_url = await self._image_repo.get_latest(self._tenant_key) or ""

        payload = {
            "userId": user_id,
            "brand": str(brand or "").strip(),
            "productName": str(product_name or "").strip(),
            "imageUrl": image_url,
        }
        usage = _normalize_usage_status(usage_status)
        if usage:
            payload["usage_status"] = usage

        return TaskEnvelope(
            task_type=MojingTaskType.CABINET_PRODUCT_RESEARCH,
            payload=payload,
            stream=MojingTaskStream.CABINET_PRODUCT,
            tenant_key=self._tenant_key,
            session_key=self._session_key,
            scope_key=_research_scope_key(
                tenant_key=self._tenant_key,
                brand=payload["brand"],
                product_name=payload["productName"],
            ),
            service_role="mojing:skincare-cabinet:research",
        )

    async def on_task_submitted(self, task: TaskEnvelope, queue_id: str) -> None:
        logger.info(
            "research_skincare_product queued: tenant={} session={} task_id={} queue_id={}",
            self._tenant_key, self._session_key, task.task_id, queue_id,
        )

    def durable_result(self, task: TaskEnvelope, queue_id: str) -> ToolResult:
        brand = str(task.payload.get("brand") or "")
        product_name = str(task.payload.get("productName") or "")
        return tool_submitted(
            tool=self.name,
            task_id=task.task_id,
            queue_id=queue_id,
            runtime_task_status="queued",
            message_focus=(
                f"已开始调研【{brand} {product_name}】的产品资料。"
                "请告诉用户我正在整理这款产品的信息。当前任务只是已开始处理，不代表资料已经完成；此时不要要求补拍背面成分表，也不要调用入柜工具。"
                "如果用户已经明确说调研完成后直接入柜，可以承接这个后续意图，但必须说清楚是资料完成后再收进护肤柜，不要说现在已经入柜。"
            ),
        )


class ConfirmSkincareCabinetRecordTool(Tool):
    """把已调研产品正式标记为加入护肤柜。"""

    name = "confirm_skincare_cabinet_record"
    description = (
        "这是一个真实写操作：会把产品正式加入护肤柜。"
        "仅当同时满足以下条件时才能调用："
        "1) 产品资料已经调研完成；"
        "2) 系统已经拿到真实 product_id；"
        "3) 当前 in_cabinet=0；"
        "4) 用户在当前语境中明确表示“要加入护肤柜/帮我加入护肤柜/收进护肤柜”。"
        "如果用户只是问“现在呢”“好了吗”“成分怎么样”，"
        "或者你只是看到了“资料已整理完成、当前未落柜”的提示，"
        "都不要直接调用本工具；这时应先询问用户是否要加入护肤柜。"
        "不要在 research 只是 submitted/queued/running/wait_external 时调用。"
        "不要猜测 product_id。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "product_id": {
                "type": "integer",
                "description": "要正式加入护肤柜的产品ID；只能使用系统已确认的真实 product_id，不要猜测。",
            },
            "usage_status": {
                "type": "string",
                "enum": ["using", "unopened", "finished"],
                "description": "如用户这时补充了状态，可一并更新",
            },
        },
        "required": ["product_id"],
    }

    needs_followup = True
    execution_mode = "inline"
    tool_category = "sync_write"
    read_only = False

    def __init__(self, *, cabinet_repo: SkincareCabinetRepository) -> None:
        self._cabinet_repo = cabinet_repo
        self._tenant_key = "__default__"

    def set_context(self, *, tenant_key: str = "", **_: Any) -> None:
        if tenant_key:
            self._tenant_key = tenant_key

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        try:
            int(params.get("product_id"))
        except Exception:
            errors.append("product_id must be an integer")
        usage = params.get("usage_status")
        if usage is not None and _normalize_usage_status(str(usage)) is None:
            errors.append("usage_status must be one of using/unopened/finished")
        return errors

    async def execute(self, *, product_id: int, usage_status: str | None = None) -> ToolResult:
        try:
            user_id = _tenant_user_id(self._tenant_key)
        except ValueError as exc:
            return ToolResult(content=f"Error: {exc}", ok=False)

        existing = await self._cabinet_repo.get(product_id=int(product_id), user_id=user_id)
        if existing is None:
            return ToolResult(content="Error: skincare cabinet product not found", ok=False)
        if int(existing.get("in_cabinet") or 0) == 1:
            return tool_no_change(
                reason="already_in_cabinet",
                message_focus="这款产品已经在护肤柜里了，不要重复落柜。",
                model_guidance=(
                    "这款产品已经在护肤柜里，没有产生新的入柜动作。"
                    "请告诉用户它已经在柜里，可直接按护肤柜记录继续处理；"
                    "不要说“刚刚已经帮你入柜成功”或暗示本轮完成了新的落柜。"
                ),
                product_id=int(product_id),
            )

        updated = await self._cabinet_repo.mark_in_cabinet(
            product_id=int(product_id),
            user_id=user_id,
            usage_status=_normalize_usage_status(usage_status),
        )
        if updated is None:
            return ToolResult(content="Error: failed to update skincare cabinet product", ok=False)
        return json_tool_result({
            "ok": True,
            "action": "succeeded",
            "product_id": int(product_id),
            "in_cabinet": 1,
            "brand": updated.get("brand"),
            "product_name": updated.get("product_name"),
            "message_focus": (
                "这款产品已经正式加入护肤柜。请告诉用户已收录成功；"
                "只有当当前系统上下文明确给出可用肌肤日记或皮肤画像事实时，才可以询问是否让肌肤日记助手基于新入柜产品重新评估方案；"
                "如果没有看到这类明确事实，不要提转交肌肤日记助手，应先引导用户上传清晰皮肤照，等基础分析完成后再生成肌肤日记。"
            ),
        })


class LookupSkincareCabinetProductStatusTool(Tool):
    """查询某款产品之前是否已经看过、调研过或已经入柜。"""

    name = "lookup_skincare_cabinet_product_status"
    description = (
        "当你已经识别出某款护肤品的候选品牌和产品名称，并需要判断这款产品之前是否已经处理过时调用。"
        "它会查询这款产品之前是否已经在护肤柜里、是否查过但未落柜、"
        "或是否已有调研任务仍在处理中。"
        "在你决定是否发起新的 research_skincare_product 之前，应优先使用本工具判断历史状态。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "brand": {"type": "string", "description": "用户已确认的产品品牌"},
            "product_name": {"type": "string", "description": "用户已确认的产品名称"},
        },
        "required": ["brand", "product_name"],
    }

    needs_followup = True
    execution_mode = "inline"
    tool_category = "sync_read"
    read_only = True

    def __init__(
        self,
        *,
        cabinet_repo: SkincareCabinetRepository,
        runtime_task_repo: RuntimeTaskRepository,
    ) -> None:
        self._cabinet_repo = cabinet_repo
        self._runtime_task_repo = runtime_task_repo
        self._tenant_key = "__default__"

    def set_context(self, *, tenant_key: str = "", **_: Any) -> None:
        if tenant_key:
            self._tenant_key = tenant_key

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not str(params.get("brand") or "").strip():
            errors.append("brand is required")
        if not str(params.get("product_name") or "").strip():
            errors.append("product_name is required")
        return errors

    async def execute(self, *, brand: str, product_name: str) -> ToolResult:
        try:
            user_id = _tenant_user_id(self._tenant_key)
        except ValueError as exc:
            return ToolResult(content=f"Error: {exc}", ok=False)

        clean_brand = str(brand or "").strip()
        clean_product_name = str(product_name or "").strip()

        product = await self._cabinet_repo.find_latest_by_name(
            user_id=user_id,
            brand=clean_brand,
            product_name=clean_product_name,
        )
        if product is not None:
            in_cabinet = int(product.get("in_cabinet") or 0)
            if in_cabinet == 1:
                return json_tool_result({
                    "ok": True,
                    "action": "succeeded",
                    "status": "already_in_cabinet",
                    "product_id": int(product["id"]),
                    "in_cabinet": 1,
                    "brand": product.get("brand"),
                    "product_name": product.get("product_name"),
                    "usage_status": product.get("usage_status"),
                    "message_focus": (
                        "这款产品之前已经记录过，并且已经在护肤柜里。"
                        "请优先基于柜内已有信息继续回答，不要重复发起调研。"
                    ),
                })
            return json_tool_result({
                "ok": True,
                "action": "succeeded",
                "status": "researched_not_recorded",
                "product_id": int(product["id"]),
                "in_cabinet": 0,
                "brand": product.get("brand"),
                "product_name": product.get("product_name"),
                "usage_status": product.get("usage_status"),
                "message_focus": (
                    "这款产品之前已经一起看过，资料也已经整理过，但还没正式加入护肤柜。"
                    "请自然告诉用户这一点，并根据当前语境决定是否询问要不要直接落柜。"
                ),
            })

        latest_task = await self._runtime_task_repo.find_latest_by_scope_key(
            tenant_key=self._tenant_key,
            task_type=MojingTaskType.CABINET_PRODUCT_RESEARCH,
            scope_key=_research_scope_key(
                tenant_key=self._tenant_key,
                brand=clean_brand,
                product_name=clean_product_name,
            ),
        )
        if latest_task is not None:
            task_status = str(latest_task.get("status") or "").strip().lower()
            if task_status in {"queued", "running", "wait_external"}:
                return json_tool_result({
                    "ok": True,
                    "action": "succeeded",
                    "status": "research_in_progress",
                    "runtime_task_status": task_status,
                    "task_id": str(latest_task.get("task_id") or ""),
                    "message_focus": (
                        "这款产品之前已经发起过调研，而且目前还在处理中。"
                        "请告诉用户不用重复发起，可以先说明结果还没回来；此时不要进入入柜步骤。"
                    ),
                })
            if task_status == "failed":
                return json_tool_result({
                    "ok": True,
                    "action": "succeeded",
                    "status": "research_failed_recently",
                    "runtime_task_status": task_status,
                    "task_id": str(latest_task.get("task_id") or ""),
                    "message_focus": (
                        "这款产品之前发起过调研，但最近一次失败了。"
                        "请如实告诉用户，并在当前语境合适时询问是否要重新调研。"
                    ),
                })

        return json_tool_result({
            "ok": True,
            "action": "succeeded",
            "status": "not_found",
            "message_focus": (
                "这款产品之前还没有查到现成记录。"
                "请按当前护肤柜场景继续：优先询问用户是否需要继续调研或整理资料。"
                "如果用户已经说明开封/使用状态，或当前图片能明显推断产品已开封/在用，"
                "例如管身被挤压、包装已打开、有残留或明显使用痕迹，只问是否要帮她查资料，"
                "不要再确认开封或在用状态。只有用户想维护护肤柜且资产状态无法从话语或图片判断时，才轻量确认在用/未拆封。"
                "不要说已经开始搜索，不要直接进入入柜步骤。"
            ),
        })


class ListSkincareCabinetProductsTool(Tool):
    """查询用户已调研产品，默认只返回正式入柜产品。"""

    name = "list_skincare_cabinet_products"
    description = (
        "只读查询用户已调研过的产品。默认 scope=in_cabinet，只返回正式入柜产品。"
        "当用户准备开始护肤、想按自己现有产品安排护肤顺序，或问“我柜里有什么/我现在能用什么/今晚用已有产品怎么搭”时使用默认 scope。"
        "当用户跨轮说“刚刚那个帮我入柜/之前调研过的入柜/查完那款收进柜里”，但当前没有稳定的产品名或 product_id 时，"
        "用 scope=researched_not_recorded 查询最近已调研完成但未入柜的产品，以便先绑定具体产品。"
        "不要用于识别产品图或调研新产品；这些场景分别走 skincare_cabinet scene 和 research_skincare_product。"
        "如果返回空列表，说明当前没有对应范围内的产品事实，不要编造用户已有或已调研产品。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "enum": ["in_cabinet", "researched_not_recorded", "all_researched"],
                "description": (
                    "查询范围。默认 in_cabinet=正式入柜产品；"
                    "researched_not_recorded=已调研完成但未入柜产品；"
                    "all_researched=全部已调研产品。"
                ),
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 8,
                "description": "最多返回的产品数量，默认 5，最多 8。",
            },
        },
        "required": [],
    }

    needs_followup = True
    execution_mode = "inline"
    tool_category = "sync_read"
    read_only = True

    def __init__(self, *, cabinet_repo: SkincareCabinetRepository) -> None:
        self._cabinet_repo = cabinet_repo
        self._tenant_key = "__default__"

    def set_context(self, *, tenant_key: str = "", **_: Any) -> None:
        if tenant_key:
            self._tenant_key = tenant_key

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        scope = str(params.get("scope") or "in_cabinet").strip() or "in_cabinet"
        if scope not in {"in_cabinet", "researched_not_recorded", "all_researched"}:
            errors.append("scope must be one of in_cabinet/researched_not_recorded/all_researched")
        if "limit" in params and params.get("limit") is not None:
            try:
                limit = int(params.get("limit"))
            except Exception:
                errors.append("limit must be an integer")
            else:
                if limit < 1 or limit > 8:
                    errors.append("limit must be between 1 and 8")
        return errors

    async def execute(self, *, scope: str = "in_cabinet", limit: int = 5) -> ToolResult:
        try:
            user_id = _tenant_user_id(self._tenant_key)
        except ValueError as exc:
            return ToolResult(content=f"Error: {exc}", ok=False)

        safe_limit = max(1, min(int(limit or 5), 8))
        clean_scope = str(scope or "").strip() or "in_cabinet"
        records = await self._cabinet_repo.list_by_cabinet_scope(
            user_id=user_id,
            scope=clean_scope,
            limit=safe_limit,
        )
        products: list[dict[str, Any]] = []
        for item in records:
            products.append({
                "product_id": int(item.get("id") or 0),
                "brand": item.get("brand") or "",
                "product_name": item.get("product_name") or "",
                "category": item.get("category") or "",
                "core_efficacy": item.get("core_efficacy") or [],
                "core_ingredients": item.get("core_ingredients") or [],
                "risk_ingredients": item.get("risk_ingredients") or [],
                "in_cabinet": int(item.get("in_cabinet") or 0),
                "usage_status": item.get("usage_status") or "using",
                "update_time": item.get("update_time"),
            })

        if not products:
            return json_tool_result({
                "ok": True,
                "action": "succeeded",
                "status": "empty",
                "scope": clean_scope,
                "count": 0,
                "products": [],
                "message_focus": _list_products_empty_focus(clean_scope),
            })

        return json_tool_result({
            "ok": True,
            "action": "succeeded",
            "status": _list_products_status(clean_scope),
            "scope": clean_scope,
            "count": len(products),
            "products": products,
            "message_focus": _list_products_focus(clean_scope, len(products)),
        })


def _list_products_status(scope: str) -> str:
    if scope == "researched_not_recorded":
        return "has_researched_not_recorded"
    if scope == "all_researched":
        return "has_researched_products"
    return "has_products"


def _list_products_empty_focus(scope: str) -> str:
    if scope == "researched_not_recorded":
        return (
            "当前没有查到已调研完成但未入柜的产品。"
            "请不要编造刚刚那款产品；如果用户想入柜，需要先确认具体品牌和产品名，或等待调研完成。"
        )
    if scope == "all_researched":
        return (
            "当前没有查到已调研产品记录。"
            "请不要编造用户调研过的产品；如果用户想查某款产品，先让她提供产品名或产品图。"
        )
    return (
        "当前没有查到用户护肤柜里已正式入柜的产品。"
        "请不要编造用户已有产品；如果用户想按手边产品安排护肤，轻量询问她现在手边有哪些产品，发名字或产品图都可以。"
    )


def _list_products_focus(scope: str, count: int) -> str:
    if scope == "researched_not_recorded":
        if count == 1:
            return (
                "已查到最近一款调研完成但未入柜的产品。"
                "如果用户只是说“刚刚那个/之前那款”，请先确认是不是这款产品；"
                "确认后才能调用 confirm_skincare_cabinet_record，不要直接落柜。"
            )
        return (
            "已查到多款调研完成但未入柜的产品。"
            "请让用户确认要入柜的是哪一款；不要替用户选择，也不要直接调用入柜工具。"
        )
    if scope == "all_researched":
        return (
            "已查到用户调研过的产品。"
            "请按当前问题挑相关的 1-3 个产品使用；不要把未入柜产品说成已经在护肤柜里。"
        )
    return (
        "已查到用户护肤柜里的已入柜产品。"
        "请按当前问题挑相关的 1-3 个产品使用，不要机械复述整份清单；"
        "如果信息不足以判断肤况，保留边界，不要把产品搭配说成确定治疗方案。"
    )
