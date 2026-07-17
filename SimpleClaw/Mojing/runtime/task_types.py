"""Mojing runtime task type names."""

from __future__ import annotations

from enum import StrEnum


class MojingTaskType(StrEnum):
    POSTPROCESS = "postprocess"
    SKIN_PROFILE_SYNC = "skin_profile_sync"
    STRUCTURED_MEMORY = "structured_memory"
    OBLIGATION_EXTRACT = "obligation_extract"
    IMAGE_ANALYSIS = "image_analysis"
    CABINET_PRODUCT_RESEARCH = "cabinet_product_research"
    CABINET_PRODUCT_RECORD = "cabinet_product_record"
    SKIN_DIARY_GENERATION = "skin_diary_generation"
    DEEP_RESEARCH = "deep_research"
    SUBAGENT_DISPATCH = "subagent_dispatch"
    MEMORY_EXTRACT = "memory_extract"
    DREAM = "dream"
