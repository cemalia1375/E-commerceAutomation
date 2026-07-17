"""ContextCompressor — 当 token 预算超出时裁剪消息窗口。

设计说明
--------
- 压缩结果明确分为 current / dropped：调用方可以直接用 current 替换
  热态消息窗口，并把 dropped 交给业务层做记忆提取、归档或审计。

- 丢弃边界始终对齐到 **UserMessage** — 我们不会在一轮中途切断
  （助手文本 + 工具结果属于同一轮，应保持完整）。

- 可选的 ``on_compress`` 回调以 asyncio 后台任务的方式接收被丢弃的片段。
  这是兼容旧调用方的轻量扩展点；需要强一致的业务应使用返回的
  ContextCompressionResult 自己处理 dropped。

用法
----
    compressor = ContextCompressor(
        max_tokens=6000,
        target_tokens=3000,
        min_keep_tokens=1500,
        on_compress=my_memory_extraction_fn,
    )

    result = await compressor.compress_window(loop.messages)
    if result.changed:
        loop.messages = result.current
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from typing import Awaitable, Callable

from simpleclaw.core.messages import AssistantMessage, UserMessage


# ---------------------------------------------------------------------------
# 默认 token 估算器
# ---------------------------------------------------------------------------

def _estimate_tokens(messages: list) -> int:
    """对内部 Message 对象进行粗略的 token 估算。

    CJK 字符按约 0.7 token/char 估算；其他文本沿用约 0.5 token/char。
    这是中文场景下比简单 char//2 更稳的轻量近似。
    """
    cjk_chars = 0
    other_chars = 0
    for msg in messages:
        cjk, other = _count_content_chars(getattr(msg, "content", "") or "")
        cjk_chars += cjk
        other_chars += other
        if isinstance(msg, AssistantMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                args = tc.arguments if isinstance(tc.arguments, str) else str(tc.arguments)
                cjk, other = _count_text_chars(args)
                cjk_chars += cjk
                other_chars += other
    return _estimate_from_counts(cjk_chars, other_chars)


def estimate_content_tokens(content: Any) -> int:
    """Estimate tokens for one message content value."""
    cjk, other = _count_content_chars(content)
    return _estimate_from_counts(cjk, other)


def _estimate_from_counts(cjk: int, other: int) -> int:
    return max(int(cjk * 0.7 + other * 0.5), 1)


def _count_content_chars(content: Any) -> tuple[int, int]:
    if isinstance(content, str):
        return _count_text_chars(content)
    if isinstance(content, list):
        cjk_total = 0
        other_total = 0
        for part in content:
            if isinstance(part, dict):
                text = part.get("text")
                if text is None and isinstance(part.get("image_url"), dict):
                    text = part["image_url"].get("url")
                elif text is None:
                    text = part.get("image_url") or part.get("url") or ""
            else:
                text = str(part)
            cjk, other = _count_text_chars(str(text or ""))
            cjk_total += cjk
            other_total += other
        return cjk_total, other_total
    return _count_text_chars(str(content))


def _count_text_chars(text: str) -> tuple[int, int]:
    cjk = 0
    other = 0
    for ch in text:
        if _is_cjk(ch):
            cjk += 1
        elif not ch.isspace():
            other += 1
    return cjk, other


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return (
        0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x3040 <= code <= 0x30FF
        or 0xAC00 <= code <= 0xD7AF
    )


# ---------------------------------------------------------------------------
# 压缩器
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ContextCompressionResult:
    """A compression decision for one message window."""

    current: list
    dropped: list = field(default_factory=list)
    dropped_count: int = 0
    tokens_before: int = 0
    tokens_after: int = 0
    cut_index: int | None = None

    @property
    def changed(self) -> bool:
        return self.dropped_count > 0


@dataclass(slots=True)
class ContextCompressionEvent:
    """Event emitted by ReactLoop after applying a compression result."""

    result: ContextCompressionResult
    history_offset_before: int
    history_offset_after: int
    iteration: int
    trigger: str = "before_llm_call"

    @property
    def dropped(self) -> list:
        return self.result.dropped

    @property
    def dropped_count(self) -> int:
        return self.result.dropped_count

    @property
    def tokens_before(self) -> int:
        return self.result.tokens_before

    @property
    def tokens_after(self) -> int:
        return self.result.tokens_after


class ContextCompressor:
    """基于估算 token 数量管理消息历史压缩。

    参数
    ----
    max_tokens:
        当估算的历史 token 数超过此值时触发压缩。
    target_tokens:
        压缩后持续丢弃轮次，直到历史大小降至此值或以下。
        必须小于 max_tokens。
    min_keep_tokens:
        累计 token 数达到此值的最新消息**永远不会**被丢弃，
        无论预算如何。作为近期消息保护机制。
    token_counter:
        可选的替代内置字符估算器的函数。
        签名：``(messages: list) -> int``。
    on_compress:
        异步回调，接收被丢弃的片段。以后台 asyncio 任务运行
        （触发后不等待）。传入 ``None`` 则跳过。
    """

    def __init__(
        self,
        *,
        max_tokens: int = 6000,
        target_tokens: int = 3000,
        min_keep_tokens: int = 1500,
        token_counter: Callable[[list], int] | None = None,
        on_compress: Callable[[list], Awaitable[None]] | None = None,
    ) -> None:
        if target_tokens >= max_tokens:
            raise ValueError("target_tokens must be less than max_tokens")
        if min_keep_tokens >= target_tokens:
            raise ValueError("min_keep_tokens must be less than target_tokens")

        self._max_tokens = max_tokens
        self._target_tokens = target_tokens
        self._min_keep_tokens = min_keep_tokens
        self._counter = token_counter or _estimate_tokens
        self._on_compress = on_compress

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------

    async def compress_window(self, messages: list) -> ContextCompressionResult:
        """Return a compression result for the current message window.

        如果估算 token 数超过 ``max_tokens``，则丢弃最旧的完整轮次，
        直到历史大小降至 ``target_tokens``，同时遵守
        ``min_keep_tokens`` 近期消息保护机制。
        """
        tokens_before = self._counter(messages)
        if tokens_before <= self._max_tokens:
            return ContextCompressionResult(
                current=messages,
                tokens_before=tokens_before,
                tokens_after=tokens_before,
            )

        cut = self._find_cut(messages)
        if cut is None:
            return ContextCompressionResult(
                current=messages,
                tokens_before=tokens_before,
                tokens_after=tokens_before,
            )

        dropped = messages[:cut]
        remaining = messages[cut:]
        tokens_after = self._counter(remaining)

        if self._on_compress and dropped:
            asyncio.create_task(self._on_compress(dropped))

        return ContextCompressionResult(
            current=remaining,
            dropped=dropped,
            dropped_count=len(dropped),
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            cut_index=cut,
        )

    async def maybe_compress(self, messages: list) -> list:
        """Backward-compatible API returning only the current window."""
        result = await self.compress_window(messages)
        return result.current

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _find_cut(self, messages: list) -> int | None:
        """返回切割索引，若不存在安全切割点则返回 None。

        规则：
        - 切割点必须是 UserMessage 的起始位置（轮次边界）。
        - 不得切入近期消息保护区。
        - 持续丢弃轮次，直到剩余 token 数 ≤ target_tokens。
        """
        # 轮次起始索引（UserMessage 边界）
        turn_starts = [i for i, m in enumerate(messages) if isinstance(m, UserMessage)]

        # 至少需要两轮才能丢弃第一轮
        if len(turn_starts) < 2:
            return None

        # 近期保护：从末尾向前统计，找到保护区起始位置
        protected_from = len(messages)
        accumulated = 0
        for i in range(len(messages) - 1, -1, -1):
            accumulated += self._counter([messages[i]])
            if accumulated >= self._min_keep_tokens:
                protected_from = i
                break

        # 从最旧的轮次边界向后遍历，找到满足预算的第一个切割点
        chosen_cut = None
        for boundary in turn_starts[1:]:     # 跳过索引 0 — 不能在第一轮之前切割
            if boundary >= protected_from:
                break
            chosen_cut = boundary
            if self._counter(messages[boundary:]) <= self._target_tokens:
                break

        return chosen_cut
