"""Evidence retrieval routing for user questions.

This module deliberately contains no database or LLM calls. It only decides
which kind of evidence a user is asking for, so providers, prefetch hooks, and
tools can share one boundary instead of carrying separate keyword rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


EvidenceRouteKind = Literal["none", "text_memory", "historical_image"]


@dataclass(frozen=True, slots=True)
class EvidenceRoute:
    kind: EvidenceRouteKind
    reason: str = ""
    matched_history: tuple[str, ...] = ()
    matched_visual: tuple[str, ...] = ()
    matched_text_memory: tuple[str, ...] = ()


_HISTORY_ANCHORS = frozenset({
    "上次", "上回", "之前", "以前", "前几天", "前几轮",
    "上周", "上个月", "昨天", "那天", "那次", "那个时候", "当时",
    "你之前", "我们之前", "我之前",
})

_TEXT_MEMORY_CUES = frozenset({
    "记得", "记不得", "记不住", "忘了", "忘记",
    "你说过", "你推荐的", "你提过", "说的那个", "推荐过",
    "推荐", "说过", "提过", "聊过", "建议", "方案",
})

_IMAGE_OBJECT_CUES = frozenset({
    "照片", "图片", "图", "自拍", "那张", "这一张", "这张",
    "原图", "历史图",
})

_SKIN_VISUAL_CUES = frozenset({
    "脸", "面颊", "脸颊", "额头", "鼻子", "鼻翼",
    "痘", "痘痘", "痘印", "泛红", "肤况", "皮肤状态",
})

_VISUAL_ACTION_CUES = frozenset({
    "看", "看看", "看一下", "看出来", "看得出",
    "发现", "明显", "明显吗", "有吗", "是不是", "像不像",
})


def route_evidence_query(query: str, *, has_current_media: bool = False) -> EvidenceRoute:
    """Classify what kind of evidence the current user query needs.

    Priority intentionally favors image evidence when the user is asking for a
    visual judgment. Text memory remains for "what did we say/recommend"
    questions.
    """

    text = str(query or "")
    history = _matches(text, _HISTORY_ANCHORS)
    image_objects = _matches(text, _IMAGE_OBJECT_CUES)
    skin_visuals = _matches(text, _SKIN_VISUAL_CUES)
    visual_actions = _matches(text, _VISUAL_ACTION_CUES)
    text_memory = _matches(text, _TEXT_MEMORY_CUES)

    if history and image_objects:
        return EvidenceRoute(
            kind="historical_image",
            reason="history_anchor_with_image_object",
            matched_history=history,
            matched_visual=image_objects,
            matched_text_memory=text_memory,
        )

    if history and skin_visuals and visual_actions:
        return EvidenceRoute(
            kind="historical_image",
            reason="visual_skin_question_with_history_anchor",
            matched_history=history,
            matched_visual=tuple(dict.fromkeys((*image_objects, *skin_visuals, *visual_actions))),
            matched_text_memory=text_memory,
        )

    if not has_current_media and image_objects and skin_visuals and visual_actions:
        return EvidenceRoute(
            kind="historical_image",
            reason="visual_skin_question_with_image_reference",
            matched_visual=tuple(dict.fromkeys((*image_objects, *skin_visuals, *visual_actions))),
            matched_text_memory=text_memory,
        )

    if not has_current_media and skin_visuals and visual_actions and not text_memory:
        return EvidenceRoute(
            kind="historical_image",
            reason="visual_skin_question_needs_image_evidence",
            matched_visual=tuple(dict.fromkeys((*skin_visuals, *visual_actions))),
        )

    if history and text_memory:
        return EvidenceRoute(
            kind="text_memory",
            reason="history_anchor_with_text_memory_cue",
            matched_history=history,
            matched_text_memory=text_memory,
        )

    if text_memory and not skin_visuals:
        return EvidenceRoute(
            kind="text_memory",
            reason="text_memory_cue",
            matched_text_memory=text_memory,
        )

    return EvidenceRoute(kind="none")


def needs_evidence_retrieval(query: str) -> bool:
    return route_evidence_query(query).kind != "none"


def _matches(text: str, keywords: frozenset[str]) -> tuple[str, ...]:
    return tuple(keyword for keyword in keywords if keyword in text)
