"""拆镜完成后并发生成 4 条角色差异化广告脚本（预览，不写库）。"""
from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from simpleclaw.tools.base import Tool, ToolResult
from Flowcut.services.script_generator import ROLES
from Flowcut.services.script_generator import generate_for_role as _generate_for_role_impl


def generate_for_role(role: dict, scene_data: list, **kwargs):
    """Sync wrapper around the async implementation; returns a coroutine.

    A plain ``def`` (not ``async def``) so that ``unittest.mock.patch``
    creates a ``MagicMock`` rather than an ``AsyncMock``.  This lets tests
    supply coroutine objects via ``side_effect`` that ``asyncio.gather``
    can await directly, without an extra AsyncMock indirection layer.
    """
    return _generate_for_role_impl(role, scene_data, **kwargs)

if TYPE_CHECKING:
    from Flowcut.storage.material_repo import MaterialRepository
    from Flowcut.storage.reference_video_repo import ReferenceVideoRepository


class GenerateScriptsTool(Tool):
    """基于拆镜结果并发生成 4 条角色差异化广告脚本，预览用，不写库。"""

    name = "generate_scripts"
    description = (
        "根据爆款视频的拆镜结果，生成 4 条差异化广告脚本（痛点型、场景型、对比型、口碑型）。"
        "每条脚本包含各分镜的画面指引和口播文案，供运营选择后再保存。"
        "请在 decompose_video 任务完成后调用。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "material_id": {
                "type": "integer",
                "description": "已完成拆镜的子素材 ID（将通过 source_video_id 关联到参考视频的 scene_data）",
            },
        },
        "required": ["material_id"],
    }
    execution_mode = "inline"
    needs_followup = True

    def __init__(self, *,
                 material_repo: "MaterialRepository",
                 ref_video_repo: "ReferenceVideoRepository") -> None:
        self._material_repo = material_repo
        self._ref_video_repo = ref_video_repo

    async def execute(self, material_id: int, **kwargs) -> ToolResult:
        material = await self._material_repo.get(material_id)
        if material is None:
            return ToolResult(content=f"素材 {material_id} 不存在", ok=False)

        # 通过 source_video_id 从 fc_reference_video 读取 scene_data
        source_video_id = material.get("source_video_id")
        if not source_video_id:
            return ToolResult(
                content="该素材不是从爆款视频拆出的子片段（无 source_video_id），无法获取拆镜数据",
                ok=False,
            )

        ref_video = await self._ref_video_repo.get(source_video_id)
        if ref_video is None:
            return ToolResult(content=f"关联的参考视频 {source_video_id} 不存在", ok=False)

        raw_scene = ref_video.get("scene_data_json")
        if not raw_scene:
            return ToolResult(
                content="该参考视频尚未完成拆镜，请先调用 decompose_video 并等待任务完成",
                ok=False,
            )

        scene_data: list[dict] = json.loads(raw_scene) if isinstance(raw_scene, str) else raw_scene

        results = await asyncio.gather(
            *[generate_for_role(role, scene_data) for role in ROLES],
            return_exceptions=False,
        )

        successful = [r for r in results if r is not None]
        failed_roles = [
            ROLES[i]["name"] for i, r in enumerate(results) if r is None
        ]

        if not successful:
            return ToolResult(content="所有角色脚本生成失败，请重试", ok=False)

        content_parts = [json.dumps(successful, ensure_ascii=False)]
        if failed_roles:
            content_parts.append(f"\n（以下角色生成失败：{', '.join(failed_roles)}）")

        return ToolResult(content="".join(content_parts), ok=True)
