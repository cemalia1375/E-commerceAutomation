"""火山引擎 / 豆包 LLM 提供方 — Responses API + 显式前缀缓存。

缓存策略
----------------
调用方（通过 ContextBuilder）将系统提示词拆分为两部分：
  - 稳定前缀
  - 动态尾部
VolcengineLLM 在服务端预先缓存稳定前缀，并接收一个 response_id。
后续请求只传入 previous_response_id + 动态尾部，
因此稳定前缀的 token 不会被重复发送或重新计算。

流式行为
-------------------
火山引擎 Responses API 通过 `response.output_text.delta` 事件逐 token 流式返回文本。
工具调用不会增量流式传输 — 它们在最终的 `response.completed` 事件中完整到达。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable

import json_repair
from loguru import logger
from openai import AsyncOpenAI

from simpleclaw.core.timing import elapsed_ms
from simpleclaw.llm.base import LLMProvider
from simpleclaw.llm.chunks import Chunk, TextChunk, ToolCallChunk
from simpleclaw.llm.config import VolcengineConfig


@dataclass(slots=True)
class PrefixCacheEntry:
    """单个服务端前缀缓存槽的内存句柄。"""

    response_id: str
    created_at: float
    expire_at: float
    prefix_hash: str


@dataclass(slots=True)
class SessionResponse:
    """A non-streaming Responses API session-cache result."""

    response_id: str
    output_text: str
    raw: Any


class VolcengineLLM(LLMProvider):
    """火山引擎 Responses API 提供方，支持显式前缀缓存复用。"""

    _CACHE_TTL_S = 2 * 24 * 60 * 60        # 2 days
    _UNSUPPORTED_STRING_SCHEMA_KEYS = frozenset({"minLength", "maxLength"})

    def __init__(
        self,
        config: VolcengineConfig,
        *,
        cache_repo: Any | None = None,
        prefix_cache_lane: str = "default",
    ) -> None:
        super().__init__(config)
        self.config: VolcengineConfig = config
        self._cache_repo = cache_repo
        self._prefix_cache_lane = prefix_cache_lane or "default"

        headers = dict(config.extra_headers)
        affinity = config.session_affinity_id or uuid.uuid4().hex
        headers.setdefault("x-session-affinity", affinity)

        self._client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.api_base,
            default_headers=headers,
        )
        self._prefix_cache: dict[str, PrefixCacheEntry] = {}
        self._prefix_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # LLMProvider 契约
    # ------------------------------------------------------------------

    async def stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
        tool_choice: str | None = None,
    ) -> AsyncIterator[Chunk]:
        logger.info("⏱ ttft llm.stream.enter +{}ms msgs={}", elapsed_ms(), len(messages))

        resolved_max_tokens, resolved_temperature = self._resolved(max_tokens, temperature)
        converted_tools = self._convert_tools(tools)

        request_kwargs, cache_debug = await self._prepare_request(
            messages=messages,
            tools=converted_tools,
            max_tokens=resolved_max_tokens,
            temperature=resolved_temperature,
            tool_choice=tool_choice,
        )
        request_kwargs["stream"] = True

        # 记录本次真正发出去的上下文规模，便于定位 "发太多" 类问题
        _input = request_kwargs.get("input") or []
        _input_bytes = sum(len(str(it.get("content") or it.get("output") or "")) for it in _input)
        logger.info(
            "⏱ ttft llm.prepared +{}ms input_items={} input_bytes={} "
            "cache_reused={} previous_response_id={} tools={} stream={}",
            elapsed_ms(),
            len(_input),
            _input_bytes,
            cache_debug.get("reused", False),
            (request_kwargs.get("previous_response_id") or "")[:24],
            bool(request_kwargs.get("tools")),
            bool(request_kwargs.get("stream")),
        )

        logger.info("⏱ ttft llm.request_start +{}ms", elapsed_ms())
        stream = await self._client.responses.create(**request_kwargs)
        logger.info("⏱ ttft llm.stream_opened +{}ms", elapsed_ms())
        final_response: Any | None = None
        first_delta_logged = False
        first_event_logged = False

        async for event in stream:
            event_type = getattr(event, "type", None)
            if not first_event_logged:
                logger.info("⏱ ttft llm.first_event +{}ms type={}", elapsed_ms(), event_type)
                first_event_logged = True
            if event_type == "response.output_text.delta":
                delta = getattr(event, "delta", None)
                if isinstance(delta, str) and delta:
                    if not first_delta_logged:
                        logger.info("⏱ ttft llm.first_delta +{}ms", elapsed_ms())
                        first_delta_logged = True
                    if self.config.thinking:
                        for chunk in self._filter_think(delta):
                            yield chunk
                    else:
                        yield TextChunk(delta)
            elif event_type == "response.completed":
                final_response = getattr(event, "response", None)
                self._log_completion(final_response)

        if final_response is None:
            if converted_tools:
                raise RuntimeError("Volcengine stream ended without a final response event")
            logger.debug("火山引擎流结束时未收到 response.completed 事件（纯文本模式）")
            return

        # 工具调用在最终响应中完整到达 — 在此处 yield。
        for item in getattr(final_response, "output", None) or []:
            if getattr(item, "type", None) == "function_call":
                raw_args = getattr(item, "arguments", None) or "{}"
                try:
                    arguments = json_repair.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except Exception:
                    arguments = {"raw": raw_args}
                yield ToolCallChunk(
                    id=str(getattr(item, "call_id", None) or getattr(item, "id", "") or uuid.uuid4().hex),
                    name=str(getattr(item, "name", None) or "unknown_tool"),
                    arguments=arguments,
                )

    async def complete_session(
        self,
        *,
        input_items: list[dict[str, Any]],
        previous_response_id: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> SessionResponse:
        """Run a no-tools non-streaming Responses request with session cache.

        This is the low-level provider primitive for app-level session-cache
        lanes such as first-token openers or short-window background classifiers.
        Business lanes decide what to store and when to invalidate; this method
        only performs the provider call and returns the new response id.
        """
        resolved_max_tokens, resolved_temperature = self._resolved(max_tokens, temperature)
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "input": input_items,
            "max_output_tokens": max(1, resolved_max_tokens),
            "temperature": resolved_temperature,
            "extra_body": self._build_extra_body(cache_enabled=True),
        }
        if previous_response_id:
            kwargs["previous_response_id"] = previous_response_id

        logger.info(
            "⏱ ttft llm.session_complete.enter +{}ms model={} input_items={} previous_response_id={} max_tokens={}",
            elapsed_ms(),
            self.config.model,
            len(input_items),
            (previous_response_id or "")[:24],
            max(1, resolved_max_tokens),
        )
        logger.info("⏱ ttft llm.session_complete.request_start +{}ms", elapsed_ms())
        response = await self._client.responses.create(**kwargs)
        self._log_completion(response)
        response_id = str(getattr(response, "id", "") or "")
        if not response_id:
            raise RuntimeError("Volcengine session response returned no response_id")
        return SessionResponse(
            response_id=response_id,
            output_text=self._extract_output_text(response),
            raw=response,
        )

    async def stream_session(
        self,
        *,
        input_items: list[dict[str, Any]],
        previous_response_id: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        on_text: Callable[[str], Awaitable[None]] | None = None,
    ) -> SessionResponse:
        """Run a no-tools streaming Responses request with session cache.

        Text deltas are forwarded through ``on_text`` as they arrive. The final
        response id is returned after ``response.completed`` so callers can
        persist the session cache chain head.
        """
        resolved_max_tokens, resolved_temperature = self._resolved(max_tokens, temperature)
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "input": input_items,
            "max_output_tokens": max(1, resolved_max_tokens),
            "temperature": resolved_temperature,
            "extra_body": self._build_extra_body(cache_enabled=True),
            "stream": True,
        }
        if previous_response_id:
            kwargs["previous_response_id"] = previous_response_id

        logger.info(
            "⏱ ttft llm.session_stream.enter +{}ms model={} input_items={} previous_response_id={} max_tokens={}",
            elapsed_ms(),
            self.config.model,
            len(input_items),
            (previous_response_id or "")[:24],
            max(1, resolved_max_tokens),
        )
        logger.info("⏱ ttft llm.session_stream.request_start +{}ms", elapsed_ms())
        stream = await self._client.responses.create(**kwargs)
        logger.info("⏱ ttft llm.session_stream.opened +{}ms", elapsed_ms())
        final_response: Any | None = None
        parts: list[str] = []
        first_event_logged = False
        first_delta_logged = False

        async for event in stream:
            event_type = getattr(event, "type", None)
            if not first_event_logged:
                logger.info("⏱ ttft llm.session_stream.first_event +{}ms type={}", elapsed_ms(), event_type)
                first_event_logged = True
            if event_type == "response.output_text.delta":
                delta = getattr(event, "delta", None)
                if isinstance(delta, str) and delta:
                    if not first_delta_logged:
                        logger.info("⏱ ttft llm.session_stream.first_delta +{}ms", elapsed_ms())
                        first_delta_logged = True
                    deltas = [c.token for c in self._filter_think(delta)] if self.config.thinking else [delta]
                    for text in deltas:
                        if not text:
                            continue
                        parts.append(text)
                        if on_text is not None:
                            await on_text(text)
            elif event_type == "response.completed":
                final_response = getattr(event, "response", None)
                self._log_completion(final_response)

        if final_response is None:
            raise RuntimeError("Volcengine session stream ended without response.completed")

        response_id = str(getattr(final_response, "id", "") or "")
        if not response_id:
            raise RuntimeError("Volcengine session stream returned no response_id")
        output_text = "".join(parts) or self._extract_output_text(final_response)
        return SessionResponse(
            response_id=response_id,
            output_text=output_text,
            raw=final_response,
        )

    # ------------------------------------------------------------------
    # 完成事件日志：usage（input/cached/output）+ 相对时刻
    # ------------------------------------------------------------------

    def _log_completion(self, final_response: Any) -> None:
        """收到 response.completed 时打印 usage，用于判定服务端是否真的命中了缓存。

        关键字段：
          - input_tokens：本轮发送的总输入 token 数
          - cached_tokens：其中命中缓存的数量（来自 input_tokens_details）
          - output_tokens：生成的 token 数
        cached / input 比值接近 1 ⇒ 命中充分；接近 0 ⇒ 缓存未生效。
        """
        usage = getattr(final_response, "usage", None) if final_response is not None else None
        if usage is None:
            logger.info("⏱ ttft llm.completed +{}ms (no usage)", elapsed_ms())
            return

        input_tokens  = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        details       = getattr(usage, "input_tokens_details", None)
        cached_tokens = getattr(details, "cached_tokens", 0) if details is not None else 0
        ratio         = (cached_tokens / input_tokens * 100) if input_tokens else 0.0
        response_id   = getattr(final_response, "id", "") or ""

        logger.info(
            "⏱ ttft llm.completed +{}ms input={} cached={} ({:.0f}%) output={} resp_id={}",
            elapsed_ms(),
            input_tokens,
            cached_tokens,
            ratio,
            output_tokens,
            response_id[:24],
        )

    # ------------------------------------------------------------------
    # 前缀缓存管理
    # ------------------------------------------------------------------

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    @staticmethod
    def _hash_json(value: Any) -> str:
        return hashlib.sha256(
            json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()

    async def _ensure_prefix_cache(
        self,
        *,
        lane: str,
        tenant_key: str,
        session_key: str,
        stable_prefix: str,
        tools: list[dict] | None,
    ) -> PrefixCacheEntry | None:
        if not self.config.prefix_cache:
            return None
        if not stable_prefix.strip():
            return None

        tool_hash = self._hash_json(tools or [])
        prefix_hash = self._hash(stable_prefix)
        cache_key = "::".join([
            lane,
            tenant_key,
            session_key,
            self.config.model,
            prefix_hash,
            tool_hash,
            str(self.config.thinking),
        ])

        # 快速路径：只持锁查 dict，不阻塞 API 调用。
        async with self._prefix_lock:
            now = time.time()
            entry = self._prefix_cache.get(cache_key)
            if entry and entry.expire_at > now and entry.prefix_hash == prefix_hash:
                logger.info(
                    "⏱ ttft prefix_cache.hit +{}ms lane={} stable_bytes={} tool_bytes_json={}",
                    elapsed_ms(), lane, len(stable_prefix), len(json.dumps(tools or []))
                )
                return entry

        repo = self._cache_repo
        if repo is not None and hasattr(repo, "get_prefix_cache"):
            try:
                record = await repo.get_prefix_cache(
                    provider="volcengine",
                    lane=lane,
                    tenant_key=tenant_key,
                    session_key=session_key,
                    model=self.config.model,
                    thinking_type="enabled" if self.config.thinking else "disabled",
                    prompt_fingerprint=prefix_hash,
                    tools_fingerprint=tool_hash,
                )
            except Exception as exc:
                logger.warning("prefix cache DB lookup failed: {}", exc)
                record = None
            if record is not None and record.response_id:
                now = time.time()
                entry = PrefixCacheEntry(
                    response_id=record.response_id,
                    created_at=now,
                    expire_at=float(record.expire_at or (now + self._CACHE_TTL_S)),
                    prefix_hash=prefix_hash,
                )
                async with self._prefix_lock:
                    self._prefix_cache[cache_key] = entry
                logger.info(
                    "⏱ ttft prefix_cache.db_hit +{}ms lane={} stable_bytes={} tool_bytes_json={}",
                    elapsed_ms(), lane, len(stable_prefix), len(json.dumps(tools or []))
                )
                return entry

        # 慢路径：释放锁后再做 API 调用，避免阻塞其他协程的 cache 查找。
        logger.info(
            "⏱ ttft prefix_cache.miss.create +{}ms lane={} stable_bytes={} tool_bytes_json={}",
            elapsed_ms(), lane, len(stable_prefix), len(json.dumps(tools or []))
        )
        response = await self._client.responses.create(
            model=self.config.model,
            input=[{"type": "message", "role": "system", "content": stable_prefix}],
            tools=tools,
            extra_body=self._build_extra_body(cache_prefix=True, cache_enabled=True),
        )
        response_id = getattr(response, "id", None)
        if not isinstance(response_id, str) or not response_id:
            raise RuntimeError("Volcengine prefix cache creation returned no response_id")

        now = time.time()
        expire_at = int(now + self._CACHE_TTL_S)
        new_entry = PrefixCacheEntry(
            response_id=response_id,
            created_at=now,
            expire_at=float(expire_at),
            prefix_hash=prefix_hash,
        )
        async with self._prefix_lock:
            existing = self._prefix_cache.get(cache_key)
            if existing and existing.expire_at > now and existing.prefix_hash == prefix_hash:
                logger.debug("前缀缓存命中（并发创建后）key={}", cache_key[:32])
                return existing
            self._prefix_cache[cache_key] = new_entry
            logger.info(
                "⏱ ttft prefix_cache.created +{}ms resp_id={}",
                elapsed_ms(), response_id[:24]
            )
            if repo is not None and hasattr(repo, "upsert_prefix_cache"):
                try:
                    await repo.upsert_prefix_cache(
                        provider="volcengine",
                        lane=lane,
                        tenant_key=tenant_key,
                        session_key=session_key,
                        model=self.config.model,
                        thinking_type="enabled" if self.config.thinking else "disabled",
                        prompt_fingerprint=prefix_hash,
                        tools_fingerprint=tool_hash,
                        response_id=response_id,
                        expire_at=expire_at,
                        metadata={
                            "stable_bytes": len(stable_prefix),
                            "tool_bytes_json": len(json.dumps(tools or [], ensure_ascii=False)),
                        },
                    )
                except Exception as exc:
                    logger.warning("prefix cache DB upsert failed: {}", exc)
            return new_entry

    # ------------------------------------------------------------------
    # extra_body 构建（对齐 nanobot）
    # ------------------------------------------------------------------

    def _build_extra_body(
        self,
        *,
        cache_prefix: bool = False,
        cache_enabled: bool = False,
    ) -> dict[str, Any] | None:
        """构建 extra_body，同时处理 caching 和 thinking 参数。
        """
        extra_body: dict[str, Any] = {}
        if cache_enabled:
            caching: dict[str, Any] = {"type": "enabled"}
            if cache_prefix:
                caching["prefix"] = True
            extra_body["caching"] = caching
            extra_body["expire_at"] = int(time.time()) + self._CACHE_TTL_S
        extra_body["thinking"] = {
            "type": "enabled" if self.config.thinking else "disabled"
        }
        return extra_body or None

    # ------------------------------------------------------------------
    # 请求构建
    # ------------------------------------------------------------------

    async def _prepare_request(
        self,
        *,
        messages: list[dict],
        tools: list[dict] | None,
        max_tokens: int,
        temperature: float,
        tool_choice: str | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """构建最终的请求 kwargs，在可用时复用前缀缓存。"""
        lane, tenant_key, session_key, stable_prefix, dynamic_tail = self._extract_prompt_parts(messages)

        cache_debug: dict[str, Any] = {"reused": False, "created": False}

        if stable_prefix:
            try:
                entry = await self._ensure_prefix_cache(
                    lane=lane,
                    tenant_key=tenant_key,
                    session_key=session_key,
                    stable_prefix=stable_prefix,
                    tools=tools,
                )
            except Exception as exc:
                logger.warning("前缀缓存创建失败，降级为普通请求：{}", exc)
                entry = None

            if entry is not None:
                tail_messages: list[dict] = []
                if dynamic_tail.strip():
                    tail_messages.append({"role": "system", "content": dynamic_tail})
                tail_messages.extend(messages[1:])
                cache_debug = {"reused": True, "response_id": entry.response_id}
                return self._build_kwargs(
                    input_items=self._convert_messages(tail_messages),
                    tools=None,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    tool_choice=None,
                    previous_response_id=entry.response_id,
                    cache_enabled=True,
                ), cache_debug

        # 无缓存 — 发送全部内容。
        return self._build_kwargs(
            input_items=self._convert_messages(messages),
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            tool_choice=tool_choice,
        ), cache_debug

    def _build_kwargs(
        self,
        *,
        input_items: list[dict],
        tools: list[dict] | None,
        max_tokens: int,
        temperature: float,
        tool_choice: str | None,
        previous_response_id: str | None = None,
        cache_enabled: bool = False,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "input": input_items,
            "max_output_tokens": max(1, max_tokens),
            "temperature": temperature,
        }
        extra_body = self._build_extra_body(cache_enabled=cache_enabled)
        if extra_body:
            kwargs["extra_body"] = extra_body
        if previous_response_id:
            kwargs["previous_response_id"] = previous_response_id
        if tools:
            kwargs["tools"] = tools
            kwargs["parallel_tool_calls"] = True
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
        return kwargs

    # ------------------------------------------------------------------
    # 消息 / 工具格式转换
    # ------------------------------------------------------------------

    def _extract_prompt_parts(self, messages: list[dict]) -> tuple[str, str, str, str, str]:
        """从系统消息中提取 (lane, tenant_key, session_key, stable_prefix, dynamic_tail)。"""
        if not messages:
            return self._prefix_cache_lane, "__default__", "", "", ""
        first = messages[0]
        if str(first.get("role") or "").lower() != "system":
            return self._prefix_cache_lane, "__default__", "", "", ""
        lane = str(first.get("_cache_lane") or self._prefix_cache_lane)
        tenant_key = str(first.get("_cache_tenant_key") or "__default__")
        session_key = str(first.get("_cache_session_key") or "")
        stable_prefix = str(first.get("_cache_stable_prefix") or first.get("content") or "")
        dynamic_tail = str(first.get("_cache_dynamic_tail") or "")
        return lane, tenant_key, session_key, stable_prefix, dynamic_tail

    @classmethod
    def _convert_messages(cls, messages: list[dict]) -> list[dict]:
        """将 OpenAI chat 格式转换为火山引擎 Responses API 输入格式。"""
        items: list[dict] = []
        for msg in messages:
            role = str(msg.get("role") or "")

            if role == "tool":
                output = msg.get("content") or ""
                if not isinstance(output, str):
                    output = json.dumps(output, ensure_ascii=False)
                items.append({
                    "type": "function_call_output",
                    "call_id": str(msg.get("tool_call_id") or ""),
                    "output": output,
                    "status": "completed",
                })
                continue

            if role == "assistant" and isinstance(msg.get("tool_calls"), list):
                if msg.get("content"):
                    items.append({
                        "type": "message",
                        "role": "assistant",
                        "content": str(msg["content"]),
                    })
                for tc in msg["tool_calls"]:
                    fn = tc.get("function") or {}
                    args = fn.get("arguments") or "{}"
                    if not isinstance(args, str):
                        args = json.dumps(args, ensure_ascii=False)
                    items.append({
                        "type": "function_call",
                        "call_id": str(tc.get("id") or ""),
                        "name": str(fn.get("name") or "unknown_tool"),
                        "arguments": args,
                        "status": "completed",
                    })
                continue

            content = cls._convert_content(msg.get("content"))
            if content == "":
                continue
            items.append({
                "type": "message",
                "role": role,
                "content": content,
            })
        return items

    @staticmethod
    def _build_text_content(text: str) -> dict[str, Any]:
        return {"type": "input_text", "text": text}

    @classmethod
    def _convert_content(cls, content: Any) -> str | list[dict[str, Any]]:
        """转换文本/多模态 content 为 Responses API 可接受的格式。"""
        if isinstance(content, str):
            return content
        if isinstance(content, dict):
            content = [content]
        if not isinstance(content, list):
            return json.dumps(content, ensure_ascii=False)

        converted: list[dict[str, Any]] = []
        for item in content:
            if not isinstance(item, dict):
                text = str(item).strip()
                if text:
                    converted.append(cls._build_text_content(text))
                continue
            item_type = item.get("type")
            if item_type == "text":
                text = str(item.get("text") or "").strip()
                if text:
                    converted.append(cls._build_text_content(text))
            elif item_type == "image_url":
                image = item.get("image_url") or {}
                url = image.get("url")
                if isinstance(url, str) and url.strip():
                    converted.append({"type": "input_image", "image_url": url.strip()})
            else:
                converted.append(cls._build_text_content(json.dumps(item, ensure_ascii=False)))
        return converted or ""

    @staticmethod
    def _extract_output_text(response: Any) -> str:
        direct = getattr(response, "output_text", None)
        if isinstance(direct, str) and direct:
            return direct

        parts: list[str] = []
        for item in getattr(response, "output", None) or []:
            content = getattr(item, "content", None)
            if isinstance(content, list):
                for block in content:
                    text = getattr(block, "text", None)
                    if isinstance(text, str):
                        parts.append(text)
                    elif isinstance(block, dict):
                        value = block.get("text")
                        if isinstance(value, str):
                            parts.append(value)
            elif isinstance(content, str):
                parts.append(content)
            elif isinstance(item, dict):
                for block in item.get("content") or []:
                    if isinstance(block, dict) and isinstance(block.get("text"), str):
                        parts.append(block["text"])
        return "".join(parts)

    @classmethod
    def _convert_tools(cls, tools: list[dict] | None) -> list[dict] | None:
        """将 OpenAI 工具 schema 格式转换为火山引擎 Responses API 格式。"""
        if not tools:
            return None
        result = []
        for tool in tools:
            fn = tool.get("function") or {}
            result.append({
                "type": "function",
                "name": fn.get("name"),
                "description": fn.get("description"),
                "parameters": cls._sanitize_schema(fn.get("parameters") or {}),
                "strict": False,
            })
        return result

    @classmethod
    def _sanitize_schema(cls, schema: Any) -> Any:
        """移除火山引擎不支持的 JSON Schema 键。"""
        if isinstance(schema, list):
            return [cls._sanitize_schema(item) for item in schema]
        if not isinstance(schema, dict):
            return schema
        return {
            k: cls._sanitize_schema(v)
            for k, v in schema.items()
            if not (schema.get("type") == "string" and k in cls._UNSUPPORTED_STRING_SCHEMA_KEYS)
        }

    # ------------------------------------------------------------------
    # R1 风格模型的 <think> 标签过滤
    # ------------------------------------------------------------------

    def _filter_think(self, delta: str) -> list[TextChunk]:
        """从流式文本增量中过滤 <think>…</think> 块。"""
        if not hasattr(self, "_in_think"):
            self._in_think = False
            self._think_buf = ""

        if self._in_think:
            self._think_buf += delta
            if "</think>" in self._think_buf:
                _, after = self._think_buf.split("</think>", 1)
                self._in_think = False
                self._think_buf = ""
                return [TextChunk(after)] if after else []
            return []

        if "<think>" in delta:
            before, after = delta.split("<think>", 1)
            self._in_think = True
            self._think_buf = after
            if "</think>" in self._think_buf:
                _, remainder = self._think_buf.split("</think>", 1)
                self._in_think = False
                self._think_buf = ""
                chunks = []
                if before:
                    chunks.append(TextChunk(before))
                if remainder:
                    chunks.append(TextChunk(remainder))
                return chunks
            return [TextChunk(before)] if before else []

        return [TextChunk(delta)]
