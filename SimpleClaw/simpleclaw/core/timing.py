"""轻量 TTFT 计时工具。

一轮对话的起始时刻通过 contextvars 沿 asyncio 调用链传递，
深层模块（例如 VolcengineLLM.stream）无需修改签名即可查询相对耗时。

用法：
    from simpleclaw.core.timing import mark_turn_start, elapsed_ms

    # 入口（_run_main_turn）
    mark_turn_start()
    logger.info("⏱ ttft turn.start ...")

    # 深层任意位置
    logger.info("⏱ ttft llm.first_delta +{}ms", elapsed_ms())
"""

from __future__ import annotations

import time
from contextvars import ContextVar

_turn_start: ContextVar[float | None] = ContextVar("turn_start", default=None)


def mark_turn_start() -> None:
    """在一轮对话入口调用，记录起始时刻。"""
    _turn_start.set(time.perf_counter())


def elapsed_ms() -> int:
    """返回从 mark_turn_start 到现在经过的毫秒数；未标记则返回 -1。"""
    start = _turn_start.get()
    if start is None:
        return -1
    return int((time.perf_counter() - start) * 1000)
