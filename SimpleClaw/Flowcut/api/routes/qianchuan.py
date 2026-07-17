"""千川相关路由：账号管理 + 手动触发数据同步 + 账户级汇总。"""
from fastapi import APIRouter, Depends, HTTPException, Request

from simpleclaw.runtime.task_protocol import TaskEnvelope
from Flowcut.api.deps import require_tenant
from Flowcut.runtime.streams import FlowcutTaskStream

router = APIRouter(prefix="/qianchuan", tags=["qianchuan"])


@router.get("/account-summary")
async def account_summary(
    request: Request,
    tenant_key: str = Depends(require_tenant),
) -> dict:
    """账户级汇总：SUM(fc_creative.qc_*) over tenant + orphan_count。

    MVP 阶段直接从 DB 聚合（数据已通过 sync 任务回填）。
    千川 statQuery 接口 schema 在变动，等稳定后可改为直接拉账户级 API。
    """
    repo = request.app.state.container.qianchuan_repo
    data = await repo.aggregate_account(tenant_key)
    data["orphan_count"] = await repo.count_orphans(tenant_key)
    return {"ok": True, "data": data}


@router.get("/accounts")
async def list_accounts(request: Request):
    raise HTTPException(501, "TODO")


@router.get("/oauth/start")
async def oauth_start(request: Request):
    raise HTTPException(501, "TODO: 返回千川 OAuth 跳转 URL")


@router.get("/oauth/callback")
async def oauth_callback(code: str, request: Request):
    raise HTTPException(501, "TODO: 处理千川 OAuth 回调")


@router.post("/token/refresh")
async def refresh_token(request: Request):
    raise HTTPException(501, "TODO: 刷新 access_token")


@router.post("/sync")
async def trigger_sync(
    request: Request,
    tenant_key: str = Depends(require_tenant),
) -> dict:
    """手动触发一次千川数据回流任务。

    MVP 单账号版本，无需 body。返回 task_id 供前端轮询 /flowcut/tasks/{task_id}。
    """
    c = request.app.state.container

    envelope = TaskEnvelope(
        task_type="qianchuan_sync",
        payload={"tenant_key": tenant_key},
        stream=FlowcutTaskStream.QIANCHUAN_SYNC,
        tenant_key=tenant_key,
        scope_key="qianchuan_sync:manual",
    )
    try:
        await c.runtime.submit_task(envelope)
    except Exception as exc:
        raise HTTPException(500, detail=f"提交同步任务失败: {exc}") from exc

    return {"task_id": envelope.task_id}
