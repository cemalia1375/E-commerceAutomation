"""Smoke test: wire up VolcengineLLM + ReactLoop, send one message, print the stream.

Run:
    cd SimpleClaw
    python -m tests.smoke_test
"""

import asyncio
import os

from dotenv import load_dotenv

load_dotenv()

from simpleclaw.core.events import DoneEvent, ErrorEvent, TextEvent, ToolResultEvent
from simpleclaw.core.loop import ReactLoop
from simpleclaw.llm.config import VolcengineConfig
from simpleclaw.llm.volcengine import VolcengineLLM
from simpleclaw.tools.registry import ToolRegistry


async def main() -> None:
    config = VolcengineConfig(
        api_key=os.environ["VOLCENGINE_API_KEY"],
        api_base=os.getenv("VOLCENGINE_API_BASE"),
        model=os.getenv("VOLCENGINE_MODEL", "doubao-seed-2-0-pro-260215"),
    )
    llm = VolcengineLLM(config)
    registry = ToolRegistry()   # 空的，没有工具，LLM 直接回答

    loop = ReactLoop(
        llm=llm,
        tool_registry=registry,
        system_prompt="你是一个简洁的助手，回答不超过两句话。",
    )

    print("=== ReactLoop smoke test ===")
    print("User: 你好，简单介绍一下你自己")
    print("Assistant: ", end="", flush=True)

    async for event in loop.run("你好，简单介绍一下你自己"):
        if isinstance(event, TextEvent):
            print(event.token, end="", flush=True)
        elif isinstance(event, ToolResultEvent):
            print(f"\n[Tool: {event.tool_name}] {event.result}")
        elif isinstance(event, DoneEvent):
            print("\n=== Done ===")
        elif isinstance(event, ErrorEvent):
            print(f"\n=== Error: {event.message} ===")


if __name__ == "__main__":
    asyncio.run(main())
