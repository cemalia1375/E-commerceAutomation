"""EmbeddingService + provider implementations."""
from __future__ import annotations

import aiohttp
from openai import AsyncOpenAI
from typing import Any, Protocol


class EmbeddingService(Protocol):
    """embedding 服务抽象接口。"""

    async def embed(self, text: str) -> list[float]: ...


class OllamaEmbeddingService:
    """通过 Ollama REST API 调用 bge-m3 生成 embedding。"""

    def __init__(self, base_url: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model

    async def embed(self, text: str) -> list[float]:
        """调用 POST {base_url}/api/embeddings，返回 embedding 向量。"""
        if not text.strip():
            return []

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self._base_url}/api/embeddings",
                json={"model": self._model, "prompt": text},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return data["embedding"]


class OpenAICompatibleEmbeddingService:
    """通过 OpenAI-compatible embeddings API 生成向量。

    适用于 OpenAI 官方接口，也适用于暴露 `/v1/embeddings` 的兼容服务。
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
        request_dimensions: int | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        if not api_key:
            raise ValueError(
                "FLOWCUT_EMBEDDING_API_KEY or OPENAI_API_KEY is required "
                "when FLOWCUT_EMBEDDING_PROVIDER=openai"
            )
        self._model = model
        self._request_dimensions = request_dimensions
        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "timeout": timeout_s,
        }
        if base_url:
            kwargs["base_url"] = base_url
        self._client = AsyncOpenAI(**kwargs)

    async def embed(self, text: str) -> list[float]:
        """调用 embeddings.create，返回第一条 embedding。"""
        if not text.strip():
            return []

        params: dict[str, Any] = {
            "model": self._model,
            "input": text,
        }
        if self._request_dimensions is not None:
            params["dimensions"] = self._request_dimensions
        response = await self._client.embeddings.create(**params)
        return [float(x) for x in response.data[0].embedding]


class ArkMultimodalEmbeddingService:
    """火山 Ark 多模态 embedding API。

    当前 Flowcut 素材检索只需要文本向量，因此这里把文本包装为：
    {"type": "text", "text": "..."}。
    """

    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str,
        model: str,
        timeout_s: float = 30.0,
    ) -> None:
        if not api_key:
            raise ValueError(
                "FLOWCUT_EMBEDDING_API_KEY or VOLCENGINE_API_KEY is required "
                "when FLOWCUT_EMBEDDING_PROVIDER=ark_multimodal"
            )
        self._api_key = api_key
        self._endpoint = endpoint
        self._model = model
        self._timeout_s = timeout_s

    async def embed(self, text: str) -> list[float]:
        """调用 Ark multimodal embedding endpoint，返回文本 embedding。"""
        if not text.strip():
            return []

        payload = {
            "model": self._model,
            "input": [
                {
                    "type": "text",
                    "text": text,
                }
            ],
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._endpoint,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=self._timeout_s),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
        return _extract_embedding(data)


def build_embedding_service(config: dict) -> EmbeddingService:
    """根据配置创建 embedding service。"""
    provider = str(config.get("provider") or "ollama").strip().lower()
    if provider == "ollama":
        return OllamaEmbeddingService(
            base_url=str(config["base_url"]),
            model=str(config["model"]),
        )
    if provider == "openai":
        return OpenAICompatibleEmbeddingService(
            api_key=str(config.get("api_key") or ""),
            base_url=str(config.get("base_url") or ""),
            model=str(config["model"]),
            request_dimensions=config.get("request_dimensions"),
            timeout_s=float(config.get("timeout_s") or 30.0),
        )
    if provider == "ark_multimodal":
        return ArkMultimodalEmbeddingService(
            api_key=str(config.get("api_key") or ""),
            endpoint=str(config.get("endpoint") or ""),
            model=str(config["model"]),
            timeout_s=float(config.get("timeout_s") or 30.0),
        )
    raise ValueError(
        "Unsupported FLOWCUT_EMBEDDING_PROVIDER="
        f"{provider!r}; supported values: ollama, openai, ark_multimodal"
    )


def _extract_embedding(payload: Any) -> list[float]:
    """从不同 embedding API 响应形态中提取向量。

    Ark multimodal 响应形态可能与标准 OpenAI embeddings 不完全一致；
    这里兼容常见结构，避免把解析逻辑绑死到一个字段路径。
    """
    candidates: list[Any] = []
    if isinstance(payload, dict):
        candidates.extend([
            payload.get("embedding"),
            payload.get("vector"),
            payload.get("embeddings"),
            payload.get("data"),
        ])
    else:
        candidates.append(payload)

    seen: set[int] = set()
    while candidates:
        current = candidates.pop(0)
        if current is None:
            continue
        ident = id(current)
        if ident in seen:
            continue
        seen.add(ident)

        if _is_number_list(current):
            return [float(x) for x in current]

        if isinstance(current, dict):
            candidates.extend([
                current.get("embedding"),
                current.get("vector"),
                current.get("embeddings"),
                current.get("data"),
            ])
        elif isinstance(current, list):
            candidates.extend(current)

    raise ValueError("Embedding API response does not contain an embedding vector")


def _is_number_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(x, int | float) for x in value)
    )
