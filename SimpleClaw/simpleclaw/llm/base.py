"""抽象 LLM 提供方接口。

缓存约定
----------------
系统提示词缓存在提供方层面实现（例如火山引擎前缀缓存）。
启用缓存时，调用方在系统消息 dict 中嵌入可选的元数据键，再传入 stream()：

    {
        "role": "system",
        "content": "<完整的回退内容>",
        "_cache_tenant_key":    "<租户 id，用于缓存键隔离>",
        "_cache_stable_prefix": "<静态部分：行为规则 + 工具 schema>",
        "_cache_dynamic_tail":  "<动态部分：USER.md / 记忆 / 会话状态>",
    }

支持缓存的提供方（例如 VolcengineLLM）将：
  1. 预先缓存稳定前缀，并接收一个 response_id。
  2. 实际请求中仅发送动态尾部 + 对话历史，
     通过 previous_response_id 引用已缓存的前缀。

不支持缓存的提供方直接使用 "content"，忽略私有键。
ContextBuilder 负责填充这些键。
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator

from loguru import logger

from simpleclaw.llm.chunks import Chunk, TextChunk
from simpleclaw.llm.config import GeminiConfig, VolcengineConfig

# 所有已支持配置类型的联合类型 — 新增提供方时在此扩展。
ProviderConfig = VolcengineConfig | GeminiConfig


class LLMProvider(ABC):
    """ReactLoop 与任意 LLM 后端之间的契约。

    唯一必须实现的方法是 stream()，它必须 yield 规范化的
    TextChunk / ToolCallChunk 对象。所有解析、工具参数组装
    以及前缀缓存均在实现内部完成 — Loop 永远不会看到原始 API 事件。
    """

    _RETRY_DELAYS: tuple[int, ...] = (1, 2, 4)
    _TRANSIENT_MARKERS: tuple[str, ...] = (
        "429",
        "rate limit",
        "500",
        "502",
        "503",
        "504",
        "overloaded",
        "timeout",
        "timed out",
        "connection",
        "server error",
    )

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    # ------------------------------------------------------------------
    # 核心接口 — 子类必须实现
    # ------------------------------------------------------------------

    @abstractmethod
    async def stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        tool_choice: str | None = None,
    ) -> AsyncIterator[Chunk]:
        """将 LLM 响应以规范化的 TextChunk / ToolCallChunk 形式流式返回。

        实现负责：
        - 将增量的 input_json_delta 片段组装为完整的
          ToolCallChunk 对象，再 yield 出来。
        - 从文本增量中过滤 <think>…</think> 块（R1 风格模型）。
        - 在支持的情况下提取并复用前缀缓存条目。
        """
        # 为类型检查器明确返回类型，尽管这是抽象方法 —
        # 子类返回一个异步生成器。
        raise NotImplementedError
        yield TextChunk("")  # pragma: no cover  (makes this an async generator)

    # ------------------------------------------------------------------
    # 重试包装器 — 所有子类均可免费使用
    # ------------------------------------------------------------------

    async def stream_with_retry(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        tool_choice: str | None = None,
    ) -> AsyncIterator[Chunk]:
        """stream() 的自动重试包装，用于处理提供方的瞬时错误。

        最多重试 len(_RETRY_DELAYS) 次，采用指数退避策略。
        一旦流已开始 yield 数据块，将不再进行重试
        （因为无法回退已部分接收的响应）。
        """
        delays = list(self._RETRY_DELAYS) + [None]
        for attempt, delay in enumerate(delays, start=1):
            started = False
            try:
                async for chunk in self.stream(
                    messages,
                    tools,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    tool_choice=tool_choice,
                ):
                    started = True
                    yield chunk
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                err = str(exc)
                if started or delay is None or not self._is_transient(err):
                    # 流已开始、非瞬时错误，或重试次数已耗尽。
                    raise
                logger.warning(
                    "LLM 瞬时错误（第 {}/{} 次），{}s 后重试：{}",
                    attempt,
                    len(delays),
                    delay,
                    err[:120],
                )
                await asyncio.sleep(delay)

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @classmethod
    def _is_transient(cls, message: str) -> bool:
        lowered = message.lower()
        return any(marker in lowered for marker in cls._TRANSIENT_MARKERS)

    def _resolved(
        self,
        max_tokens: int | None,
        temperature: float | None,
    ) -> tuple[int, float]:
        """返回实际生效的 (max_tokens, temperature)，未传入时回退到配置默认值。"""
        return (
            max_tokens if max_tokens is not None else self.config.max_tokens,
            temperature if temperature is not None else self.config.temperature,
        )
