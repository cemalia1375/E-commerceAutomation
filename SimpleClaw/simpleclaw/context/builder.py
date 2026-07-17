"""ContextBuilder — 为 LLM 组装最终消息列表。

稳定部分 / 动态部分拆分
------------------------
用法
----
    builder = ContextBuilder(
        stable_sections=["# Agent\n...", "# Soul\n..."],
        dynamic_context_providers=[MemoryDynamicContextProvider(mysql_memory)],
        tenant_key="user_001",
    )
    messages = await builder.build(
        history=loop.messages,
        dynamic_context_sections=[ContextSection(content="## User Profile\n...")],
        query="用户当前消息",
    )
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from simpleclaw.core.messages import AssistantMessage, ToolResultMessage, UserMessage
from simpleclaw.context.providers import (
    AttentionPacket,
    AttentionProvider,
    ContextBuildContext,
    ContextSection,
    DynamicContextProvider,
    StablePromptProvider,
)
from simpleclaw.skills import SkillRegistry

_HISTORICAL_IMAGE_PLACEHOLDER = "[用户已上传图片]"
_FETCHED_IMAGE_ACTION = "image_fetched"


class ContextBuilder:
    """组装消息，并对系统提示词进行稳定/动态部分拆分。"""

    _SEP = "\n\n---\n\n"

    def __init__(
        self,
        stable_sections: list[str],
        *,
        stable_prompt_providers: list[StablePromptProvider] | None = None,
        dynamic_context_providers: list[DynamicContextProvider] | None = None,
        attention_providers: list[AttentionProvider] | None = None,
        skill_registry: SkillRegistry | None = None,
        include_skill_index: bool = False,
        tenant_key: str = "__default__",
        cache_lane: str = "agent",
        cache_session_key: str = "",
    ) -> None:
        self._stable_sections = stable_sections
        self._stable_prompt_providers = list(stable_prompt_providers or [])
        self._dynamic_context_providers = list(dynamic_context_providers or [])
        self._attention_providers = list(attention_providers or [])
        self._skill_registry = skill_registry
        self._include_skill_index = include_skill_index
        self._tenant_key = tenant_key
        self._cache_lane = cache_lane
        self._cache_session_key = cache_session_key
        self._attention_signatures: dict[str, str] = {}
        self._attention_counts: dict[str, int] = {}
        self._active_scene_skills: dict[str, str] = {}

    @property
    def skill_registry(self) -> SkillRegistry | None:
        return self._skill_registry

    @property
    def active_skill_names(self) -> list[str]:
        return list(self._active_scene_skills)

    def activate_skill(self, name: str) -> str:
        """Activate one scene skill for later ContextBuilder.build() calls.

        Returns the skill materialization mode ("observation" or "scene").
        Observation skills are not persisted as active scene state.
        """
        if self._skill_registry is None:
            raise RuntimeError("skill registry is not configured on this ContextBuilder")

        document = self._skill_registry.require(name)
        materialization = document.descriptor.materialization
        if materialization == "scene":
            self._active_scene_skills[document.descriptor.name] = self._skill_registry.render_skill_body(
                document.descriptor.name
            )
        return materialization

    def deactivate_skill(self, name: str) -> None:
        self._active_scene_skills.pop(str(name or "").strip(), None)

    def clear_active_skills(self) -> None:
        self._active_scene_skills.clear()

    def render_skill_body(self, name: str) -> str:
        if self._skill_registry is None:
            raise RuntimeError("skill registry is not configured on this ContextBuilder")
        return self._skill_registry.render_skill_body(name)

    async def build(
        self,
        history: list,
        *,
        dynamic_context_sections: list[ContextSection] | None = None,
        attention_packets: list[AttentionPacket] | None = None,
        metadata: dict[str, Any] | None = None,
        query: str = "",
    ) -> list[dict]:
        """构建供 LLMProvider.stream() 使用的最终消息列表。

        Args:
            history:          ReactLoop.messages（内部 Message 对象）
            dynamic_context_sections:
                              本轮结构化动态上下文；业务层不要直接拼系统提示。
            attention_packets:       本轮结构化注意力包。
            metadata:                provider 可读的调用方元数据，框架不解释其业务含义。
            query:            当前用户输入，用作记忆检索查询
        """
        ctx = ContextBuildContext(
            history=history,
            query=query,
            tenant_key=self._tenant_key,
            cache_lane=self._cache_lane,
            cache_session_key=self._cache_session_key,
            metadata=metadata or {},
            active_skills=self.active_skill_names,
        )

        # --- 稳定前缀 ---
        stable_parts = [s for s in self._stable_sections if s and s.strip()]
        if self._include_skill_index and self._skill_registry is not None:
            skill_index = self._skill_registry.render_index()
            if skill_index:
                stable_parts.append(skill_index)
        for provider in self._stable_prompt_providers:
            sections = await provider.collect_stable_prompt(ctx)
            stable_parts.extend(
                section.content
                for section in sections
                if section.content and section.content.strip()
            )
        stable_prefix = self._SEP.join(stable_parts)

        # --- 动态尾部 ---
        dynamic_parts = [
            section.content
            for section in dynamic_context_sections or []
            if section.content and section.content.strip()
        ]

        for provider in self._dynamic_context_providers:
            sections = await provider.collect_dynamic_context(ctx)
            dynamic_parts.extend(
                section.content
                for section in sections
                if section.content and section.content.strip()
            )

        dynamic_parts.extend(
            content
            for content in self._active_scene_skills.values()
            if content and content.strip()
        )

        dynamic_parts = [s for s in dynamic_parts if s and s.strip()]
        dynamic_tail = self._SEP.join(dynamic_parts)

        full_prompt = self._SEP.join(p for p in [stable_prefix, dynamic_tail] if p)

        result: list[dict] = []

        if full_prompt:
            result.append({
                "role": "system",
                "content": full_prompt,
                "_cache_stable_prefix": stable_prefix,
                "_cache_dynamic_tail":  dynamic_tail,
                "_cache_tenant_key":    self._tenant_key,
                "_cache_lane":          self._cache_lane,
                "_cache_session_key":   self._cache_session_key,
            })

        # 找到最后一条用户消息在 history 中的索引，用于插入 attention。
        last_user_idx: int | None = None
        for i, msg in enumerate(history):
            if isinstance(msg, UserMessage):
                last_user_idx = i

        packets = await self._collect_attention_packets(
            ctx,
            attention_packets=attention_packets,
        )
        before_last_user_packets = [
            packet for packet in packets
            if packet.placement == "before_last_user" and last_user_idx is not None
        ]
        after_history_packets = [
            packet for packet in packets
            if packet.placement == "after_history"
            or (packet.placement == "before_last_user" and last_user_idx is None)
        ]
        tail_packets = [
            packet for packet in packets
            if packet.placement == "tail"
        ]

        # --- 对话历史 ---
        fetched_image_contexts: list[dict] = []
        for idx, msg in enumerate(history):
            if isinstance(msg, UserMessage):
                # Attention 冒泡：紧贴生成点，插在最后一条用户消息之前
                if idx == last_user_idx:
                    result.extend(_render_attention_packets(before_last_user_packets))
                content = msg.content
                if idx != last_user_idx:
                    content = _replace_historical_images(content)
                result.append({"role": "user", "content": content})

            elif isinstance(msg, AssistantMessage):
                if last_user_idx is not None and idx < last_user_idx and _is_followed_by_assistant(history, idx):
                    continue
                if msg.tool_calls:
                    result.append({
                        "role": "assistant",
                        "content": msg.content,
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.name,
                                    "arguments": tc.arguments,
                                },
                                # Gemini-only：opaque bytes，必须原样回传，否则 400
                                "thought_signature": tc.thought_signature,
                            }
                            for tc in msg.tool_calls
                        ],
                        # text Part 上的 signatures，回放时转为前置 thought Parts
                        "pending_signatures": msg.pending_signatures,
                    })
                else:
                    result.append({
                        "role": "assistant",
                        "content": msg.content,
                        "pending_signatures": msg.pending_signatures,
                    })

            elif isinstance(msg, ToolResultMessage):
                result.append({
                    "role": "tool",
                    "tool_call_id": msg.call_id,
                    "content": msg.content,
                })
                if last_user_idx is not None and idx > last_user_idx:
                    image_context = _build_fetched_image_context(msg.content)
                    if image_context:
                        fetched_image_contexts.append(image_context)

        for image_context in fetched_image_contexts:
            result.append(image_context)

        result.extend(_render_attention_packets(after_history_packets))
        result.extend(_render_attention_packets(tail_packets))

        return result

    async def _collect_attention_packets(
        self,
        ctx: ContextBuildContext,
        *,
        attention_packets: list[AttentionPacket] | None,
    ) -> list[AttentionPacket]:
        packets: list[tuple[int, AttentionPacket]] = []
        order = 0

        def add(packet: AttentionPacket) -> None:
            nonlocal order
            packets.append((order, packet))
            order += 1

        for packet in attention_packets or []:
            add(packet)

        for provider in self._attention_providers:
            for packet in await provider.collect_attention(ctx):
                add(packet)

        sorted_packets = [
            packet
            for _, packet in sorted(packets, key=lambda item: (item[1].priority, item[0]))
            if _has_attention_content(packet)
        ]
        return [
            packet
            for packet in sorted_packets
            if self._should_emit_attention_packet(packet)
        ]

    def _should_emit_attention_packet(self, packet: AttentionPacket) -> bool:
        lifetime = packet.lifetime
        if lifetime in {"one_turn", "always"}:
            return True

        key = _attention_packet_key(packet)
        signature = _attention_packet_signature(packet)
        previous = self._attention_signatures.get(key)

        if lifetime == "until_changed":
            if previous == signature:
                return False
            self._attention_signatures[key] = signature
            return True

        if lifetime == "periodic":
            count = self._attention_counts.get(key, 0) + 1
            self._attention_counts[key] = count
            interval = _attention_packet_interval(packet)
            changed = previous != signature
            if changed:
                self._attention_signatures[key] = signature
                return True
            if interval <= 1 or count % interval == 0:
                return True
            return False

        return True


def _replace_historical_images(content):
    """Replace historical image inputs with a text marker.

    Only the current user turn should send real image_url parts to the LLM.
    Older images remain available through image analysis tables and USER.md,
    but should not be re-sent as multimodal input on every later turn.
    """
    if not isinstance(content, list):
        return content

    text_parts: list[str] = []
    image_count = 0

    for item in content:
        if not isinstance(item, dict):
            text = str(item).strip()
            if text:
                text_parts.append(text)
            continue

        item_type = item.get("type")
        if item_type in {"image_url", "input_image"} or "image_url" in item:
            image_count += 1
            continue

        if item_type in {"text", "input_text"}:
            text = str(item.get("text") or "").strip()
            if text:
                text_parts.append(text)
            continue

        text = str(item.get("text") or "").strip()
        if text:
            text_parts.append(text)

    if image_count:
        text_parts.append(
            _HISTORICAL_IMAGE_PLACEHOLDER
            if image_count == 1
            else f"{_HISTORICAL_IMAGE_PLACEHOLDER} x{image_count}"
        )

    return "\n".join(text_parts).strip() or _HISTORICAL_IMAGE_PLACEHOLDER


def _is_followed_by_assistant(messages: list, index: int) -> bool:
    """Return whether this content-only assistant is followed by assistant output.

    The caller decides whether the message belongs to a completed historical
    turn. Current-turn first token openers must remain visible across all ReAct
    iterations until the user turn finishes.
    """
    msg = messages[index] if 0 <= index < len(messages) else None
    if not isinstance(msg, AssistantMessage) or msg.tool_calls:
        return False
    return index + 1 < len(messages) and isinstance(messages[index + 1], AssistantMessage)


def _build_fetched_image_context(tool_content: str) -> dict | None:
    payload = _parse_json_object(tool_content)
    if not payload:
        return None
    if str(payload.get("action") or "").strip() != _FETCHED_IMAGE_ACTION:
        return None

    image_url = str(payload.get("image_url") or "").strip()
    if not image_url:
        return None

    focus = str(payload.get("message_focus") or "").strip()
    uploaded_at = str(payload.get("uploaded_at") or "").strip()
    source = str(payload.get("source") or "").strip()
    details = []
    if uploaded_at:
        details.append(f"上传时间：{uploaded_at}")
    if source:
        details.append(f"来源：{source}")
    detail_text = "\n".join(details)
    text = (
        "【系统补充的历史图片】\n"
        f"{focus or '这是一张用户之前上传的历史图片，请结合当前问题看图回答。'}"
    )
    if detail_text:
        text += "\n" + detail_text

    return {
        "role": "user",
        "content": [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": image_url}},
        ],
    }


def _has_attention_content(packet: AttentionPacket) -> bool:
    content = packet.content
    if isinstance(content, str):
        return bool(content.strip())
    return bool(content)


def _render_attention_packets(packets: list[AttentionPacket]) -> list[dict]:
    return [_render_attention_packet(packet) for packet in packets]


def _render_attention_packet(packet: AttentionPacket) -> dict:
    role = packet.role
    if role == "system" and isinstance(packet.content, list):
        # Multimodal content must be rendered as user content for OpenAI-style
        # APIs. Providers can set role="user" explicitly; this keeps the
        # default safe for image packets.
        role = "user"
    return {
        "role": role,
        "content": packet.content,
    }


def _attention_packet_key(packet: AttentionPacket) -> str:
    return f"{packet.source}:{packet.placement}:{packet.role}"


def _attention_packet_signature(packet: AttentionPacket) -> str:
    payload = {
        "content": packet.content,
        "metadata": packet.metadata,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _attention_packet_interval(packet: AttentionPacket) -> int:
    raw = packet.metadata.get("interval", packet.metadata.get("period", 1))
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1


def _parse_json_object(content: str) -> dict[str, Any] | None:
    try:
        value = json.loads(content)
    except Exception:
        return None
    return value if isinstance(value, dict) else None
