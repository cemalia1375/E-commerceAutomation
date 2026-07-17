"""Prompt 文件注册表 — 管理所有可编辑的 prompt 文件路径与元数据。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PromptFileEntry:
    key: str          # 唯一标识符（用于 URL 参数 ?file=key）
    label: str        # UI 显示名称
    group: str        # 侧边栏分组名称
    path: Path        # 绝对路径
    hot_reload: bool = True   # True = 保存即生效；False = 需要重启


def make_prompt_file_map(
    workspace: Path,
    subagent_prompt: Path,
) -> dict[str, PromptFileEntry]:
    """根据工作区路径构建 prompt 文件注册表。

    workspace:       Mojing/workspace/
    subagent_prompt: Mojing/subagent/prompt/
    """
    # journey 目录名含前置空格（历史遗留）
    journey = workspace / " journey"
    device = workspace / "device"
    device_journey = device / "journey"

    entries = [
        # ── App 主 Agent ──────────────────────────────────────────
        PromptFileEntry("agent",      "Agent.md",          "App 主 Agent", workspace / "Agent.md"),
        PromptFileEntry("soul",       "SOUL.md",            "App 主 Agent", workspace / "SOUL.md"),
        PromptFileEntry("tool",       "TOOL.md",            "App 主 Agent", workspace / "TOOL.md"),
        PromptFileEntry("first_token", "first_token.md",     "App 主 Agent", workspace / "first_token.md"),
        PromptFileEntry("user_tpl",   "USER.md（格式模板）", "App 主 Agent", workspace / "USER.md"),

        # ── 硬件魔镜 Device ───────────────────────────────────────
        PromptFileEntry("device_agent",       "device/Agent.md",       "硬件魔镜 Device", device / "Agent.md"),
        PromptFileEntry("device_soul",        "device/SOUL.md",        "硬件魔镜 Device", device / "SOUL.md"),
        PromptFileEntry("device_tool",        "device/TOOL.md",        "硬件魔镜 Device", device / "TOOL.md"),
        PromptFileEntry("device_first_token", "device/first_token.md", "硬件魔镜 Device", device / "first_token.md"),
        PromptFileEntry("device_novice",      "device/journey/novice.md", "硬件 Journey", device_journey / "novice.md"),
        PromptFileEntry("device_explore",     "device/journey/explore.md", "硬件 Journey", device_journey / "explore.md"),

        # ── 共享层（主 + 子 Agent 都注入）──────────────────────────
        PromptFileEntry("compliance", "compliance.md",      "共享合规", workspace / "compliance.md"),

        # ── App Journey 阶段 ───────────────────────────────────────
        PromptFileEntry("novice",  "novice.md",  "App Journey", journey / "novice.md"),
        PromptFileEntry("explore", "explore.md", "App Journey", journey / "explore.md"),

        # ── 冷链路 ─────────────────────────────────────────────────
        PromptFileEntry("cold_path",          "cold_path.md",          "冷链路", workspace / "cold_path.md"),
        PromptFileEntry("compression_memory", "compression_memory.md", "冷链路", workspace / "compression_memory.md"),
        PromptFileEntry("postprocess",        "postprocess.md",        "冷链路", workspace / "postprocess.md"),

        # ── 肌肤日记子 Agent ───────────────────────────────────────
        PromptFileEntry("skin_diary",      "skin_diary.md",      "肌肤日记", subagent_prompt / "skin_diary.md",      hot_reload=False),
        PromptFileEntry("skin_diary_tool", "skin_diary_tool.md", "肌肤日记", subagent_prompt / "skin_diary_tool.md", hot_reload=False),

        # ── 深度报告子 Agent ───────────────────────────────────────
        PromptFileEntry("deep_report", "deep_report.md", "深度报告", subagent_prompt / "deep_report.md", hot_reload=False),
    ]

    return {e.key: e for e in entries}
