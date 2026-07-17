"""Stable prompt provider for WorkItem governance rules."""

from __future__ import annotations

from dataclasses import dataclass

from simpleclaw.context.providers import ContextBuildContext, PromptSection


_WORK_ITEM_RULES = """# WorkItem Governance

Use WorkItem tools only for tasks that need explicit task-state governance.

Create a WorkItem when the user asks for a multi-step, trackable task with a
clear goal, user-visible deliverable, external side effect, cross-turn
continuation, recovery need, or completion evidence requirement.

Do not create a WorkItem for ordinary chat, one-off explanations, simple
read-only queries, tool discovery, or every small checklist step.

WorkItem is for task commitment and execution state. Checklist is for the
current local steps. Evidence proves completion; do not mark a WorkItem
completed only because the model says it is done.

WorkItem state does not control your next action. It preserves facts so the
user and runtime can inspect, patch, recover, and verify the task."""


@dataclass(slots=True)
class WorkItemPromptProvider:
    """Prompt rules explaining when the model should use WorkItem tools."""

    source: str = "work_item_governance_prompt"

    async def collect_stable_prompt(
        self,
        ctx: ContextBuildContext,
    ) -> list[PromptSection]:
        del ctx
        return [PromptSection(content=_WORK_ITEM_RULES, source=self.source)]
