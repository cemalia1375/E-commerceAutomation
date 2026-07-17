"""Chat 路由：/events/stream, /agent/chat, /v1/chat/completions"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger

from simpleclaw.context import AttentionPacket
from simpleclaw.core.events import DoneEvent, ErrorEvent, TextEvent, ToolResultEvent
from simpleclaw.core.messages import AssistantMessage, ToolResultMessage, UserMessage
from simpleclaw.core.loop import _build_user_content
from simpleclaw.core.timing import elapsed_ms, mark_turn_start

from Mojing.agent.capabilities import capabilities_from_device_context
from Mojing.agent.first_token import (
    build_first_token_continuation_instruction,
    build_first_token_context_message,
    build_first_token_user_message,
    join_first_token_reply,
)
from Mojing.api.session_ingress import MainSessionIngressCoordinator
from Mojing.api.request_utils import normalize_session_key, resolve_agent_chat_context, resolve_volcano_context
from Mojing.runtime.photo_capture import build_capture_photo_problem_message
from Mojing.runtime.post_turn import enqueue_post_turn_tasks
from Mojing.runtime.turn_facts import collect_turn_facts
from Mojing.storage.image_repo import normalize_image_ref

router = APIRouter()

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def _get_container(request: Request):
    return request.app.state.container


def _get_main_session_ingress(c) -> MainSessionIngressCoordinator:
    coordinator = getattr(c, "main_session_ingress", None)
    if coordinator is None:
        raise RuntimeError("main_session_ingress is not initialized")
    return coordinator


# ---------------------------------------------------------------------------
# 副作用：用户上传图片时只记录资产，不自动派发分析任务
# ---------------------------------------------------------------------------

async def _record_uploaded_images(
    c,
    *,
    tenant_key: str,
    session_key: str,
    origin_session_key: str | None = None,
    media: list[str],
    message_id: str | None,
    query: str,
) -> None:
    del origin_session_key, query
    if not media or c.image_repo is None:
        return
    for image_ref in media:
        if not isinstance(image_ref, str) or not image_ref.strip():
            continue
        try:
            job = await c.image_repo.create_job(
                tenant_key=tenant_key,
                session_key=session_key,
                image_ref=image_ref,
                message_id=message_id,
                status="uploaded",
            )
        except Exception as exc:
            logger.warning("image_repo.create_job 失败：{}", exc)
            continue
        logger.info(
            "image uploaded recorded: tenant={} image_ref={} job_id={}",
            tenant_key, image_ref, job.get("job_id"),
        )


# ---------------------------------------------------------------------------
# 核心：运行 ReactLoop 并保存到数据库
# ---------------------------------------------------------------------------

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
    on_first_token_text=None,
    on_first_token_status=None,
    media: list[str] | None = None,
    message_id: str | None = None,
    device_id: int | str | None = None,
    device_code: str | None = None,
    prompt_surface: str = "app",
    capture_photo_enabled: bool = True,
    report_id: str | None = None,
    origin_session_key: str | None = None,
    ingress_id: str | None = None,
    on_prompt_messages: Callable[[list[dict]], None] | None = None,
    on_attention_packets: Callable[[list[AttentionPacket]], None] | None = None,
) -> None:
    media = [normalize_image_ref(ref) for ref in (media or []) if normalize_image_ref(ref)]
    if c.subagent_store is not None and c.subagent_store.find_subagent(session_key) is not None:
        try:
            async def _on_token(token: str) -> None:
                await queue.put(on_text(token))

            async def _on_first_token(token: str) -> None:
                formatter = on_first_token_text or on_text
                await queue.put(formatter(token))

            async def _on_first_token_status(status: str, **extra) -> None:
                if on_first_token_status is not None:
                    await queue.put(on_first_token_status(status, **extra))

            first_token_agent = (
                getattr(c, "first_token_agent", None)
                if on_first_token_text is not None or on_first_token_status is not None
                else None
            )

            await c.subagent_store.run_turn(
                session_key=session_key,
                tenant_key=tenant_key,
                message=message,
                on_token=_on_token,
                media=media,
                message_id=message_id,
                report_id=report_id,
                origin_session_key=origin_session_key,
                ingress_id=ingress_id,
                first_token_agent=first_token_agent,
                on_first_token=_on_first_token if first_token_agent is not None else None,
                on_first_token_status=(
                    _on_first_token_status
                    if on_first_token_status is not None
                    else None
                ),
            )
            await queue.put(on_done())
        except Exception as exc:
            logger.error("SubagentStore.run_turn failed: {}", exc)
            await queue.put(on_error(str(exc)))
        finally:
            await queue.put(None)
        return

    session_lock = c.sessions.get_lock(session_key)
    async with session_lock:
        await _run_main_turn(
            c, session_key, tenant_key, message, queue,
            on_text=on_text, on_done=on_done, on_error=on_error,
            on_first_token_text=on_first_token_text,
            on_first_token_status=on_first_token_status,
            media=media, message_id=message_id,
            device_id=device_id, device_code=device_code,
            prompt_surface=prompt_surface,
            capture_photo_enabled=capture_photo_enabled,
            origin_session_key=origin_session_key,
            on_prompt_messages=on_prompt_messages,
            on_attention_packets=on_attention_packets,
        )


async def _run_main_turn(
    c,
    session_key: str,
    tenant_key: str,
    message: str,
    queue: asyncio.Queue,
    *,
    on_text,
    on_done,
    on_error,
    on_first_token_text=None,
    on_first_token_status=None,
    media: list[str] | None = None,
    message_id: str | None = None,
    device_id: int | str | None = None,
    device_code: str | None = None,
    prompt_surface: str = "app",
    capture_photo_enabled: bool = True,
    origin_session_key: str | None = None,
    on_prompt_messages: Callable[[list[dict]], None] | None = None,
    on_attention_packets: Callable[[list[AttentionPacket]], None] | None = None,
) -> None:
    mark_turn_start()
    media = [normalize_image_ref(ref) for ref in (media or []) if normalize_image_ref(ref)]
    turn_started_ms = int(time.time() * 1000)
    is_cold = session_key not in c.sessions.active_sessions
    logger.info("⏱ ttft turn.start session={} tenant={} cold={} msg_len={} message_id={}",
                session_key, tenant_key, is_cold, len(message), message_id or "")

    capabilities = capabilities_from_device_context(
        device_id=device_id,
        device_code=device_code,
        prompt_surface=prompt_surface,
        capture_photo_enabled=capture_photo_enabled,
    )
    loop = await c.sessions.get_or_create(
        session_key,
        tenant_key,
        capabilities=capabilities,
    )
    await c.sessions.maybe_compress(session_key, tenant_key)
    messages_before = loop.absolute_message_count
    messages_before_local = len(loop.messages)
    logger.info(
        "⏱ ttft session.ready session={} tenant={} +{}ms history_n={}",
        session_key, tenant_key, elapsed_ms(), messages_before_local,
    )
    restore_prompt_debug = _maybe_wrap_prompt_debug(loop, on_prompt_messages)
    provider_completion_event_ids: list[str] = []

    def _observe_attention_packets(packets: list[AttentionPacket]) -> None:
        if on_attention_packets is not None:
            on_attention_packets(packets)
        for packet in packets:
            source = str(getattr(packet, "source", "") or "")
            if not source.endswith("_state"):
                continue
            metadata = getattr(packet, "metadata", None) or {}
            event_id = str(metadata.get("event_id") or "").strip()
            if event_id and event_id not in provider_completion_event_ids:
                provider_completion_event_ids.append(event_id)

    restore_attention_debug = _maybe_wrap_attention_debug(loop, _observe_attention_packets)

    opener_task: asyncio.Task | None = None
    opener_buffer: list[str] = []
    opener_input = build_first_token_user_message(message, media)
    if getattr(c, "first_token_agent", None) is not None and opener_input.strip():
        if on_first_token_status is not None:
            await queue.put(on_first_token_status(
                "started",
                model=c.first_token_agent.config.model,
                timeout_ms=int(c.first_token_agent.timeout_s * 1000),
            ))

        async def _on_opener_token(token: str) -> None:
            opener_buffer.append(token)
            formatter = on_first_token_text or on_text
            await queue.put(formatter(token))

        opener_task = asyncio.create_task(
            c.first_token_agent.generate_stream(
                tenant_key=tenant_key,
                session_key=session_key,
                user_message=opener_input,
                history=loop.messages,
                consolidated_from=loop.consolidated_from,
                history_offset=loop.history_offset,
                prompt_surface=capabilities.prompt_surface,
                on_token=_on_opener_token,
            )
        )
    elif on_first_token_status is not None:
        await queue.put(on_first_token_status("disabled"))

    dynamic_context_sections = []
    attention_packets = []
    logger.info(
        "⏱ ttft context.providers_deferred +{}ms explicit_dyn_n={} explicit_attention_n={}",
        elapsed_ms(),
        len(dynamic_context_sections),
        len(attention_packets),
    )

    c.sessions.set_turn_context(
        session_key,
        tenant_key=tenant_key,
        query=message,
        media=media,
        message_id=message_id,
        device_id=device_id,
        device_code=device_code,
        origin_session_key=origin_session_key,
        capture_photo_enabled=capabilities.capture_photo_enabled,
    )

    reply_parts: list[str] = []
    tool_result_events: list[dict] = []
    intercepted_photo_result: dict | None = None
    first_token_logged = False
    main_completed = False
    terminal_done_pending = False
    terminal_error_sent = False
    opener_text, opener_status, opener_detail = await _resolve_opener_text(opener_task, c, opener_buffer)
    if on_first_token_status is not None and opener_task is not None:
        await queue.put(on_first_token_status(
            opener_status,
            chars=len(opener_text),
            detail=opener_detail,
        ))
    if opener_text:
        logger.info("⏱ ttft opener.sent +{}ms chars={}", elapsed_ms(), len(opener_text))
        first_token_logged = True

    if opener_text:
        loop.messages.append(UserMessage(_build_user_content(message, media)))
        loop.messages.append(AssistantMessage(build_first_token_context_message(opener_text)))
        continuation = build_first_token_continuation_instruction(opener_text)
        if continuation:
            attention_packets.append(AttentionPacket(
                content=continuation,
                source="first_token_continuation",
                priority=1000,
                lifetime="one_turn",
                role="system",
                placement="tail",
            ))

    main_separator_pending = bool(opener_text)
    try:
        async for event in loop.run(
            message,
            persist_user_input=not bool(opener_text),
            dynamic_context_sections=dynamic_context_sections,
            media=media,
            attention_packets=attention_packets,
            context_metadata={
                "tenant_key": tenant_key,
                "session_key": session_key,
                "message_id": message_id,
                "device_id": device_id,
                "device_code": device_code,
                "device_enabled": capabilities.device_enabled,
                "capture_photo_enabled": capabilities.capture_photo_enabled,
                "prompt_surface": capabilities.prompt_surface,
                "origin_session_key": origin_session_key,
                "entrypoint": "chat",
                "image_just_uploaded": bool(media),
                "media": media or [],
            },
        ):
            if isinstance(event, TextEvent):
                if not first_token_logged:
                    logger.info("⏱ ttft first_token +{}ms", elapsed_ms())
                    first_token_logged = True
                reply_parts.append(event.token)
                token = event.token
                if main_separator_pending:
                    token = token if token.startswith("\n") else "\n" + token
                    main_separator_pending = False
                await queue.put(on_text(token))
            elif isinstance(event, ToolResultEvent):
                tool_result_events.append({
                    "tool_name": event.tool_name,
                    "result": event.result,
                    "source": "react_loop_event",
                })
                photo_result = _parse_capture_photo_result(event)
                if photo_result is not None:
                    _append_intercepted_tool_result(loop, event.tool_name, event.result)
                    intercepted_photo_result = photo_result
                    break
            elif isinstance(event, DoneEvent):
                main_completed = True
                logger.info("⏱ ttft stream.done +{}ms reply_chars={}",
                            elapsed_ms(), sum(len(p) for p in reply_parts))
                terminal_done_pending = True
            elif isinstance(event, ErrorEvent):
                terminal_error_sent = True
                await queue.put(on_error(event.message))
        if intercepted_photo_result is not None:
            photo_action = intercepted_photo_result.get("action")
            if photo_action == "photo_ready":
                await _run_internal_photo_reply(
                    c,
                    session_key=session_key,
                    tenant_key=tenant_key,
                    loop=loop,
                    queue=queue,
                    on_text=on_text,
                    on_error=on_error,
                    reply_parts=reply_parts,
                    tool_result_events=tool_result_events,
                    photo_result=intercepted_photo_result,
                    device_id=device_id,
                    device_code=device_code,
                    prompt_surface=prompt_surface,
                    origin_session_key=origin_session_key,
                )
            elif photo_action in {"photo_failed", "photo_timeout", "photo_pending"}:
                problem_reply = build_capture_photo_problem_message(
                    intercepted_photo_result,
                    user_message=message,
                    messages=loop.messages,
                )
                reply_parts.append(problem_reply)
                loop.messages.append(AssistantMessage(problem_reply))
                token = problem_reply
                if main_separator_pending:
                    token = "\n" + token
                    main_separator_pending = False
                await queue.put(on_text(token))
            main_completed = True
            terminal_done_pending = True
            logger.info(
                "capture_photo terminal turn intercepted session={} action={} captureRequestId={} photoId={}",
                session_key,
                intercepted_photo_result.get("action"),
                intercepted_photo_result.get("captureRequestId"),
                intercepted_photo_result.get("photoId"),
            )
    except Exception as exc:
        terminal_error_sent = True
        await queue.put(on_error(str(exc)))
    finally:
        restore_attention_debug()
        restore_prompt_debug()
        main_reply = "".join(reply_parts)
        if not main_completed and main_reply:
            loop.messages.append(AssistantMessage(main_reply))

        try:
            await asyncio.shield(c.sessions.save_turn(session_key, tenant_key, messages_before))
        except Exception as e:
            logger.warning("save_turn 失败：{}", e)

        visible_reply = join_first_token_reply(opener_text, main_reply)
        if main_reply.strip() and provider_completion_event_ids and getattr(c, "completion_event_repo", None) is not None:
            for event_id in provider_completion_event_ids:
                try:
                    await c.completion_event_repo.mark_consumed_by_provider(event_id=event_id)
                except Exception as exc:
                    logger.warning("completion event provider consume failed: event_id={} err={}", event_id, exc)

        if visible_reply and c.runtime is not None:
            recent_messages = loop.messages_since_absolute(messages_before)
            turn_facts = await collect_turn_facts(
                c,
                tenant_key=tenant_key,
                session_key=session_key,
                since_ms=turn_started_ms,
                messages=recent_messages,
                tool_result_events=tool_result_events,
            )
            await enqueue_post_turn_tasks(
                c,
                tenant_key=tenant_key,
                session_key=session_key,
                user_message=message,
                assistant_reply=visible_reply,
                first_token_reply=opener_text,
                main_assistant_reply=main_reply,
                media=media,
                **turn_facts,
            )

        if terminal_done_pending and not terminal_error_sent:
            await queue.put(on_done())
        await queue.put(None)


def _parse_capture_photo_result(event: ToolResultEvent) -> dict | None:
    if event.tool_name != "device_command":
        return None
    try:
        payload = json.loads(event.result)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("command") != "capture_photo":
        return None
    if payload.get("action") not in {"photo_ready", "photo_failed", "photo_timeout", "photo_pending"}:
        return None
    return payload


def _append_intercepted_tool_result(loop, tool_name: str, result: str) -> None:
    call_id = None
    for message in reversed(loop.messages):
        if not isinstance(message, AssistantMessage):
            continue
        for tool_call in reversed(message.tool_calls or []):
            if tool_call.name == tool_name:
                call_id = tool_call.id
                break
        if call_id:
            break
    if not call_id:
        logger.warning("capture_photo intercept could not find tool_call_id for {}", tool_name)
        return
    loop.messages.append(ToolResultMessage(call_id=call_id, content=result))


async def _run_internal_photo_reply(
    c,
    *,
    session_key: str,
    tenant_key: str,
    loop,
    queue: asyncio.Queue,
    on_text,
    on_error,
    reply_parts: list[str],
    tool_result_events: list[dict],
    photo_result: dict,
    device_id: int | str | None,
    device_code: str | None,
    prompt_surface: str,
    origin_session_key: str | None,
) -> None:
    photo_url = normalize_image_ref(photo_result.get("cleanPhotoUrl") or photo_result.get("photoUrl"))
    if not photo_url:
        return
    capabilities = capabilities_from_device_context(
        device_id=device_id,
        device_code=device_code,
        prompt_surface=prompt_surface,
        capture_photo_enabled=False,
    )
    loop = await c.sessions.get_or_create(session_key, tenant_key, capabilities=capabilities)
    visual_message = _photo_returned_message()
    c.sessions.set_turn_context(
        session_key,
        tenant_key=tenant_key,
        query=visual_message,
        media=[photo_url],
        device_id=device_id,
        device_code=device_code,
        origin_session_key=origin_session_key,
        capture_photo_enabled=False,
    )
    async for event in loop.run(
        visual_message,
        media=[photo_url],
        persist_user_input=True,
        context_metadata={
            "tenant_key": tenant_key,
            "session_key": session_key,
            "device_id": device_id,
            "device_code": device_code,
            "device_enabled": capabilities.device_enabled,
            "capture_photo_enabled": False,
            "prompt_surface": capabilities.prompt_surface,
            "origin_session_key": origin_session_key,
            "entrypoint": "internal_photo_reply",
            "image_just_uploaded": True,
            "media": [photo_url],
            "capture_request_id": photo_result.get("captureRequestId"),
            "photo_id": photo_result.get("photoId"),
        },
    ):
        if isinstance(event, TextEvent):
            reply_parts.append(event.token)
            await queue.put(on_text(event.token))
        elif isinstance(event, ToolResultEvent):
            tool_result_events.append({
                "tool_name": event.tool_name,
                "result": event.result,
                "source": "internal_photo_reply",
            })
        elif isinstance(event, ErrorEvent):
            await queue.put(on_error(event.message))
            break
        elif isinstance(event, DoneEvent):
            break


def _photo_returned_message() -> str:
    return (
        "【设备照片返回】刚才拍照结果回来了。请直接基于这张图片回应用户刚才的拍照意图。"
        "不要再次调用拍照工具；如果确实需要设备辅助，只能使用非拍照设备能力。"
    )


def _maybe_wrap_prompt_debug(loop, callback):
    if callback is None:
        return lambda: None
    original = loop._get_messages_async

    async def _debug_get_messages_async():
        messages = await original()
        callback(messages)
        return messages

    loop._get_messages_async = _debug_get_messages_async

    def restore() -> None:
        loop._get_messages_async = original

    return restore


def _maybe_wrap_attention_debug(loop, callback):
    builder = getattr(loop, "context_builder", None)
    if callback is None or builder is None or not hasattr(builder, "_collect_attention_packets"):
        return lambda: None
    original = builder._collect_attention_packets

    async def _debug_collect_attention_packets(ctx, *, attention_packets):
        packets = await original(ctx, attention_packets=attention_packets)
        callback(packets)
        return packets

    builder._collect_attention_packets = _debug_collect_attention_packets

    def restore() -> None:
        builder._collect_attention_packets = original

    return restore


async def _resolve_opener_text(
    opener_task: asyncio.Task | None,
    c,
    opener_buffer: list[str],
) -> tuple[str, str, str]:
    if opener_task is None:
        return "", "disabled", ""
    timeout_s = c.first_token_agent.timeout_s
    try:
        result = await asyncio.wait_for(asyncio.shield(opener_task), timeout=timeout_s)
    except asyncio.TimeoutError:
        buffered_text = "".join(opener_buffer).strip()
        if not buffered_text:
            opener_task.cancel()
            logger.info("first_token opener timeout before first delta after {}s", timeout_s)
            return "", "timeout", f"timeout after {timeout_s}s"
        logger.info("first_token opener started before timeout; waiting for completion after {}s", timeout_s)
        try:
            result = await opener_task
        except Exception as exc:
            logger.warning("first_token opener failed after partial output: {}", exc)
            return buffered_text, "failed_after_partial", str(exc)
        text = str(getattr(result, "text", "") or "").strip() if result is not None else buffered_text
        return text or buffered_text, "done_after_timeout", f"first delta arrived before {timeout_s}s"
    except Exception as exc:
        logger.warning("first_token opener failed: {}", exc)
        return "".join(opener_buffer).strip(), "failed", str(exc)
    text = str(getattr(result, "text", "") or "").strip() if result is not None else ""
    return text, "done" if text else "empty", ""


def _insert_opener_message(loop, messages_before: int, opener_text: str) -> None:
    """Persist the user-visible opener as part of the business transcript."""
    if not opener_text.strip():
        return
    if len(loop.messages) <= messages_before:
        return
    if not isinstance(loop.messages[messages_before], UserMessage):
        return
    insert_at = min(messages_before + 1, len(loop.messages))
    if insert_at < len(loop.messages):
        existing = loop.messages[insert_at]
        if isinstance(existing, AssistantMessage) and existing.content == opener_text:
            return
    loop.messages.insert(insert_at, AssistantMessage(opener_text))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/events/stream")
async def event_stream(request: Request, tenant_key: str = "", session_key: str = "") -> StreamingResponse:
    c = _get_container(request)
    tenant_key = tenant_key.strip()
    raw_session_key = session_key.strip()
    if not tenant_key or not raw_session_key:
        async def _bad_request():
            data = json.dumps({"type": "error", "error": "missing tenant_key or session_key"}, ensure_ascii=False)
            yield f"data: {data}\n\n".encode()
        return StreamingResponse(_bad_request(), media_type="text/event-stream", headers=_SSE_HEADERS)
    session_key = normalize_session_key(raw_session_key, tenant_key)
    logger.info(
        "events.stream connect tenant={} session={} raw_session={}",
        tenant_key, session_key, raw_session_key,
    )

    async def _events():
        async for event in c.event_hub.subscribe(tenant_key, session_key):
            data = json.dumps(event, ensure_ascii=False)
            yield f"data: {data}\n\n".encode()

    return StreamingResponse(_events(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.post("/internal/device/photo-returned")
async def device_photo_returned(request: Request) -> JSONResponse:
    c = _get_container(request)
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Request body must be valid JSON"}, status_code=400)

    coordinator = getattr(c, "photo_capture_coordinator", None)
    if coordinator is None:
        return JSONResponse({"ok": True, "action": "recorded_only", "reason": "coordinator_not_available"})

    returned_photo_url = (
        payload.get("cleanPhotoUrl")
        or payload.get("clean_photo_url")
        or payload.get("photoUrl")
        or payload.get("signedPhotoUrl")
        or payload.get("photo_url")
    )

    result = await coordinator.resolve_photo(
        capture_request_id=payload.get("captureRequestId") or payload.get("capture_request_id"),
        photo_id=payload.get("photoId") or payload.get("photo_id"),
        photo_url=returned_photo_url,
        clean_photo_url=returned_photo_url,
    )
    if result.get("action") == "resolved_waiter":
        return JSONResponse(result)
    if result.get("action") != "late_photo_ready":
        return JSONResponse(result)

    session_key = str(result.get("sessionKey") or "").strip()
    if not session_key:
        result["action"] = "recorded_only"
        result["reason"] = "missing_session_key"
        return JSONResponse(result)

    ingress = _get_main_session_ingress(c)
    created_at_ms = int(result.get("createdAtMs") or 0)
    busy = await ingress.scheduler.is_busy(session_key)
    newer_user = await ingress.store.find_newer_user_message(
        session_key=session_key,
        after_created_at_ms=created_at_ms,
    )
    if newer_user is not None:
        result["action"] = "recorded_only"
        result["reason"] = "newer_user_message"
        result["sessionBusy"] = busy
        result["newerUserMessage"] = newer_user.ingress_id if newer_user is not None else None
        return JSONResponse(result)

    capture_request_id = str(result.get("captureRequestId") or "").strip()
    if capture_request_id:
        await coordinator.mark_auto_continuation_sent(capture_request_id)
    result["action"] = "trigger_external_text_to_llm"
    result["message"] = _photo_returned_message()
    result["interruptMode"] = 3
    result["sessionBusy"] = busy
    return JSONResponse(result)


@router.post("/internal/device/photo-failed")
async def device_photo_failed(request: Request) -> JSONResponse:
    c = _get_container(request)
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Request body must be valid JSON"}, status_code=400)

    coordinator = getattr(c, "photo_capture_coordinator", None)
    if coordinator is None:
        return JSONResponse({"ok": True, "action": "recorded_only", "reason": "coordinator_not_available"})

    result = await coordinator.resolve_failure(
        capture_request_id=payload.get("captureRequestId") or payload.get("capture_request_id"),
        photo_id=payload.get("photoId") or payload.get("photo_id"),
        reason=payload.get("reason") or payload.get("message") or payload.get("error"),
    )
    return JSONResponse(result)


@router.post("/agent/chat", response_model=None)
async def agent_chat(request: Request) -> StreamingResponse | JSONResponse:
    c = _get_container(request)
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "Request body must be valid JSON"}, status_code=400)

    ctx     = resolve_agent_chat_context(payload)
    message = ctx["message"]
    if not message and not ctx["media"]:
        return JSONResponse({"ok": False, "error": "Missing required field: message"}, status_code=400)

    session_key = ctx["session_key"]
    tenant_key  = ctx["tenant_key"]

    await _record_uploaded_images(
        c,
        tenant_key=tenant_key,
        session_key=session_key,
        origin_session_key=ctx.get("origin_session_key"),
        media=ctx["media"],
        message_id=ctx.get("message_id"),
        query=message,
    )

    def on_text(token: str) -> str:
        data = json.dumps({"type": "chunk", "node": "expert", "data": {"text": token, "source": "main_agent"}}, ensure_ascii=False)
        return f"data: {data}\n\n"

    def on_first_token_text(token: str) -> str:
        data = json.dumps({
            "type": "chunk",
            "node": "first_token_llm",
            "data": {"text": token, "source": "first_token_llm"},
        }, ensure_ascii=False)
        return f"data: {data}\n\n"

    def on_first_token_status(status: str, **extra) -> str:
        data = json.dumps({
            "type": "first_token_status",
            "node": "first_token_llm",
            "data": {"status": status, **extra},
        }, ensure_ascii=False)
        return f"data: {data}\n\n"

    def on_done() -> str:
        data = json.dumps({"type": "done", "node": None, "data": {"current_state": "expert"}}, ensure_ascii=False)
        return f"data: {data}\n\n"

    def on_error(msg: str) -> str:
        data = json.dumps({"type": "error", "node": None, "data": {"error": msg}}, ensure_ascii=False)
        return f"data: {data}\n\n"

    queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=128)
    ingress = _get_main_session_ingress(c)
    ingress_id = await ingress.submit_user_message(
        session_key=session_key,
        tenant_key=tenant_key,
        message=message,
        queue=queue,
        on_text=on_text,
        on_done=on_done,
        on_error=on_error,
        on_first_token_text=on_first_token_text,
        on_first_token_status=on_first_token_status,
        media=ctx["media"],
        message_id=ctx.get("message_id"),
        device_id=ctx.get("device_id"),
        device_code=ctx.get("device_code"),
        prompt_surface=ctx.get("prompt_surface", "app"),
        capture_photo_enabled=ctx.get("capture_photo_enabled", True),
        report_id=ctx.get("report_id"),
        origin_session_key=ctx.get("origin_session_key"),
    )

    async def _event_stream():
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item.encode()
        finally:
            await ingress.cancel(ingress_id)

    return StreamingResponse(_event_stream(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.post("/v1/chat/completions", response_model=None)
async def volcano_chat(request: Request) -> StreamingResponse:
    c = _get_container(request)
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    ctx         = resolve_volcano_context(payload, dict(request.headers))
    message     = ctx["message"]
    session_key = ctx["session_key"]
    tenant_key  = ctx["tenant_key"]
    model_name  = ctx["model"] or "doubao-seed-2-0-pro-260215"

    await _record_uploaded_images(
        c,
        tenant_key=tenant_key,
        session_key=session_key,
        origin_session_key=ctx.get("origin_session_key"),
        media=ctx["media"],
        message_id=None,
        query=message,
    )

    chat_id    = f"chatcmpl-{uuid.uuid4().hex[:16]}"
    created_ts = int(time.time())

    def _chunk(delta: dict, finish_reason=None) -> str:
        obj = {
            "id": chat_id,
            "object": "chat.completion.chunk",
            "created": created_ts,
            "model": model_name,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
        }
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"

    def on_text(token: str) -> str:
        return _chunk({"content": token})

    def on_done() -> str:
        return _chunk({}, finish_reason="stop") + "data: [DONE]\n\n"

    def on_error(msg: str) -> str:
        return _chunk({"content": f"[Error: {msg}]"}, finish_reason="stop") + "data: [DONE]\n\n"

    queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=128)
    ingress = _get_main_session_ingress(c)
    ingress_id = await ingress.submit_user_message(
        session_key=session_key,
        tenant_key=tenant_key,
        message=message,
        queue=queue,
        on_text=on_text,
        on_done=on_done,
        on_error=on_error,
        media=ctx["media"],
        device_id=ctx.get("device_id"),
        device_code=ctx.get("device_code"),
        prompt_surface=ctx.get("prompt_surface", "device"),
        capture_photo_enabled=ctx.get("capture_photo_enabled", True),
        origin_session_key=ctx.get("origin_session_key"),
    )

    async def _event_stream():
        yield _chunk({"role": "assistant"}).encode()
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item.encode()
        finally:
            await ingress.cancel(ingress_id)

    return StreamingResponse(_event_stream(), media_type="text/event-stream", headers=_SSE_HEADERS)
