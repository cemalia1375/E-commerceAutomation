"""Static admin route handlers — no runtime deps, no closure state."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import signal
import subprocess
import sys
from pathlib import Path

from loguru import logger
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from nanobot.admin._page import HTML_PAGE
from nanobot.admin.prompt_files import PROMPT_FILE_MAP


async def admin_editor(_request: Request) -> HTMLResponse:
    return HTMLResponse(HTML_PAGE)


async def admin_prompt_get(request: Request) -> JSONResponse:
    key = request.query_params.get("file", "")
    entry = PROMPT_FILE_MAP.get(key)
    if entry is None:
        return JSONResponse({"error": f"unknown file key: {key!r}"}, status_code=404)
    workspace_base: Path = request.app.state.admin_workspace
    path = entry.resolve_path(workspace_base)
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        content = ""
    except Exception as exc:
        logger.warning("admin_prompt_get: read error for {}: {}", path, exc)
        return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse({"content": content, "hot_reload": entry.hot_reload, "label": entry.label})


async def admin_prompt_put(request: Request) -> Response:
    key = request.query_params.get("file", "")
    entry = PROMPT_FILE_MAP.get(key)
    if entry is None:
        return Response(f"unknown file key: {key!r}", status_code=404)
    try:
        body = await request.json()
        content: str = body["content"]
    except Exception as exc:
        return Response(f"bad request: {exc}", status_code=400)
    workspace_base: Path = request.app.state.admin_workspace
    path = entry.resolve_path(workspace_base)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        logger.info("admin: wrote {} ({} bytes)", path, len(content))
    except Exception as exc:
        logger.error("admin_prompt_put: write error for {}: {}", path, exc)
        return Response(str(exc), status_code=500)
    return Response(status_code=204)


async def admin_restart(_request: Request) -> Response:
    # 用 sys.executable + sys.argv 重建完整启动命令，避免依赖 PATH 或 shebang
    cmd_parts = [sys.executable] + sys.argv
    restart_cmd = " ".join(shlex.quote(a) for a in cmd_parts)
    cwd = os.getcwd()
    shell_cmd = f"sleep 2 && {restart_cmd}"
    log_path = "/tmp/nanobot_restart.log"
    logger.info("admin: restart cmd: {} (cwd={})", restart_cmd, cwd)
    with open(log_path, "w") as _log:
        subprocess.Popen(
            shell_cmd,
            shell=True,
            close_fds=True,
            start_new_session=True,  # 脱离当前进程组
            cwd=cwd,
            stdout=_log,
            stderr=_log,
        )

    async def _kill() -> None:
        await asyncio.sleep(0.3)
        logger.info("admin: sending SIGTERM (pid={})", os.getpid())
        os.kill(os.getpid(), signal.SIGTERM)

    asyncio.create_task(_kill())
    return Response(status_code=202)


async def admin_preview_postprocess(request: Request) -> JSONResponse:
    """POST /admin/preview/postprocess — return assembled System+User prompt without calling LLM."""
    try:
        body = await request.json()
    except Exception as exc:
        return JSONResponse({"error": f"bad request: {exc}"}, status_code=400)
    try:
        from nanobot.agent.postprocess import render_postprocess_prompt

        user_prompt = render_postprocess_prompt(
            origin_user_message=str(body.get("user_message") or ""),
            assistant_reply=str(body.get("assistant_reply") or ""),
            current_user_md=str(body.get("current_user_md") or ""),
            current_soul_md=str(body.get("current_soul_md") or ""),
            current_heartbeat_md=str(body.get("current_heartbeat_md") or ""),
        )
        system_prompt = str(body.get("system_prompt") or "").strip()
        if system_prompt:
            prompt = f"[SYSTEM]\n{system_prompt}\n\n{'─' * 60}\n\n[USER]\n{user_prompt}"
        else:
            prompt = f"[USER]\n{user_prompt}"
        return JSONResponse({"prompt": prompt})
    except Exception as exc:
        logger.exception("admin_preview_postprocess error")
        return JSONResponse({"error": str(exc)}, status_code=500)


async def admin_preview_cold_path(request: Request) -> JSONResponse:
    """POST /admin/preview/cold_path — obligation extraction prompt."""
    try:
        body = await request.json()
    except Exception as exc:
        return JSONResponse({"error": f"bad request: {exc}"}, status_code=400)
    try:
        from Mojing.agent.cold_path import (
            _COLD_PATH_PROMPT_PATH,
            _fill_user_template,
            _load_split_prompt,
        )

        system_prompt, user_template = _load_split_prompt(_COLD_PATH_PROMPT_PATH)
        user_prompt = _fill_user_template(
            template=user_template,
            user_message=str(body.get("user_message") or ""),
            assistant_reply=str(body.get("assistant_reply") or ""),
        )
        prompt = f"[SYSTEM]\n{system_prompt}\n\n{'─' * 60}\n\n[USER]\n{user_prompt}"
        return JSONResponse({"prompt": prompt})
    except Exception as exc:
        logger.exception("admin_preview_cold_path error")
        return JSONResponse({"error": str(exc)}, status_code=500)


async def admin_preview_compression_memory(request: Request) -> JSONResponse:
    """POST /admin/preview/compression_memory — path 2: compression-triggered memory extraction prompt."""
    try:
        body = await request.json()
    except Exception as exc:
        return JSONResponse({"error": f"bad request: {exc}"}, status_code=400)
    try:
        from nanobot.memory.prompts.cold_path import (
            COLD_PATH_SYSTEM_PROMPT,
            build_compression_memory_prompt,
        )

        chunk = body.get("chunk") or []
        memory_index = body.get("memory_index") or []
        if isinstance(chunk, str):
            chunk = json.loads(chunk)
        if isinstance(memory_index, str):
            memory_index = json.loads(memory_index)
        user_prompt = build_compression_memory_prompt(
            chunk=chunk,
            memory_index=memory_index,
        )
        prompt = f"[SYSTEM]\n{COLD_PATH_SYSTEM_PROMPT}\n\n[USER]\n{user_prompt}"
        return JSONResponse({"prompt": prompt})
    except Exception as exc:
        logger.exception("admin_preview_compression_memory error")
        return JSONResponse({"error": str(exc)}, status_code=500)
