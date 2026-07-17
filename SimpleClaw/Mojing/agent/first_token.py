"""First-token opener lane.

This lane is deliberately outside the main ReactLoop:
- no tools
- no cold-path reminder
- no main-agent dynamic sections
- stable opener prompt in prefix cache plus a small dynamic conversation tail
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger

from simpleclaw.core.messages import AssistantMessage, ToolResultMessage, UserMessage
from simpleclaw.llm.config import VolcengineConfig
from simpleclaw.llm.chunks import TextChunk
from simpleclaw.llm.volcengine import VolcengineLLM

_FIRST_TOKEN_PROMPT_DIR = Path(__file__).parent.parent / "workspace"
_DEFAULT_FIRST_TOKEN_PROMPT_PATH = _FIRST_TOKEN_PROMPT_DIR / "first_token.md"
_DEVICE_FIRST_TOKEN_PROMPT_DIR = _FIRST_TOKEN_PROMPT_DIR / "device"
_MAX_ACTIVE_WINDOW_CHARS = 3200
_MAX_LAST_REPLY_CHARS = 1200
_MEDIA_OPENER_SIGNAL = (
    "【本轮输入信号】用户本轮上传了图片。你只需要输出一句轻量开场，"
    "不要描述图片内容，不要判断清晰度，不要做分析，不要承诺已经看完或开始处理。"
)


@dataclass(slots=True)
class FirstTokenResult:
    text: str
    response_id: str
    cache_hit: bool


@dataclass(slots=True)
class _SharedContext:
    active_window_text: str
    last_assistant_reply: str
    context_version: int
    context_fingerprint: str


class FirstTokenAgent:
    """Generate a short opener through a no-tools Responses prefix cache."""

    provider = "volcengine"
    lane = "opener"
    cache_mode = "prefix"

    def __init__(
        self,
        *,
        llm: VolcengineLLM,
        cache_repo: Any | None = None,
        timeout_s: float = 0.8,
        enabled: bool = True,
    ) -> None:
        self._llm = llm
        self.config = llm.config
        self._cache_repo = cache_repo
        self.timeout_s = timeout_s
        self.enabled = enabled
        self._prompt_cache: dict[str, tuple[str, str]] = {}

    async def generate(
        self,
        *,
        tenant_key: str,
        session_key: str,
        user_message: str,
        history: list,
        consolidated_from: int,
        history_offset: int | None = None,
        agent_lane: str | None = None,
        prompt_surface: str = "app",
    ) -> FirstTokenResult | None:
        """Return a short opener and persist the new session response id."""
        return await self.generate_stream(
            tenant_key=tenant_key,
            session_key=session_key,
            user_message=user_message,
            history=history,
            consolidated_from=consolidated_from,
            history_offset=history_offset,
            agent_lane=agent_lane,
            prompt_surface=prompt_surface,
            on_token=None,
        )

    async def generate_stream(
        self,
        *,
        tenant_key: str,
        session_key: str,
        user_message: str,
        history: list,
        consolidated_from: int,
        history_offset: int | None = None,
        agent_lane: str | None = None,
        prompt_surface: str = "app",
        on_token: Callable[[str], Awaitable[None]] | None = None,
    ) -> FirstTokenResult | None:
        """Stream a short opener using a cached stable prompt prefix."""
        if not self.enabled:
            return None
        resolved_lane = _normalize_agent_lane(agent_lane or _infer_agent_lane(session_key))
        resolved_surface = _normalize_prompt_surface(prompt_surface)
        system_prompt, prompt_fingerprint = self._prompt_for_lane(resolved_lane, resolved_surface)
        if not system_prompt.strip():
            return None
        if not user_message.strip():
            return None

        shared = _build_shared_context(
            history,
            consolidated_from=consolidated_from,
            history_offset=history_offset,
        )
        messages = self._build_input(
            shared,
            user_message,
            system_prompt,
            agent_lane=resolved_lane,
            prompt_surface=resolved_surface,
        )
        visible_parts: list[str] = []
        first_delta_logged = False
        logger.info(
            "first_token start tenant={} session={} agent_lane={} surface={} model={} cache_mode=prefix messages={} active_chars={} last_reply_chars={}",
            tenant_key,
            session_key,
            resolved_lane,
            resolved_surface,
            self.config.model,
            len(messages),
            len(shared.active_window_text),
            len(shared.last_assistant_reply),
        )

        async def _on_text(delta: str) -> None:
            nonlocal first_delta_logged
            if not first_delta_logged:
                logger.info("first_token first_delta tenant={} session={}", tenant_key, session_key)
                first_delta_logged = True
            remaining = 80 - sum(len(part) for part in visible_parts)
            if remaining <= 0:
                return
            clipped = delta[:remaining]
            visible_parts.append(clipped)
            if on_token is not None and clipped:
                await on_token(clipped)

        async for chunk in self._llm.stream_with_retry(messages, tools=None):
            if isinstance(chunk, TextChunk):
                await _on_text(chunk.token)

        text = _normalize_opener("".join(visible_parts))
        if not text:
            return None

        logger.info(
            "first_token generated tenant={} session={} cache_mode=prefix chars={}",
            tenant_key, session_key, len(text),
        )
        return FirstTokenResult(text=text, response_id="", cache_hit=False)

    def _build_input(
        self,
        shared: _SharedContext,
        user_message: str,
        system_prompt: str,
        *,
        agent_lane: str = "main",
        prompt_surface: str = "app",
    ) -> list[dict[str, Any]]:
        dynamic_tail = _build_dynamic_tail(shared, user_message)
        cache_lane = f"first_token:{_normalize_prompt_surface(prompt_surface)}:{_normalize_agent_lane(agent_lane)}"
        return [
            {
                "role": "system",
                "content": system_prompt,
                "_cache_lane": cache_lane,
                "_cache_tenant_key": "__first_token__",
                "_cache_session_key": "__shared__",
            },
            {
                "role": "user",
                "content": dynamic_tail,
            },
        ]

    def _build_input_items(
        self,
        shared: _SharedContext,
        user_message: str,
        system_prompt: str,
        *,
        agent_lane: str = "main",
        prompt_surface: str = "app",
    ) -> list[dict[str, Any]]:
        """Compatibility helper for tests/debug renderers that expect Responses items."""
        messages = self._build_input(
            shared,
            user_message,
            system_prompt,
            agent_lane=agent_lane,
            prompt_surface=prompt_surface,
        )
        return [{
            "type": "message",
            "role": str(msg.get("role") or ""),
            "content": str(msg.get("content") or ""),
        } for msg in messages]

    def _prompt_for_lane(self, agent_lane: str, prompt_surface: str) -> tuple[str, str]:
        cache_key = f"{agent_lane}:{prompt_surface}"
        cached = self._prompt_cache.get(cache_key)
        if cached is not None:
            return cached
        prompt = _load_prompt(agent_lane, prompt_surface)
        fingerprint = _hash(f"{agent_lane}\n{prompt_surface}\n{prompt}")
        cached = (prompt, fingerprint)
        self._prompt_cache[cache_key] = cached
        return cached


def _build_dynamic_tail(shared: _SharedContext, user_message: str) -> str:
    sections: list[str] = []
    if shared.active_window_text:
        sections.append("【最近主对话】\n" + shared.active_window_text)
    sections.append("【本轮输入】\n用户：" + str(user_message or "").strip())
    return "\n\n".join(section for section in sections if section.strip()).strip()


def _is_followed_by_assistant(messages: list, index: int) -> bool:
    next_index = index + 1
    return next_index < len(messages) and isinstance(messages[next_index], AssistantMessage)


def _load_prompt(agent_lane: str = "main", prompt_surface: str = "app") -> str:
    path = _prompt_path_for_lane(agent_lane, prompt_surface)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _prompt_path_for_lane(agent_lane: str, prompt_surface: str = "app") -> Path:
    lane = _normalize_agent_lane(agent_lane)
    surface = _normalize_prompt_surface(prompt_surface)
    candidates: list[Path] = []
    if surface == "device":
        if lane != "main":
            candidates.append(_DEVICE_FIRST_TOKEN_PROMPT_DIR / f"first_token.{lane}.md")
        candidates.append(_DEVICE_FIRST_TOKEN_PROMPT_DIR / "first_token.md")
    if lane != "main":
        candidates.append(_FIRST_TOKEN_PROMPT_DIR / f"first_token.{lane}.md")
    for path in candidates:
        if path.exists():
            return path
    return _DEFAULT_FIRST_TOKEN_PROMPT_PATH


def _infer_agent_lane(session_key: str) -> str:
    prefix, sep, _ = str(session_key or "").partition(":")
    if sep and prefix:
        return _normalize_agent_lane(prefix)
    return "main"


def _normalize_agent_lane(agent_lane: str) -> str:
    lane = str(agent_lane or "").strip().lower().replace("-", "_")
    return lane or "main"


def _normalize_prompt_surface(prompt_surface: str) -> str:
    surface = str(prompt_surface or "app").strip().lower()
    if surface not in {"app", "device"}:
        return "app"
    return surface


def join_first_token_reply(first_token_reply: str, assistant_reply: str) -> str:
    """Join the visible first-token opener and the real assistant reply."""
    opener = str(first_token_reply or "").strip()
    reply = str(assistant_reply or "").lstrip()
    if opener and reply:
        return f"{opener}\n{reply}"
    return opener or reply


def build_first_token_context_message(first_token_reply: str) -> str:
    """Build the hidden assistant message shown only to the main LLM.

    The visible opener has already been streamed to the user. This wrapper
    makes that boundary explicit so the main lane treats the next output as a
    continuation, not as another chance to greet or acknowledge.
    """

    opener = str(first_token_reply or "").strip()
    if not opener:
        return ""
    return (
        "【已发送给用户的第一气泡，可以延续这条消息继续输出】\n"
        f"{opener}"
    )


def build_first_token_continuation_instruction(first_token_reply: str) -> str:
    """Build the one-turn hidden instruction that hands control back to main LLM."""
    opener = str(first_token_reply or "").strip()
    if not opener:
        return ""
    return (
        "上面 assistant 内容已经作为本轮第一气泡发送给用户。\n"
        "你接下来必须从第一气泡之后继续，只输出后续新增内容。\n"
        "不要复述、改写或同义重复第一气泡。\n"
        f"已发送内容：{opener}"
    )


def build_first_token_user_message(user_message: str, media: list[str] | None = None) -> str:
    """Build the no-tools opener input.

    The opener lane must be able to react to pure-image turns, but it should
    never inspect or describe the actual image. The main agent owns image
    judgment and tool decisions.
    """
    text = str(user_message or "").strip()
    has_media = any(str(ref or "").strip() for ref in (media or []))
    if not has_media:
        return text
    if not text:
        return _MEDIA_OPENER_SIGNAL
    return f"{text}\n\n{_MEDIA_OPENER_SIGNAL}"


def _thinking_type(config: VolcengineConfig) -> str:
    return "enabled" if config.thinking else "disabled"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _build_shared_context(
    history: list,
    *,
    consolidated_from: int,
    history_offset: int | None = None,
) -> _SharedContext:
    active = history[consolidated_from:]
    active_text = _render_active_window(active)
    context_version = history_offset if history_offset is not None else consolidated_from
    return _SharedContext(
        active_window_text=active_text,
        last_assistant_reply=_last_assistant_reply(active),
        context_version=int(context_version or 0),
        context_fingerprint=_hash(active_text),
    )


def _render_active_window(messages: list) -> str:
    lines: list[str] = []
    for index, msg in enumerate(messages):
        if isinstance(msg, UserMessage):
            text = _content_to_text(msg.content)
            if text:
                lines.append(f"用户：{text}")
        elif isinstance(msg, AssistantMessage):
            if _is_followed_by_assistant(messages, index):
                continue
            if msg.content:
                lines.append(f"助手：{msg.content}")
        elif isinstance(msg, ToolResultMessage):
            continue
        if sum(len(line) for line in lines) >= _MAX_ACTIVE_WINDOW_CHARS:
            break
    text = "\n".join(lines)
    if len(text) <= _MAX_ACTIVE_WINDOW_CHARS:
        return text
    return text[-_MAX_ACTIVE_WINDOW_CHARS:]


def _last_assistant_reply(messages: list) -> str:
    """Return the latest assistant text in the active business history."""
    for msg in reversed(messages):
        if isinstance(msg, AssistantMessage) and msg.content.strip():
            text = msg.content.strip()
            return text[-_MAX_LAST_REPLY_CHARS:]
    return ""


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return str(content or "").strip()
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict):
            if item.get("type") in {"image_url", "input_image"} or "image_url" in item:
                parts.append("[用户上传了图片]")
            else:
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text))
        elif item:
            parts.append(str(item))
    return "\n".join(p.strip() for p in parts if p and str(p).strip()).strip()


def _normalize_opener(text: str) -> str:
    text = " ".join(str(text or "").split())
    if not text:
        return ""
    # Keep this lane visibly short even if the model ignores max_output_tokens.
    return text[:80]
