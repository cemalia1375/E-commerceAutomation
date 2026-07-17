"""SkillTool —— 按需读取或激活本地 SKILL.md。"""

from __future__ import annotations

from pathlib import Path

from simpleclaw.context.builder import ContextBuilder
from simpleclaw.tools.base import Tool, ToolResult


class LoadSkillTool(Tool):
    """Load one registered skill by name.

    observation skill:
      - returns the skill body as a normal tool observation

    scene skill:
      - activates the skill on the current ContextBuilder
      - persists a short activation ack, not the full skill body
    """

    name = "load_skill"
    description = (
        "当当前轮已经明显进入某个本地 scene skill 的适用场景时，先调用本工具加载对应 SKILL.md。"
        "对于 scene skill，这通常是进入该场景的前置动作；在加载对应 skill 之前，不要直接跳到该场景的后续业务工具。"
        "调用本工具的同一轮 assistant content 只能是极短过渡句；"
        "不要在 load_skill 前输出该 skill 的正式邀请、结论、步骤或完整话术，正式回复等 skill 加载后的下一轮再说。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The registered skill name to load.",
            },
        },
        "required": ["name"],
    }
    needs_followup = True
    tool_category = "sync_read"
    read_only = True

    def __init__(self) -> None:
        self._context_builder: ContextBuilder | None = None

    def set_context(self, *, context_builder: ContextBuilder | None = None, **_: object) -> None:
        self._context_builder = context_builder

    async def execute(self, *, name: str) -> ToolResult:
        if self._context_builder is None:
            return ToolResult(content="Error: context_builder is not available for load_skill", ok=False)
        if self._context_builder.skill_registry is None:
            return ToolResult(content="Error: no skill registry is configured on this agent", ok=False)

        skill_name = str(name or "").strip()
        if not skill_name:
            return ToolResult(content="Error: skill name is required", ok=False)

        try:
            materialization = self._context_builder.activate_skill(skill_name)
        except KeyError as exc:
            return ToolResult(content=f"Error: {exc}", ok=False)
        except Exception as exc:
            return ToolResult(content=f"Error loading skill '{skill_name}': {exc}", ok=False)

        if materialization == "scene":
            return ToolResult(
                content=(
                    f"场景 skill「{skill_name}」已激活。"
                    "上一条 assistant 可见内容已经发送给用户。"
                    "不要重复上一条 assistant 内容；只继续补充该场景下下一步需要说的话。"
                ),
                ok=True,
                persist_to_history=True,
                metadata={"skill_name": skill_name, "materialization": materialization},
            )

        try:
            body = self._context_builder.render_skill_body(skill_name)
        except Exception as exc:
            return ToolResult(content=f"Error reading skill '{skill_name}': {exc}", ok=False)
        return ToolResult(
            content=body,
            ok=True,
            persist_to_history=True,
            metadata={"skill_name": skill_name, "materialization": materialization},
        )


class UnloadSkillTool(Tool):
    """Deactivate one previously activated scene skill."""

    name = "unload_skill"
    description = (
        "当当前 scene 已完成、已不相关，或用户明显切到别的话题时，调用本工具退出已激活的 scene skill。"
        "退出后，不要继续按该 skill 的流程推进。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The active scene skill name to unload.",
            },
        },
        "required": ["name"],
    }
    needs_followup = False
    tool_category = "sync_read"
    read_only = True

    def __init__(self) -> None:
        self._context_builder: ContextBuilder | None = None

    def set_context(self, *, context_builder: ContextBuilder | None = None, **_: object) -> None:
        self._context_builder = context_builder

    async def execute(self, *, name: str) -> ToolResult:
        if self._context_builder is None:
            return ToolResult(content="Error: context_builder is not available for unload_skill", ok=False)
        skill_name = str(name or "").strip()
        if not skill_name:
            return ToolResult(content="Error: skill name is required", ok=False)
        self._context_builder.deactivate_skill(skill_name)
        return ToolResult(
            content=f"Deactivated scene skill '{skill_name}'.",
            ok=True,
            persist_to_history=False,
            metadata={"skill_name": skill_name},
        )


class ReadSkillAssetTool(Tool):
    """Read a sibling file from a registered skill's directory.

    Skills often bundle SKILL.md alongside workflow YAMLs, action JS files,
    and other assets that the LLM must load on demand. SKILL.md only
    references those files by relative path; this tool resolves them
    against the owning skill's root and returns their text.
    """

    name = "read_skill_asset"
    description = (
        "Read a file (workflow YAML, action JS, asset markdown, etc.) "
        "from a registered skill's directory by skill_name + relative path. "
        "Use this when the active SKILL.md tells you to load a sibling file. "
        "The path is resolved relative to the skill's root and must stay "
        "inside that directory (no '..' escapes)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "description": "The registered skill name (as listed in the Skills Index).",
            },
            "path": {
                "type": "string",
                "description": "File path relative to the skill's root directory (e.g. 'workflows/login.yaml').",
            },
        },
        "required": ["skill_name", "path"],
    }
    needs_followup = True
    tool_category = "sync_read"
    read_only = True

    def __init__(self) -> None:
        self._context_builder: ContextBuilder | None = None

    def set_context(self, *, context_builder: ContextBuilder | None = None, **_: object) -> None:
        self._context_builder = context_builder

    async def execute(self, *, skill_name: str, path: str) -> ToolResult:
        if self._context_builder is None:
            return ToolResult(
                content="Error: context_builder is not available for read_skill_asset",
                ok=False,
            )
        registry = self._context_builder.skill_registry
        if registry is None:
            return ToolResult(
                content="Error: no skill registry is configured on this agent",
                ok=False,
            )

        name = str(skill_name or "").strip()
        relative = str(path or "").strip()
        if not name:
            return ToolResult(content="Error: skill_name is required", ok=False)
        if not relative:
            return ToolResult(content="Error: path is required", ok=False)

        try:
            skill_root = registry.skill_dir(name).resolve()
        except KeyError as exc:
            return ToolResult(content=f"Error: {exc}", ok=False)

        target = (skill_root / relative).resolve()
        if not target.is_relative_to(skill_root):
            return ToolResult(
                content=f"Error: path '{relative}' escapes skill '{name}' directory",
                ok=False,
            )

        try:
            text = Path(target).read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return ToolResult(
                content=f"Error: file not found in skill '{name}': {relative}",
                ok=False,
            )
        except IsADirectoryError:
            return ToolResult(
                content=f"Error: '{relative}' is a directory, not a file",
                ok=False,
            )
        except Exception as exc:
            return ToolResult(
                content=f"Error reading skill asset '{relative}': {exc}",
                ok=False,
            )

        header = f"[skill={name} path={relative} chars={len(text)}]\n"
        return ToolResult(
            content=header + text,
            ok=True,
            metadata={"skill_name": name, "path": relative},
        )
