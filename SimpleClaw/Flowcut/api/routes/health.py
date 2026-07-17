import os
from urllib.parse import urlparse

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health():
    base_url = os.getenv("GEMINI_BASE_URL", "").strip()
    proxy = os.getenv("GEMINI_PROXY", "").strip()
    return {
        "status": "ok",
        "service": "flowcut",
        "runtime_config": {
            "llm_provider": os.getenv("FLOWCUT_LLM_PROVIDER", "").strip(),
            "gemini_transport": "base_url" if base_url else ("proxy" if proxy else "direct"),
            "gemini_base_host": urlparse(base_url).netloc.lower() if base_url else "",
        },
    }
