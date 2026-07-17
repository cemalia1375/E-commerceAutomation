"""Chat 路由：POST /agent/chat — SSE 流式对话。"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from Flowcut.api.deps import require_tenant
from loguru import logger

from simpleclaw.core.events import DoneEvent, ErrorEvent, TextEvent, ToolResultEvent
from simpleclaw.core.timing import elapsed_ms, mark_turn_start

router = APIRouter()

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def _get_container(request: Request):
    return request.app.state.container


async def _run_turn(
    c,
    session_key: str,
    tenant_key: str,
    message: str,
    queue: asyncio.Queue,
    *,
    on_text,
    on_done,
    on_error,
    on_tool_result,
    ui_context: dict | None = None,
) -> None:
    session_lock = c.sessions.get_lock(session_key)
    async with session_lock:
        mark_turn_start()
        logger.info(
            "turn.start session={} tenant={} msg_len={}",
            session_key, tenant_key, len(message),
        )

        loop = await c.sessions.get_or_create(session_key, tenant_key)
        messages_before = len(loop.messages)

        # 首条用户消息 → 自动设置会话标题（取前 30 字，去掉换行）
        if messages_before == 0:
            title = message.replace('\n', ' ').strip()[:30]
            if title:
                try:
                    await c.session_repo.update_title(tenant_key, session_key, title)
                except Exception:
                    pass  # 命名失败不影响主流程

        c.sessions.set_turn_context(
            session_key,
            tenant_key=tenant_key,
            query=message,
            ui_context=ui_context,
        )

        try:
            async for event in loop.run(message):
                if isinstance(event, TextEvent):
                    await queue.put(on_text(event.token))
                elif isinstance(event, ToolResultEvent):
                    await queue.put(on_tool_result(event))
                elif isinstance(event, DoneEvent):
                    logger.info("stream.done +{}ms", elapsed_ms())
                    await queue.put(on_done())
                elif isinstance(event, ErrorEvent):
                    await queue.put(on_error(event.message))
        except Exception as exc:
            logger.error("run_turn failed: {}", exc)
            await queue.put(on_error(str(exc)))
        finally:
            try:
                await c.sessions.save_turn(session_key, tenant_key, messages_before)
            except Exception as exc:
                logger.warning("save_turn 失败：{}", exc)
            await queue.put(None)


@router.post("/agent/chat", response_model=None)
async def agent_chat(
    request: Request,
    tenant_key: str = Depends(require_tenant),
) -> StreamingResponse | JSONResponse:
    c = _get_container(request)

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Request body must be valid JSON"}, status_code=400)

    session_key = str(payload.get("session_key", "")).strip()
    query = str(payload.get("query", "")).strip()
    ui_context: dict | None = payload.get("ui_context") or None
    if ui_context is not None and not isinstance(ui_context, dict):
        ui_context = None

    if not session_key:
        return JSONResponse({"ok": False, "error": "Missing required field: session_key"}, status_code=400)
    if not query:
        return JSONResponse({"ok": False, "error": "Missing required field: query"}, status_code=400)

    def on_text(token: str) -> str:
        data = json.dumps({"event": "chunk", "data": token}, ensure_ascii=False)
        return f"data: {data}\n\n"

    def on_done() -> str:
        data = json.dumps({"event": "done"}, ensure_ascii=False)
        return f"data: {data}\n\n"

    def on_error(msg: str) -> str:
        data = json.dumps({"event": "error", "data": msg}, ensure_ascii=False)
        return f"data: {data}\n\n"

    def on_tool_result(event: ToolResultEvent) -> str:
        # 尝试把 result 解析成 JSON 对象；解析失败则原样作为字符串透传
        content: object
        try:
            content = json.loads(event.result)
        except (TypeError, ValueError):
            content = event.result
        if not isinstance(content, dict):
            content = {
                "ok": True,
                "data": content,
                "ui_hint": {"render_as": "none"},
            }
        data = json.dumps(
            {
                "event": "tool_result",
                "data": {
                    "tool_name": event.tool_name,
                    "content": content,
                    "ok": True,
                },
            },
            ensure_ascii=False,
        )
        return f"data: {data}\n\n"

    queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=128)

    async def _event_stream():
        task = asyncio.create_task(
            _run_turn(
                c, session_key, tenant_key, query, queue,
                on_text=on_text, on_done=on_done, on_error=on_error,
                on_tool_result=on_tool_result,
                ui_context=ui_context,
            )
        )
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item.encode()
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(_event_stream(), media_type="text/event-stream", headers=_SSE_HEADERS)
