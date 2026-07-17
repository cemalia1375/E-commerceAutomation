"""Mojing task stream names.

simpleclaw 的 runtime 层只把 stream 当作字符串通道；Mojing 在这里集中声明
自己的业务流名，避免业务枚举泄漏回框架协议。
"""

from __future__ import annotations

from enum import StrEnum


class MojingTaskStream(StrEnum):
    POSTPROCESS = "postprocess"
    OBLIGATION_EXTRACT = "obligation_extract"
    IMAGE_ANALYSIS = "image_analysis"
    CABINET_PRODUCT = "cabinet_product"
    SKIN_DIARY = "skin_diary"
    DEEP_RESEARCH = "deep_research"
    SUBAGENT_DISPATCH = "subagent_dispatch"
    MEMORY_EXTRACT = "memory_extract"
    BACKGROUND = "background"
