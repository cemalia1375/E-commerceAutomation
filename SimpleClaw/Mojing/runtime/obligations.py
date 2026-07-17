"""Compatibility exports for obligation dispatching."""

from __future__ import annotations

from Mojing.runtime.obligation_actions import (
    ACTION_CONFIRM_SKINCARE_CABINET_RECORD,
    ACTION_GENERATE_DEEP_REPORT,
    ACTION_GENERATE_SKIN_DIARY,
    DEPENDENCY_CABINET_PRODUCT_RESEARCH_SUCCEEDED,
    DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED,
    ObligationRuntimeTask,
    build_obligation_runtime_task,
)
from Mojing.runtime.obligation_dispatcher import dispatch_obligations_for_dependency

__all__ = [
    "ACTION_GENERATE_SKIN_DIARY",
    "ACTION_GENERATE_DEEP_REPORT",
    "ACTION_CONFIRM_SKINCARE_CABINET_RECORD",
    "DEPENDENCY_IMAGE_ANALYSIS_SUCCEEDED",
    "DEPENDENCY_CABINET_PRODUCT_RESEARCH_SUCCEEDED",
    "ObligationRuntimeTask",
    "build_obligation_runtime_task",
    "dispatch_obligations_for_dependency",
]
