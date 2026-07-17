"""Flowcut 应用配置。"""
from __future__ import annotations
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from simpleclaw.llm.config import GeminiConfig, VolcengineConfig

load_dotenv()


def _resource_path(rel: str) -> Path:
    # PyInstaller 打包后资源位于 _MEIPASS；开发时回退到源码目录
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
    return base / rel


_WORKSPACE = _resource_path("workspace")
_STABLE_FILES = ("Agent.md", "SOUL.md", "TOOL.md", "compliance.md")
_COMPLIANCE_FILE = "compliance.md"


def make_llm_config() -> GeminiConfig | VolcengineConfig:
    """主 Agent 用的模型。"""
    provider = _llm_provider()
    if provider == "volcengine":
        return VolcengineConfig(
            api_key=os.environ["VOLCENGINE_API_KEY"],
            api_base=os.getenv("VOLCENGINE_API_BASE"),
            model=os.getenv("VOLCENGINE_MODEL", "doubao-seed-2-0-lite-260215"),
            temperature=float(os.getenv("FLOWCUT_LLM_TEMPERATURE", "0.7")),
            max_tokens=int(os.getenv("FLOWCUT_LLM_MAX_TOKENS", "4096")),
        )
    return GeminiConfig(
        api_key=os.environ["GOOGLE_API_KEY"],
        model=os.getenv("GOOGLE_MODEL", "gemini-3.1-flash-lite"),
    )


def make_hook_llm_config() -> GeminiConfig | VolcengineConfig:
    """后台异步任务用的模型，独立实例。"""
    provider = _llm_provider()
    if provider == "volcengine":
        return VolcengineConfig(
            api_key=os.environ["VOLCENGINE_API_KEY"],
            api_base=os.getenv("VOLCENGINE_API_BASE"),
            model=os.getenv("VOLCENGINE_HOOK_MODEL", os.getenv("VOLCENGINE_MODEL", "doubao-seed-2-0-lite-260215")),
            temperature=float(os.getenv("FLOWCUT_HOOK_TEMPERATURE", "0.7")),
            max_tokens=int(os.getenv("FLOWCUT_HOOK_MAX_TOKENS", "4096")),
        )
    return GeminiConfig(
        api_key=os.environ["GOOGLE_API_KEY"],
        model=os.getenv("FLOWCUT_HOOK_MODEL", "gemini-3.1-flash-lite"),
    )


def make_first_token_llm_config() -> GeminiConfig | VolcengineConfig:
    """first_token_llm：低延迟首 token，限制输出长度。"""
    provider = _llm_provider()
    if provider == "volcengine":
        return VolcengineConfig(
            api_key=(
                os.getenv("VOLCENGINE_FIRST_TOKEN_API_KEY", "").strip()
                or os.environ["VOLCENGINE_API_KEY"]
            ),
            api_base=os.getenv("VOLCENGINE_API_BASE"),
            model=os.getenv(
                "VOLCENGINE_FIRST_TOKEN_MODEL",
                os.getenv("VOLCENGINE_MODEL", "doubao-seed-2-0-lite-260215"),
            ),
            temperature=float(os.getenv("FIRST_TOKEN_TEMPERATURE", os.getenv("FLOWCUT_FIRST_TOKEN_TEMPERATURE", "0.6"))),
            max_tokens=int(os.getenv("FIRST_TOKEN_MAX_TOKENS", os.getenv("FLOWCUT_FIRST_TOKEN_MAX_TOKENS", "48"))),
        )
    return GeminiConfig(
        api_key=os.environ["GOOGLE_API_KEY"],
        model=os.getenv("FLOWCUT_FIRST_TOKEN_MODEL", "gemini-3.1-flash-lite"),
        temperature=float(os.getenv("FLOWCUT_FIRST_TOKEN_TEMPERATURE", "0.6")),
        max_tokens=int(os.getenv("FLOWCUT_FIRST_TOKEN_MAX_TOKENS", "48")),
    )


