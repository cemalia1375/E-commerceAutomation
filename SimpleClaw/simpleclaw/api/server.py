"""SimpleClaw dev API server.

Endpoints:
  POST /chat          — SSE stream: run ReactLoop, emit text/tool/done/error events
  GET  /admin         — serve the admin chat page
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

load_dotenv()

from simpleclaw.core.events import DoneEvent, ErrorEvent, TextEvent, ToolResultEvent
from simpleclaw.core.loop import ReactLoop
from simpleclaw.llm.config import VolcengineConfig
from simpleclaw.llm.volcengine import VolcengineLLM
from simpleclaw.tools.registry import ToolRegistry

app = FastAPI(title="SimpleClaw Dev Server")

# ---------------------------------------------------------------------------
# LLM + registry (shared across requests — VolcengineLLM is stateless per-req)
# ---------------------------------------------------------------------------

_config = VolcengineConfig(
    api_key=os.environ["VOLCENGINE_API_KEY"],
    api_base=os.getenv("VOLCENGINE_API_BASE"),
    model=os.getenv("VOLCENGINE_MODEL", "doubao-seed-2-0-pro-260215"),
)
_llm = VolcengineLLM(_config)
_registry = ToolRegistry()   # no tools yet


# ---------------------------------------------------------------------------
# Chat endpoint
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str
    system_prompt: str | None = None


async def _event_stream(message: str, system_prompt: str | None):
    """Run ReactLoop and yield SSE lines."""
    loop = ReactLoop(
        llm=_llm,
        tool_registry=_registry,
        system_prompt=system_prompt or "你是一个简洁有帮助的助手。",
    )
    try:
        async for event in loop.run(message):
            if isinstance(event, TextEvent):
                data = json.dumps({"type": "text", "token": event.token}, ensure_ascii=False)
            elif isinstance(event, ToolResultEvent):
                data = json.dumps({"type": "tool", "name": event.tool_name, "result": event.result}, ensure_ascii=False)
            elif isinstance(event, DoneEvent):
                data = json.dumps({"type": "done"})
            elif isinstance(event, ErrorEvent):
                data = json.dumps({"type": "error", "message": event.message}, ensure_ascii=False)
            else:
                continue
            yield f"data: {data}\n\n"
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        data = json.dumps({"type": "error", "message": str(exc)}, ensure_ascii=False)
        yield f"data: {data}\n\n"


@app.post("/chat")
async def chat(req: ChatRequest):
    return StreamingResponse(
        _event_stream(req.message, req.system_prompt),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Admin page
# ---------------------------------------------------------------------------

_ADMIN_PATH = Path(__file__).parent.parent.parent / "admin" / "index.html"


@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    return _ADMIN_PATH.read_text(encoding="utf-8")


@app.get("/")
async def root():
    return {"status": "ok", "admin": "/admin"}
