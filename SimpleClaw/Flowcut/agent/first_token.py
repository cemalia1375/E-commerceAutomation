"""First-token opener lane.

This lane is deliberately outside the main ReactLoop:
- no tools
- no cold-path reminder
- no main-agent dynamic sections
- only a stable opener prompt plus shared conversation history
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable

from loguru import logger

from simpleclaw.core.messages import AssistantMessage, ToolResultMessage, UserMessage
from simpleclaw.llm.config import VolcengineConfig
from simpleclaw.llm.volcengine import VolcengineLLM


@runtime_checkable
class _CacheRepo(Protocol):
    """LLM 缓存仓库最小接口，由调用方注入真实实现。"""

    async def get_session_cache(
        self,
        *,
        provider: str,
        lane: str,
        tenant_key: str,
        session_key: str,
        model: str,
        thinking_type: str,
        cache_mode: str,
        prompt_fingerprint: str,
        context_version: int,
    ) -> Any | None: ...

    async def upsert_session_cache(self, **kwargs: Any) -> None: ...

    async def invalidate_session_cache(
        self,
        *,
        provider: str,
        lane: str,
        tenant_key: str,
        session_key: str,
        cache_mode: str,
    ) -> None: ...

_FIRST_TOKEN_PROMPT_DIR = Path(__file__).parent.parent / "workspace"
_DEFAULT_FIRST_TOKEN_PROMPT_PATH = _FIRST_TOKEN_PROMPT_DIR / "first_token.md"
_CACHE_EXPIRE_S = 2 * 24 * 60 * 60
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
    """Generate a short opener through a no-tools Responses session cache."""

    provider = "volcengine"
    lane = "opener"
    cache_mode = "session_chain"

    def __init__(
        self,
        *,
        llm: VolcengineLLM,
        cache_repo: "_CacheRepo",
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
        agent_lane: str | None = None,
    ) -> FirstTokenResult | None:
        """Return a short opener and persist the new session response id."""
        return await self.generate_stream(
            tenant_key=tenant_key,
            session_key=session_key,
            user_message=user_message,
            history=history,
            consolidated_from=consolidated_from,
            agent_lane=agent_lane,
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
        agent_lane: str | None = None,
        on_token: Callable[[str], Awaitable[None]] | None,
    ) -> FirstTokenResult | None:
        """Stream a short opener, buffer it, then persist the session response id."""
        if not self.enabled:
            return None
        resolved_lane = _normalize_agent_lane(agent_lane or _infer_agent_lane(session_key))
        system_prompt, prompt_fingerprint = self._prompt_for_lane(resolved_lane)
        if not system_prompt.strip():
            return None
        if not user_message.strip():
            return None

        shared = _build_shared_context(history, consolidated_from=consolidated_from)
        record = await self._cache_repo.get_session_cache(
            provider=self.provider,
            lane=self.lane,
            tenant_key=tenant_key,
            session_key=session_key,
            model=self.config.model,
            thinking_type=_thinking_type(self.config),
            cache_mode=self.cache_mode,
            prompt_fingerprint=prompt_fingerprint,
            context_version=shared.context_version,
        )

        input_items = self._build_input(record, shared, user_message, system_prompt)
        visible_parts: list[str] = []
        first_delta_logged = False
        logger.info(
            "first_token start tenant={} session={} agent_lane={} model={} cache_hit={} input_items={} prev_resp={} active_chars={} last_reply_chars={}",
            tenant_key,
            session_key,
            resolved_lane,
            self.config.model,
            bool(record),
            len(input_items),
            (record.response_id if record else "")[:24],
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

        response = await self._llm.stream_session(
            input_items=input_items,
            previous_response_id=record.response_id if record else None,
            max_tokens=self.config.max_tokens,
            temperature=self.config.temperature,
            on_text=_on_text,
        )
        response_id = response.response_id
        text = _normalize_opener("".join(visible_parts) or response.output_text)
        if not text:
            await self._cache_repo.invalidate_session_cache(
                provider=self.provider,
                lane=self.lane,
                tenant_key=tenant_key,
                session_key=session_key,
                cache_mode=self.cache_mode,
            )
            return None

        base_response_id = (record.base_response_id or record.response_id) if record else response_id
        turn_count = (record.turn_count if record else 0) + 1
        expire_at = int(time.time()) + _CACHE_EXPIRE_S
        try:
            await self._cache_repo.upsert_session_cache(
                provider=self.provider,
                lane=self.lane,
                tenant_key=tenant_key,
                session_key=session_key,
                model=self.config.model,
                thinking_type=_thinking_type(self.config),
                cache_mode=self.cache_mode,
                prompt_fingerprint=prompt_fingerprint,
                context_version=shared.context_version,
                main_consolidated_from=consolidated_from,
                context_fingerprint=shared.context_fingerprint,
                response_id=response_id,
                base_response_id=base_response_id,
                turn_count=turn_count,
                expire_at=expire_at,
                metadata={
                    "agent_lane": resolved_lane,
                    "cache_hit": bool(record),
                    "last_reply_chars": len(shared.last_assistant_reply),
                    "active_window_chars": len(shared.active_window_text),
                    "streamed": bool(on_token),
                },
            )
        except Exception as exc:
            logger.warning(
                "first_token session cache upsert failed tenant={} session={}: {}",
                tenant_key, session_key, exc,
            )
        logger.info(
            "first_token generated tenant={} session={} cache_hit={} chars={} resp_id={}",
            tenant_key, session_key, bool(record), len(text), response_id[:24],
        )
        return FirstTokenResult(text=text, response_id=response_id, cache_hit=bool(record))

    def _build_input(
        self,
        record: Any | None,
        shared: _SharedContext,
        user_message: str,
        system_prompt: str,
    ) -> list[dict[str, Any]]:
        if record is not None:
            items: list[dict[str, Any]] = []
            if shared.last_assistant_reply:
                items.append({
                    "type": "message",
                    "role": "assistant",
                    "content": (
                        "上一轮最终回复，仅作为对话连续性上下文：\n"
                        f"{shared.last_assistant_reply}"
                    ),
                })
            items.append({"type": "message", "role": "user", "content": user_message})
            return items

        items = [{
            "type": "message",
            "role": "system",
            "content": system_prompt,
        }]
        if shared.active_window_text:
            items.append({
                "type": "message",
                "role": "user",
                "content": "以下是近期共享对话历史，只用于保持上下文连续：\n" + shared.active_window_text,
            })
        items.append({"type": "message", "role": "user", "content": user_message})
        return items

    def _prompt_for_lane(self, agent_lane: str) -> tuple[str, str]:
        cached = self._prompt_cache.get(agent_lane)
        if cached is not None:
            return cached
        prompt = _load_prompt(agent_lane)
        fingerprint = _hash(f"{agent_lane}\n{prompt}")
        cached = (prompt, fingerprint)
        self._prompt_cache[agent_lane] = cached
        return cached


def _load_prompt(agent_lane: str = "main") -> str:
    path = _prompt_path_for_lane(agent_lane)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _prompt_path_for_lane(agent_lane: str) -> Path:
    lane = _normalize_agent_lane(agent_lane)
    if lane == "main":
        return _DEFAULT_FIRST_TOKEN_PROMPT_PATH
    lane_path = _FIRST_TOKEN_PROMPT_DIR / f"first_token.{lane}.md"
    if lane_path.exists():
        return lane_path
    return _DEFAULT_FIRST_TOKEN_PROMPT_PATH


def _infer_agent_lane(session_key: str) -> str:
    prefix, sep, _ = str(session_key or "").partition(":")
    if sep and prefix:
        return _normalize_agent_lane(prefix)
    return "main"


def _normalize_agent_lane(agent_lane: str) -> str:
    lane = str(agent_lane or "").strip().lower().replace("-", "_")
    return lane or "main"


def join_first_token_reply(first_token_reply: str, assistant_reply: str) -> str:
    """Join the visible first-token opener and the real assistant reply."""
    opener = str(first_token_reply or "").strip()
    reply = str(assistant_reply or "").lstrip()
    if opener and reply:
        return f"{opener}\n\n{reply}"
    return opener or reply


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


def _build_shared_context(history: list, *, consolidated_from: int) -> _SharedContext:
    active = history[consolidated_from:]
    active_text = _render_active_window(active)
    return _SharedContext(
        active_window_text=active_text,
        last_assistant_reply=_last_assistant_reply(active),
        context_version=int(consolidated_from or 0),
        context_fingerprint=_hash(active_text),
    )


def _render_active_window(messages: list) -> str:
    lines: list[str] = []
    for msg in messages:
        if isinstance(msg, UserMessage):
            text = _content_to_text(msg.content)
            if text:
                lines.append(f"用户：{text}")
        elif isinstance(msg, AssistantMessage):
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
