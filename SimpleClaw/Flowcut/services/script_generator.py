"""Flowcut 脚本生成服务 — 基于拆镜数据生成差异化广告脚本。"""
from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

from google.genai import types

from simpleclaw.llm.genai_client import make_genai_client

_DEFAULT_MODEL = "gemini-3.1-flash-lite"

ROLES: list[dict[str, str]] = [
    {
        "name": "痛点型",
        "instruction": (
            "开头直击用户痛点，建立共鸣，产品作为解法登场。"
            "情绪基调：共鸣 → 希望。"
        ),
    },
    {
        "name": "场景型",
        "instruction": (
            "描绘真实使用场景，代入感强，让观众想象自己正在使用产品。"
            "情绪基调：轻松 → 向往。"
        ),
    },
    {
        "name": "对比型",
        "instruction": (
            "呈现使用前后的明显对比，强化产品效果，制造惊喜感。"
            "情绪基调：怀疑 → 惊喜。"
        ),
    },
    {
        "name": "口碑型",
        "instruction": (
            "以真实用户证言视角叙述，增强可信度，引发推荐欲望。"
            "情绪基调：信任 → 推荐。"
        ),
    },
]


def _build_prompt(role: dict[str, str], scene_data: list[dict]) -> str:
    scene_json = json.dumps(scene_data, ensure_ascii=False, indent=2)
    return (
        f"你是一名专业的抖音短视频脚本创作专家，擅长「{role['name']}」风格。\n"
        f"该风格特点：{role['instruction']}\n\n"
        f"以下是爆款视频的拆镜数据（共 {len(scene_data)} 个分镜）：\n"
        f"{scene_json}\n\n"
        "请为每个分镜生成画面指引（visual_guide）和口播文案（copy_text），"
        f"创作一条「{role['name']}」广告脚本。\n\n"
        "输出严格遵循以下 JSON 格式，不要添加任何解释文字：\n"
        "{\n"
        f'  "role": "{role["name"]}",\n'
        '  "title": "<一句吸引人的标题>",\n'
        '  "segments": [\n'
        "    {\n"
        '      "segment_idx": 0,\n'
        '      "start_time": 0.0,\n'
        '      "end_time": 3.96,\n'
        '      "visual_guide": "<画面指引>",\n'
        '      "copy_text": "<口播文案>"\n'
        "    },\n"
        "    ...\n"
        "  ]\n"
        "}"
    )


def _parse_script_response(raw_text: str, *, role: str) -> dict[str, Any] | None:
    """从模型返回文本中解析脚本 JSON，容错处理 markdown fence 和缺失字段。"""
    text = raw_text.strip()

    fence = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if fence:
        text = fence.group(1).strip()

    try:
        data = json.loads(text)
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    segments_raw = data.get("segments")
    if not isinstance(segments_raw, list) or len(segments_raw) == 0:
        return None

    segments: list[dict] = []
    for item in segments_raw:
        if not isinstance(item, dict):
            continue
        segments.append({
            "segment_idx": int(item.get("segment_idx", len(segments))),
            "start_time": float(item.get("start_time", 0.0)),
            "end_time": float(item.get("end_time", 0.0)),
            "visual_guide": str(item.get("visual_guide", "")),
            "copy_text": str(item.get("copy_text", "")),
        })

    if not segments:
        return None

    return {
        "role": role,
        "title": str(data.get("title", "")),
        "segments": segments,
    }


def _call_gemini(prompt: str, *, api_key: str, model: str) -> str:
    """同步调用 Gemini（在线程中执行）。"""
    client = make_genai_client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=[types.Part(text=prompt)],
        config=types.GenerateContentConfig(
            temperature=0.8,
            max_output_tokens=4096,
            response_mime_type="application/json",
        ),
    )
    return response.text or ""


async def generate_for_role(
    role: dict[str, str],
    scene_data: list[dict],
    *,
    api_key: str | None = None,
    model: str | None = None,
) -> dict[str, Any] | None:
    """为单个角色生成脚本，失败返回 None。"""
    resolved_key = api_key or os.environ["GOOGLE_API_KEY"]
    resolved_model = model or os.getenv("FLOWCUT_DECOMPOSE_MODEL", _DEFAULT_MODEL)
    prompt = _build_prompt(role, scene_data)

    try:
        raw = await asyncio.to_thread(_call_gemini, prompt, api_key=resolved_key, model=resolved_model)
        return _parse_script_response(raw, role=role["name"])
    except Exception:
        return None