def _llm_provider() -> str:
    provider = os.getenv("FLOWCUT_LLM_PROVIDER", "").strip().lower()
    if not provider:
        provider = "gemini" if os.getenv("GOOGLE_API_KEY") else "volcengine"
    if provider in {"ark", "doubao", "volcano"}:
        provider = "volcengine"
    if provider not in {"gemini", "volcengine"}:
        raise ValueError(
            "Unsupported FLOWCUT_LLM_PROVIDER="
            f"{provider!r}; supported values: gemini, volcengine"
        )
    return provider


def make_first_token_enabled() -> bool:
    """返回 first_token 功能是否启用，默认开启。"""
    value = os.getenv("FLOWCUT_FIRST_TOKEN_ENABLED", "true").strip().lower()
    return value not in {"0", "false", "no", "off"}


def make_first_token_timeout_s() -> float:
    """返回 first_token 超时时长（秒），从毫秒环境变量换算，最低 0.05s。"""
    return max(float(os.getenv("FLOWCUT_FIRST_TOKEN_TIMEOUT_MS", "2500")) / 1000.0, 0.05)


def make_ollama_config() -> dict:
    """返回 Ollama Embedding 配置（兼容旧调用）。"""
    return {
        "base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        "model": os.getenv("OLLAMA_EMBEDDING_MODEL", "bge-m3"),
    }


def make_embedding_config() -> dict:
    """返回 Flowcut embedding 配置。

    默认保持旧行为：Ollama + bge-m3 + 1024 维。
    若设置 FLOWCUT_EMBEDDING_PROVIDER=openai，则使用 OpenAI-compatible
    embeddings API，并从 FLOWCUT_EMBEDDING_* / OPENAI_* 环境变量读取配置。
    """
    provider = os.getenv(
        "FLOWCUT_EMBEDDING_PROVIDER",
        os.getenv("EMBEDDING_PROVIDER", "ollama"),
    ).strip().lower()
    if provider in {"openai-compatible", "openai_compatible", "api"}:
        provider = "openai"
    if provider in {"ark", "volcengine", "doubao", "ark_multimodal"}:
        provider = "ark_multimodal"

    if provider == "ollama":
        return {
            "provider": "ollama",
            "base_url": os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            "model": os.getenv("OLLAMA_EMBEDDING_MODEL", "bge-m3"),
            "vector_size": _env_int("FLOWCUT_EMBEDDING_VECTOR_SIZE", 1024),
            "timeout_s": float(os.getenv("FLOWCUT_EMBEDDING_TIMEOUT_S", "30")),
        }

    if provider == "ark_multimodal":
        return {
            "provider": "ark_multimodal",
            "api_key": (
                os.getenv("FLOWCUT_EMBEDDING_API_KEY", "").strip()
                or os.getenv("VOLCENGINE_API_KEY", "").strip()
            ),
            "endpoint": (
                os.getenv("FLOWCUT_EMBEDDING_BASE_URL", "").strip()
                or "https://ark.cn-beijing.volces.com/api/v3/embeddings/multimodal"
            ),
            "model": (
                os.getenv("FLOWCUT_EMBEDDING_MODEL", "").strip()
                or "doubao-embedding-vision-251215"
            ),
            # 留空时 container 会启动期探测一次真实维度。
            "vector_size": _env_int("FLOWCUT_EMBEDDING_VECTOR_SIZE", 0),
            "timeout_s": float(os.getenv("FLOWCUT_EMBEDDING_TIMEOUT_S", "30")),
        }

    return {
        "provider": provider,
        "api_key": (
            os.getenv("FLOWCUT_EMBEDDING_API_KEY", "").strip()
            or os.getenv("OPENAI_API_KEY", "").strip()
        ),
        "base_url": (
            os.getenv("FLOWCUT_EMBEDDING_BASE_URL", "").strip()
            or os.getenv("OPENAI_BASE_URL", "").strip()
        ),
        "model": (
            os.getenv("FLOWCUT_EMBEDDING_MODEL", "").strip()
            or os.getenv("OPENAI_EMBEDDING_MODEL", "").strip()
            or "text-embedding-3-small"
        ),
        "vector_size": _env_int("FLOWCUT_EMBEDDING_VECTOR_SIZE", 1536),
        "request_dimensions": _optional_int(
            os.getenv("FLOWCUT_EMBEDDING_REQUEST_DIMENSIONS", "").strip()
        ),
        "timeout_s": float(os.getenv("FLOWCUT_EMBEDDING_TIMEOUT_S", "30")),
    }


