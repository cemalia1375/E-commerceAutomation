"""七天人脸图片生成：async 封装 + job 状态追踪。

支持自然语言描述：每天可自定义提示词；Day1 用全文提示词文生图，
Day2+ 用状态描述填入 edit_template 做锚定编辑。
生成的 PNG 驻留内存供回填直取，同时写入 script/imagegen/out/ 磁盘留档。
"""

from __future__ import annotations

import asyncio
import base64
import io
import os
import uuid
import zipfile
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from loguru import logger

try:
    from script.imagegen.spec import OUT_DIR, load_spec
    _SPEC_IMPORT_ERROR: Exception | None = None
except ModuleNotFoundError as exc:
    OUT_DIR = Path("script/imagegen/out")
    load_spec = None
    _SPEC_IMPORT_ERROR = exc

IMAGEGEN_JOBS: dict[str, dict[str, Any]] = {}


def get_spec_defaults() -> dict[str, Any]:
    """返回 days.yaml 的默认配置和天列表，供前端初始化表单。
    日期标签按今天动态计算（最后一天=今天，往前推）。
    """
    from datetime import date, timedelta

    if load_spec is None:
        raise RuntimeError(
            "script.imagegen.spec is not available; lab image generation is disabled"
        ) from _SPEC_IMPORT_ERROR

    spec = load_spec()
    today = date.today()
    n = len(spec.days)
    dates = [today - timedelta(days=n - 1 - i) for i in range(n)]
    days = [
        {
            "day": d.day,
            "label": f"{dates[i].month}/{dates[i].day}",
            "skin_state": d.skin_state,
        }
        for i, d in enumerate(spec.days)
    ]
    return {
        "config": {
            "model": spec.image_model,
            "size": spec.size,
            "persona": spec.persona.strip(),
        },
        "days": days,
    }


def new_imagegen_job(
    *,
    days_input: list[dict[str, Any]],
    model: str,
    size: str,
    edit_template: str,
) -> dict[str, Any]:
    if not days_input:
        raise ValueError("days_input 不能为空")
    if not model.strip():
        raise ValueError("model 不能为空")
    state: dict[str, Any] = {
        "job_id": uuid.uuid4().hex,
        "state": "running",
        "error": "",
        "items": [
            {
                "day": int(d["day"]),
                "label": str(d.get("label") or f"D{d['day']}"),
                "prompt": str(d.get("prompt") or ""),
                "refs": [int(r) for r in (d.get("refs") or [])],
                "is_base": bool(d.get("is_base", False)),
                "stage": "pending",
                "error": "",
            }
            for d in days_input
        ],
        "_images": {},
        "_model": model.strip(),
        "_size": size.strip(),
        "_edit_template": edit_template,
    }
    IMAGEGEN_JOBS[state["job_id"]] = state
    return state


def _to_data_url(data: bytes) -> str:
    return f"data:image/png;base64,{base64.b64encode(data).decode('ascii')}"


def _generate_png_sync(
    model: str, size: str, prompt: str, ref_data_urls: list[str]
) -> bytes:
    load_dotenv()
    api_key = os.environ["VOLCENGINE_API_KEY"]
    api_base = os.environ.get(
        "VOLCENGINE_API_BASE", "https://ark.cn-beijing.volces.com/api/v3"
    ).rstrip("/")
    body: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "response_format": "b64_json",
        "watermark": False,
    }
    if ref_data_urls:
        body["image"] = ref_data_urls
    resp = httpx.post(
        f"{api_base}/images/generations",
        json=body,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=180.0,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"images/generations {resp.status_code}: {resp.text[:500]}")
    data = resp.json()["data"]
    if not data or "b64_json" not in data[0]:
        raise RuntimeError(f"响应缺少 b64_json: {str(data)[:300]}")
    return base64.b64decode(data[0]["b64_json"])


async def _generate_one(
    item: dict[str, Any],
    images: dict[int, bytes],
    model: str,
    size: str,
) -> None:
    """生成单天图片；结果写入 images[day] 并落盘。异常只更新 item，不上抛。"""
    item["stage"] = "generating"
    try:
        png_bytes = await asyncio.to_thread(
            _generate_png_sync, model, size, item["prompt"], []
        )
        images[item["day"]] = png_bytes
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / f"day{item['day']}.png").write_bytes(png_bytes)
        item["stage"] = "done"
    except Exception as exc:
        logger.warning("lab.imagegen day{} failed: {}", item["day"], exc)
        item["stage"] = "failed"
        item["error"] = str(exc)


async def run_generation(state: dict[str, Any]) -> None:
    """全部天并行提交；每天的 prompt 已由前端拼好（persona + skin_state），无跨天依赖。"""
    model: str = state["_model"]
    size: str = state["_size"]
    images: dict[int, bytes] = state["_images"]

    try:
        await asyncio.gather(
            *[_generate_one(item, images, model, size) for item in state["items"]]
        )
        failed = [it for it in state["items"] if it["stage"] == "failed"]
        if failed:
            state["state"] = "failed"
            state["error"] = "；".join(
                f"day{it['day']} {it['error']}" for it in failed
            )
        else:
            state["state"] = "done"
    except Exception as exc:
        logger.exception("lab.imagegen batch failed")
        state["state"] = "failed"
        state["error"] = str(exc)


def get_image(job_id: str, day: int) -> bytes | None:
    state = IMAGEGEN_JOBS.get(job_id)
    if state is None:
        return None
    return state.get("_images", {}).get(day)


def get_imagegen_zip(job_id: str) -> bytes | None:
    """把该 job 所有图片打包成 zip bytes 返回。"""
    state = IMAGEGEN_JOBS.get(job_id)
    if state is None or state["state"] != "done":
        return None
    images: dict[int, bytes] = state.get("_images", {})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for d in sorted(images):
            item = next((it for it in state["items"] if it["day"] == d), {})
            label = (item.get("label") or f"D{d}").replace("/", "-").replace(".", "-")
            zf.writestr(f"day{d}_{label}.png", images[d])
    return buf.getvalue()


def get_imagegen_photos(job_id: str) -> list[tuple[str, bytes]] | None:
    """BackfillRunner 兼容格式：list[(filename, bytes)]，按 day 升序。"""
    state = IMAGEGEN_JOBS.get(job_id)
    if state is None or state["state"] != "done":
        return None
    images: dict[int, bytes] = state.get("_images", {})
    return [(f"skin7d_day{d}.png", images[d]) for d in sorted(images)]


def list_done_jobs() -> list[dict[str, Any]]:
    """已完成的 job 摘要，供回填面板的下拉选择。"""
    return [
        {
            "job_id": s["job_id"],
            "count": len(s.get("_images", {})),
            "labels": [it.get("label", "") for it in s["items"]],
        }
        for s in IMAGEGEN_JOBS.values()
        if s["state"] == "done"
    ]
