"""统一构造 google-genai Client，支持代理与自定义 API 端点。

国内网络无法直连 Google Gemini，支持两种方式：
1. GEMINI_PROXY：HTTP 代理（如 Clash），仅对 Gemini 请求生效
2. GEMINI_BASE_URL：自定义 API 端点（如 moyu.info 等第三方中转），国内直连

GEMINI_PROXY 示例：http://127.0.0.1:7890
GEMINI_BASE_URL 示例：https://moyu.info
未设置时行为与原先完全一致（直连 Google）。

重要：第三方中转（如 moyu.info）通常只代理 generateContent 接口，
不支持 Gemini Files API（/upload/v1beta/files）。
需要文件上传（视频拆镜）时，应使用 make_genai_upload_client()，
它会跳过 GEMINI_BASE_URL，通过代理直连 Google。
"""

from __future__ import annotations

import os

import google.genai as genai
from google.genai import types


def _apply_proxy(proxy: str) -> None:
    """设置 HTTP/HTTPS 代理环境变量，排除国内服务。"""
    os.environ["HTTP_PROXY"] = proxy
    os.environ["HTTPS_PROXY"] = proxy
    os.environ["NO_PROXY"] = "localhost,127.0.0.1,.cn,.com.cn,.volces.com,ark.cn"


def make_genai_client(api_key: str) -> genai.Client:
    """构造 genai.Client（用于 chat / generateContent）。

    优先使用 GEMINI_BASE_URL 自定义端点（国内中转服务），
    其次使用 GEMINI_PROXY HTTP 代理，都未设置则直连 Google。
    """
    base_url = os.getenv("GEMINI_BASE_URL", "").strip()
    proxy = os.getenv("GEMINI_PROXY", "").strip()

    if base_url:
        # 第三方 API 中转（如 moyu.info），国内直连无需代理
        return genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(base_url=base_url),
        )

    if proxy:
        _apply_proxy(proxy)
        return genai.Client(api_key=api_key)

    return genai.Client(api_key=api_key)


def make_genai_upload_client(api_key: str) -> genai.Client:
    """构造 genai.Client（用于 Files API 文件上传）。

    第三方中转（如 moyu.info）不支持 Gemini Files API 的
    /upload/v1beta/files 端点，因此本函数跳过 GEMINI_BASE_URL，
    始终直连 Google（通过 GEMINI_PROXY 代理，如需）。

    API Key：优先使用 GOOGLE_API_KEY_DIRECT（真实 Google Key），
    因为中转 Key（如 sk-xxx）Google 不识别。
    """
    proxy = os.getenv("GEMINI_PROXY", "").strip()
    direct_key = os.getenv("GOOGLE_API_KEY_DIRECT", "").strip()
    resolved_key = direct_key or api_key

    if proxy:
        _apply_proxy(proxy)
        return genai.Client(api_key=resolved_key)

    return genai.Client(api_key=resolved_key)