def _optional_int(value: str) -> int | None:
    if not value:
        return None
    return int(value)


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    return int(value)


def make_qdrant_url() -> str:
    """返回 Qdrant 服务地址。"""
    return os.getenv("QDRANT_URL", "http://localhost:6333")


def make_db_kwargs() -> dict:
    """返回 aiomysql 连接池参数，含连接池大小配置。"""
    return {
        "host": os.environ["MYSQL_HOST"],
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.environ["MYSQL_USER"],
        "password": os.environ["MYSQL_PASSWORD"],
        "db": os.environ["MYSQL_DB"],
        "minsize": int(os.getenv("MYSQL_POOL_MINSIZE", "2")),
        "maxsize": int(os.getenv("MYSQL_POOL_MAXSIZE", "30")),
    }


def make_task_queue():
    """返回 InMemoryTaskQueue 或 RedisTaskQueue，取决于 REDIS_URL 是否配置。"""
    from simpleclaw.runtime.task_queue import InMemoryTaskQueue, RedisTaskQueue
    redis_url = os.getenv("REDIS_URL", "").strip()
    if redis_url:
        return RedisTaskQueue(url=redis_url, stream_prefix="flowcut:tasks")
    return InMemoryTaskQueue()


def make_oss_config() -> dict:
    """返回 OSS 存储配置（endpoint、密钥、bucket、region）。"""
    return {
        "endpoint": os.getenv("FLOWCUT_OSS_ENDPOINT", ""),
        "access_key_id": os.getenv("FLOWCUT_OSS_ACCESS_KEY_ID", ""),
        "access_key_secret": os.getenv("FLOWCUT_OSS_ACCESS_KEY_SECRET", ""),
        "bucket": os.getenv("FLOWCUT_OSS_BUCKET", ""),
        "region": os.getenv("FLOWCUT_OSS_REGION", ""),
    }


def make_qc_cdp_url() -> str:
    """千川 Chromium CDP 调试端口 URL，默认 9222。"""
    return os.getenv("FLOWCUT_QC_CDP_URL", "http://127.0.0.1:9222")


def make_auth_config() -> dict:
    """返回登录会话与 cookie 配置。

    - session_ttl_seconds：登录会话有效期，默认 7 天。
    - cookie_secure：是否仅 HTTPS 下发 cookie，生产置 true，本地 http 调试 false。
    - cookie_samesite：SameSite 策略（lax/strict/none）。
    """
    return {
        "session_ttl_seconds": int(os.getenv("FLOWCUT_SESSION_TTL_S", str(7 * 24 * 3600))),
        "cookie_name": os.getenv("FLOWCUT_COOKIE_NAME", "fc_sid"),
        "cookie_secure": os.getenv("FLOWCUT_COOKIE_SECURE", "false").lower() in ("true", "1"),
        "cookie_samesite": os.getenv("FLOWCUT_COOKIE_SAMESITE", "lax"),
    }


def make_cors_origins() -> list[str]:
    """允许携带 cookie 的前端来源（逗号分隔），默认本地 Vite 端口。"""
    raw = os.getenv("FLOWCUT_CORS_ORIGINS", "http://localhost:5173").strip()
    return [o.strip() for o in raw.split(",") if o.strip()]


def load_stable_sections() -> list[str]:
    """从 workspace 目录读取稳定段落，按顺序返回字符串列表。"""
    sections = []
    for fname in _STABLE_FILES:
        fpath = _WORKSPACE / fname
        if fpath.exists():
            content = fpath.read_text(encoding="utf-8").strip()
            if content:
                sections.append(content)
    return sections


def load_compliance() -> str:
    """读取共享合规约束文本。"""
    fpath = _WORKSPACE / _COMPLIANCE_FILE
    return fpath.read_text(encoding="utf-8") if fpath.exists() else ""
