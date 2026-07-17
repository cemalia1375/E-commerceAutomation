"""Journey 阶段跳转逻辑。

规则（当前版本）：
  - novice  → explore : 首次肌肤日记生成成功（skin_diary_generated 里程碑）
  - novice  → explore : 前端明确触发进入探索期（explore_entered 里程碑，兼容旧入口）
  - explore → mature  : 预留，暂未实现

所有跳转规则集中在此文件，不散落在 repo 或 server 里。
"""

from __future__ import annotations

from Mojing.storage.tenant_state_repo import TenantStateRepository

# 事件名 → 对应的里程碑 key
_EVENT_MILESTONE_MAP: dict[str, str] = {
    "explore_entered": "explore_entered",
    "skin_diary_generated": "skin_diary_generated",
}


def _maybe_promote(stage: str, milestones: dict) -> str:
    """根据当前里程碑判断是否需要升级阶段。"""
    if stage == "novice" and (
        milestones.get("skin_diary_generated")
        or milestones.get("explore_entered")
    ):
        return "explore"
    return stage


async def record_journey_event(
    repo: TenantStateRepository,
    tenant_key: str,
    event: str,
) -> tuple[str, str]:
    """记录一个 journey 事件，必要时推进阶段，持久化到 DB。

    Args:
        repo:       TenantStateRepository 实例
        tenant_key: 租户标识
        event:      事件名（如 "explore_entered"）

    Returns:
        (stage_before, stage_after) — 调用方可用于判断是否发生跳转
    """
    journey      = await repo.get_journey(tenant_key)
    stage_before = journey["stage"]
    milestones   = dict(journey["milestones"])

    # 标记里程碑（幂等：已标记的不重复写）
    milestone = _EVENT_MILESTONE_MAP.get(event)
    if milestone and not milestones.get(milestone):
        milestones[milestone] = True

    stage_after = _maybe_promote(stage_before, milestones)

    # 只在有变化时写 DB
    if stage_after != stage_before or milestones != journey["milestones"]:
        await repo.save_journey(tenant_key, {
            "stage":      stage_after,
            "milestones": milestones,
        })

    return stage_before, stage_after
