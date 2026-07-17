"""create_cross_episode_highlights 工具：跨集高光切片规划任务。

支持两种模式：
  - Batch 管道（默认）：每个剧名独立的 highlight_batch，可单独治理
  - 旧管道（回退）：所有剧名合并为一个 highlight_plan task（向后兼容）

当 batch 管道全部失败时，自动回退到旧管道。
"""
from __future__ import annotations

import json
import logging
import traceback
import uuid
from typing import TYPE_CHECKING, Any

from simpleclaw.runtime.task_protocol import TaskEnvelope
from simpleclaw.tools.base import Tool, ToolResult

from Flowcut.runtime.streams import FlowcutTaskStream

if TYPE_CHECKING:
    from simpleclaw.runtime.services import RuntimeServices

logger = logging.getLogger(__name__)


class CreateCrossEpisodeHighlightsTool(Tool):
    name = "create_cross_episode_highlights"
    description = (
        "按 AI 漫剧名称，从指定集数范围自动识别高光起点，跨集向后拼接约 1 分钟的连续切片，"
        "批量产出候选成片（数量不设上限）。支持同时指定多个剧名批量处理。"
        "用户要求「跨集高光」「从前几集抽一分钟」「连续切片」「第X集到第Y集」时使用。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "drama_name": {"type": "string", "description": "单个 AI 漫剧名称（与 drama_names 二选一）"},
            "drama_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "多个 AI 漫剧名称列表，同时为多部剧批量生成高光时使用",
            },
            "start_episode": {
                "type": "integer",
                "description": "从第几集开始搜索高光起点，默认 1（从第 1 集开始）",
                "default": 1,
            },
            "end_episode": {
                "type": "integer",
                "description": "到第几集为止（含），不传则不限制上限",
            },
            "num_candidates": {
                "type": "integer",
                "description": "每部剧产出几条候选切片，默认 3，不设上限",
                "default": 3,
            },
            "connector_asset_id": {
                "type": "integer",
                "description": "数字人素材 ID（可选）；不传时系统自动选库中第一个可用数字人",
            },
        },
    }

    execution_mode = "durable"
    needs_followup = True
    durable_action = "queued"
    tool_category = "background_write"
    read_only = False

    def __init__(
        self,
        *,
        runtime: "RuntimeServices",
        task_repo=None,
        highlight_batch_repo=None,
        highlight_asset_repo=None,
    ) -> None:
        self._runtime = runtime
        self._task_repo = task_repo
        self._highlight_batch_repo = highlight_batch_repo
        self._highlight_asset_repo = highlight_asset_repo
        self._tenant_key = "flowcut"
        self._session_key = ""

    def set_context(self, *, tenant_key: str = "", session_key: str = "", **_: object) -> None:
        if tenant_key:
            self._tenant_key = tenant_key
        self._session_key = session_key or self._session_key

    @property
    def _use_batch_pipeline(self) -> bool:
        """当 batch_repo + runtime 都可用时走新的批量管道。"""
        return (
            self._highlight_batch_repo is not None
            and self._runtime is not None
        )

    async def prepare_task(
        self,
        drama_name: str | None = None,
        drama_names: list[str] | None = None,
        start_episode: int = 1,
        end_episode: int | None = None,
        num_candidates: int = 3,
        connector_asset_id: int | None = None,
        **_: Any,
    ) -> TaskEnvelope | ToolResult:
        names: list[str] = [str(d).strip() for d in (drama_names or []) if d and str(d).strip()]
        if not names and drama_name:
            names = [drama_name.strip()]
        if not names:
            return ToolResult(
                content=json.dumps({"ok": False, "error": "drama_name / drama_names 不能为空"},
                                   ensure_ascii=False),
                ok=False,
            )
        capped = max(1, int(num_candidates or 3))
        start_ep = max(1, int(start_episode or 1))
        end_ep = int(end_episode) if end_episode is not None else None

        # ── 新路径：Batch 管道（每个剧名独立 batch）──
        if self._use_batch_pipeline:
            return await self._prepare_batch_pipeline(
                names, capped, start_ep, end_ep, connector_asset_id,
            )

        # ── 旧路径：单体 highlight_plan（向后兼容）──
        return await self._prepare_legacy(names, capped, start_ep, end_ep, connector_asset_id)

    async def _prepare_batch_pipeline(
        self,
        names: list[str],
        num_candidates: int,
        start_episode: int,
        end_episode: int | None,
        connector_asset_id: int | None,
    ) -> TaskEnvelope | ToolResult:
        """新路径：为每个剧名创建独立的 highlight_batch。

        若全部提交失败，自动回退到旧 highlight_plan 管道，
        并在 ToolResult 中说明回退原因。
        """
        batch_ids: list[str] = []
        errors: list[dict] = []
        submitted_count = 0

        for drama_name in names:
            batch_id = uuid.uuid4().hex
            try:
                # 查询该剧是否有 episode_source
                if self._highlight_asset_repo is not None:
                    rows = await self._highlight_asset_repo.list_by_tenant(
                        self._tenant_key,
                        asset_type="episode_source",
                        drama_name=drama_name,
                        limit=1,
                    )
                    if not rows:
                        all_rows = await self._highlight_asset_repo.list_by_tenant(
                            self._tenant_key,
                            asset_type="episode_source",
                            limit=500,
                        )
                        from Flowcut.services.clip_planner import match_drama_episodes
                        matched = match_drama_episodes(all_rows, drama_name)
                        if not matched:
                            errors.append({
                                "drama_name": drama_name,
                                "error": f"没有在原片库找到「{drama_name}」",
                            })
                            continue

                # 创建 batch 记录
                await self._highlight_batch_repo.create_batch(
                    tenant_key=self._tenant_key,
                    drama_name=drama_name,
                    num_candidates=num_candidates,
                    batch_id=batch_id,
                )
                await self._highlight_batch_repo.update_orchestrator_state(
                    batch_id,
                    {
                        "start_episode": start_episode,
                        "end_episode": end_episode,
                        "connector_asset_id": connector_asset_id,
                        "session_key": self._session_key or "highlight_plan",
                    },
                )

                # 提交编排器任务
                await self._runtime.submit_task(
                    TaskEnvelope(
                        task_type="highlight_batch",
                        payload={
                            "batch_id": batch_id,
                            "tenant_key": self._tenant_key,
                            "session_key": self._session_key,
                            "num_candidates": num_candidates,
                            "start_episode": start_episode,
                            "end_episode": end_episode,
                            "connector_asset_id": connector_asset_id,
                        },
                        stream=FlowcutTaskStream.HIGHLIGHT_BATCH,
                        tenant_key=self._tenant_key,
                        session_key=self._session_key or None,
                        scope_key=batch_id,
                    ),
                    tool_name="highlight_batch",
                    summary=f"跨集高光: {drama_name}",
                )
                batch_ids.append(batch_id)
                submitted_count += 1
                logger.info(
                    "create_cross_episode_highlights: batch submitted drama=%s batch_id=%s",
                    drama_name, batch_id,
                )
            except Exception as exc:
                tb = traceback.format_exc()
                logger.error(
                    "create_cross_episode_highlights: batch submit failed drama=%s: %s\n%s",
                    drama_name, exc, tb,
                )
                errors.append({
                    "drama_name": drama_name,
                    "error": f"{type(exc).__name__}: {exc}",
                })

        # ── 全部失败 → 自动回退到旧管道 ──
        if submitted_count == 0:
            error_details = "; ".join(
                f"「{e['drama_name']}」: {e['error']}" for e in errors
            )
            logger.warning(
                "create_cross_episode_highlights: batch pipeline 全部失败 (%s)，"
                "回退到旧 highlight_plan 管道",
                error_details,
            )
            # 走旧管道兜底
            legacy_envelope = await self._prepare_legacy(
                names, num_candidates, start_episode, end_episode, connector_asset_id,
            )
            # 在旧 envelope 的 payload 中附带回退原因，供日志追查
            legacy_envelope.payload["batch_fallback_reason"] = error_details
            return legacy_envelope

        # ── 部分成功 ──
        title = (
            f"已启动 {submitted_count} 部剧的高光生成"
            if submitted_count > 1
            else f"已启动「{names[0]}」高光生成"
        )
        if errors:
            title += f"（{len(errors)} 部失败）"

        return ToolResult(
            content=json.dumps({
                "ok": True,
                "action": self.durable_action,
                "pipeline": "batch",
                "submitted": submitted_count,
                "batch_ids": batch_ids,
                "task_id": f"batch:{batch_ids[0]}" if len(batch_ids) == 1 else None,
                "errors": errors if errors else None,
                "error_details": [
                    f"「{e['drama_name']}」: {e['error']}" for e in errors
                ] if errors else None,
                "data": {
                    "drama_names": names,
                    "start_episode": start_episode,
                    "end_episode": end_episode,
                    "num_candidates": num_candidates,
                    "batch_ids": batch_ids,
                },
                "navigate": {"route": "/creative?tab=highlight", "mode": "push"},
                "ui_hint": {"render_as": "text", "title": title},
            }, ensure_ascii=False),
            ok=True,
        )

    async def _prepare_legacy(
        self,
        names: list[str],
        num_candidates: int,
        start_episode: int,
        end_episode: int | None,
        connector_asset_id: int | None,
    ) -> TaskEnvelope:
        """旧路径：所有剧名合并为一个 highlight_plan task。"""
        queue_position = 0
        if self._task_repo is not None:
            try:
                active = await self._task_repo.list_active(
                    tenant_key=self._tenant_key,
                    task_types=("highlight_plan",),
                )
                queue_position = len(active)
            except Exception:
                pass
        batch_id = uuid.uuid4().hex
        scope_key = "highlight_plan:" + ",".join(names) + ":" + batch_id
        return TaskEnvelope(
            task_type="highlight_plan",
            payload={
                "drama_names": names,
                "num_candidates": num_candidates,
                "start_episode": start_episode,
                "end_episode": end_episode,
                "tenant_key": self._tenant_key,
                "session_key": self._session_key,
                "batch_id": batch_id,
                "connector_asset_id": connector_asset_id,
                "queue_position": queue_position,
            },
            stream=FlowcutTaskStream.HIGHLIGHT_PLAN,
            tenant_key=self._tenant_key,
            session_key=self._session_key or None,
            scope_key=scope_key,
        )

    def durable_result(self, task: TaskEnvelope, queue_id: str) -> ToolResult:
        """旧路径的提交确认（batch 路径在 prepare_task 中已返回 ToolResult）。"""
        qpos = task.payload.get("queue_position", 0)
        if isinstance(qpos, int) and qpos > 0:
            title = (
                f"已有 {qpos} 个任务在排队，"
                f"当前任务将在前序完成后自动开始"
            )
        else:
            title = "已开始跨集高光切片"
        return ToolResult(
            content=json.dumps(
                {
                    "ok": True,
                    "action": self.durable_action,
                    "task_id": task.task_id,
                    "task_type": task.task_type,
                    "queue_id": queue_id,
                    "queue_position": qpos,
                    "data": {
                        "drama_names": task.payload.get("drama_names"),
                        "start_episode": task.payload.get("start_episode"),
                        "end_episode": task.payload.get("end_episode"),
                        "num_candidates": task.payload.get("num_candidates"),
                    },
                    "navigate": {"route": "/creative?tab=highlight", "mode": "push"},
                    "ui_hint": {"render_as": "text", "title": title},
                },
                ensure_ascii=False,
            )
        )
