"""内置工具 —— 四个基础文件系统 / Shell 工具。

用法：
    from simpleclaw.tools.builtin import register_builtin_tools

    registry = ToolRegistry()
    register_builtin_tools(registry, workspace="/path/to/workspace")
"""

from simpleclaw.tools.builtin.edit import EditTool
from simpleclaw.tools.builtin.exec import ExecTool
from simpleclaw.tools.builtin.read import ReadTool
from simpleclaw.tools.builtin.skill import LoadSkillTool, ReadSkillAssetTool, UnloadSkillTool
from simpleclaw.tools.builtin.tool_search import ToolSearchTool
from simpleclaw.tools.builtin.work_item import (
    AttachEvidenceTool,
    CompleteWorkItemTool,
    CreateWorkItemTool,
    UpdateChecklistTool,
)
from simpleclaw.tools.builtin.write import WriteTool
from simpleclaw.tools.catalog import ToolExposureState
from simpleclaw.tools.registry import ToolRegistry
from simpleclaw.workitem.store import WorkItemStore

__all__ = [
    "ReadTool",
    "WriteTool",
    "EditTool",
    "ExecTool",
    "LoadSkillTool",
    "UnloadSkillTool",
    "ReadSkillAssetTool",
    "ToolSearchTool",
    "CreateWorkItemTool",
    "UpdateChecklistTool",
    "AttachEvidenceTool",
    "CompleteWorkItemTool",
    "register_builtin_tools",
    "register_tool_search",
    "register_work_item_tools",
]


def register_builtin_tools(registry: ToolRegistry, workspace: str | None = None) -> None:
    """将全部四个内置工具注册到给定的注册表中。"""
    for tool_cls in (ReadTool, WriteTool, EditTool, ExecTool):
        registry.register(tool_cls(workspace=workspace))
    for tool_cls in (LoadSkillTool, UnloadSkillTool, ReadSkillAssetTool):
        registry.register(tool_cls())


def register_tool_search(
    registry: ToolRegistry,
    exposure_state: ToolExposureState | None = None,
) -> ToolExposureState:
    """Register tool_search and return the exposure state it will update."""
    state = exposure_state or registry.exposure_state or ToolExposureState()
    registry.set_exposure_state(state)
    registry.register(ToolSearchTool(registry.catalog, state))
    return state


def register_work_item_tools(
    registry: ToolRegistry,
    store: WorkItemStore,
) -> None:
    """Register the minimal WorkItem governance tool set."""
    for tool_cls in (
        CreateWorkItemTool,
        UpdateChecklistTool,
        AttachEvidenceTool,
        CompleteWorkItemTool,
    ):
        registry.register(tool_cls(store))
