"""Journey 路由：/journey/event"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from loguru import logger

router = APIRouter()


@router.post("/journey/event")
async def journey_event(request: Request) -> JSONResponse:
    """记录 journey 事件，必要时推进阶段，并无感替换 in-memory session overlay。

    Request body:
        {"tenant_key": "...", "event": "explore_entered"}

    Response:
        {"ok": true, "stage_before": "novice", "stage_after": "explore", "promoted": true}
    """
    from Mojing.journey.rules import record_journey_event

    c = request.app.state.container

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)

    tenant_key = (payload.get("tenant_key") or "").strip()
    event      = (payload.get("event") or "").strip()

    if not tenant_key or not event:
        return JSONResponse({"ok": False, "error": "tenant_key and event are required"}, status_code=400)

    if c.tenant_state_repo is None:
        return JSONResponse({"ok": False, "error": "tenant_state_repo not initialized"}, status_code=503)

    stage_before, stage_after = await record_journey_event(c.tenant_state_repo, tenant_key, event)
    promoted = stage_after != stage_before

    if promoted and c.sessions is not None:
        swapped = await c.sessions.swap_tenant_overlay(tenant_key, stage_after)
        logger.info(
            "journey: tenant={} {} → {} (overlay swapped on {} session(s))",
            tenant_key, stage_before, stage_after, swapped,
        )
    else:
        logger.info(
            "journey: tenant={} event={} stage={} (no change)",
            tenant_key, event, stage_after,
        )

    return JSONResponse({
        "ok":           True,
        "stage_before": stage_before,
        "stage_after":  stage_after,
        "promoted":     promoted,
    })
