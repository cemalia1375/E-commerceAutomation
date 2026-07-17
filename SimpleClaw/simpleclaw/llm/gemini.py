"""Google Gemini LLM 提供方 — 官方 google-genai SDK。

消息格式转换
-------------------
将 OpenAI chat messages 转换为 SDK 的 Content / Part 对象：
  system  → GenerateContentConfig.system_instruction
  user    → Content(role="user",    parts=[Part(...)])
  assistant (text)      → Content(role="model", parts=[Part.from_text(...)])
  assistant (tool_calls)→ Content(role="model", parts=[Part(function_call=...)])
  tool    → Content(role="user",    parts=[Part(function_response=...)])

流式行为
-------------------
每个 chunk 是 GenerateContentResponse，通过以下两个属性读取输出：
  chunk.text           → 文本增量（str | None）
  chunk.function_calls → 完整工具调用列表（SDK 不分片，到达即完整）
"""

from __future__ import annotations

import json
import uuid
from typing import Any, AsyncIterator

from google.genai import types
from loguru import logger

from simpleclaw.llm.base import LLMProvider
from simpleclaw.llm.chunks import Chunk, TextChunk, ToolCallChunk
from simpleclaw.llm.config import GeminiConfig
from simpleclaw.llm.genai_client import make_genai_client


class GeminiLLM(LLMProvider):
    """Google Gemini 提供方，基于官方 google-genai SDK。"""

    def __init__(self, config: GeminiConfig) -> None:
        super().__init__(config)
        self.config: GeminiConfig = config
        self._client = make_genai_client(config.api_key)

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
        resolved_max_tokens, resolved_temperature = self._resolved(max_tokens, temperature)

        system_instruction, contents = self._convert_messages(messages)
        sdk_tools = self._convert_tools(tools)

        config = types.GenerateContentConfig(
            system_instruction=system_instruction or None,
            temperature=resolved_temperature,
            max_output_tokens=resolved_max_tokens,
            tools=sdk_tools or None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        logger.info(
            "GeminiLLM.stream model={} contents={} tools={}",
            self.config.model,
            len(contents),
            bool(sdk_tools),
        )

        response_stream = await self._client.aio.models.generate_content_stream(
            model=self.config.model,
            contents=contents,
            config=config,
        )
        # Gemini 的 thought_signature 可能挂在以下任何一种 Part 上：
        #   (a) 直接挂在 function_call Part 上
        #   (b) 挂在 function_call 之前一个 thought-only / 普通 text Part 上
        # 对于 (b) 中无 text 内容的 thought-only Part，sig 先存入 pending_signatures，
        # 绑给紧随其后的 function_call（作为 ToolCallChunk.thought_signature）。
        # 对于有 text 的普通 Part，sig 通过 TextChunk.thought_signature 传出，
        # 让 loop 层累积进 AssistantMessage.pending_signatures，下轮回传 Gemini。
        pending_signatures: list[bytes] = []

        async for chunk in response_stream:
            candidates = getattr(chunk, "candidates", None) or []
            if not candidates:
                # 流末尾可能有无 candidate 的心跳包，忽略
                continue
            content = getattr(candidates[0], "content", None)
            parts = getattr(content, "parts", None) or [] if content else []

            for part in parts:
                sig = getattr(part, "thought_signature", None)
                fc = getattr(part, "function_call", None)
                text = getattr(part, "text", None)
                is_thought = bool(getattr(part, "thought", False))

                if fc is not None:
                    # function_call Part：优先用自带 sig，否则取最近累积的 pending
                    effective_sig = sig if isinstance(sig, (bytes, bytearray)) else (
                        pending_signatures.pop() if pending_signatures else None
                    )
                    logger.info(
                        "GeminiLLM captured function_call name={} sig={}",
                        fc.name,
                        "present" if effective_sig else "MISSING",
                    )
                    yield ToolCallChunk(
                        id=str(fc.id or uuid.uuid4().hex),
                        name=str(fc.name or "unknown_tool"),
                        arguments=dict(fc.args or {}),
                        thought_signature=bytes(effective_sig) if effective_sig else None,
                    )
                elif text and not is_thought:
                    # 普通文本 Part：实时流出 token，同时把 sig 附带出去
                    yield TextChunk(
                        token=text,
                        thought_signature=bytes(sig) if isinstance(sig, (bytes, bytearray)) else None,
                    )
                elif isinstance(sig, (bytes, bytearray)):
                    # thought-only Part（无 text 或 is_thought=True）：
                    # sig 先进 pending，等待绑给后续 fc；
                    # 若该轮无 fc，流结束时通过空 token TextChunk 发出让 loop 累积
                    pending_signatures.append(bytes(sig))

        # 流结束后仍有残留 pending_signatures（该轮无 fc 的纯推理场景）
        # → 用空 token TextChunk 逐条发出，loop 会累积进 AssistantMessage.pending_signatures
        for sig in pending_signatures:
            yield TextChunk(token="", thought_signature=sig)

    # ------------------------------------------------------------------
    # 消息格式转换
    # ------------------------------------------------------------------

    @classmethod
    def _convert_messages(cls, messages: list[dict]) -> tuple[str, list[types.Content]]:
        """将 OpenAI chat messages 转换为 (system_instruction, Contents)。"""
        system_instruction = ""
        contents: list[types.Content] = []
        # Gemini 要求 FunctionResponse.name 与之前的 FunctionCall.name 完全一致；
        # 但 ReactLoop 序列化 ToolResultMessage 时只保留 tool_call_id / content，
        # 没有 name 字段。这里边遍历边维护 call_id → name 映射，让 tool 角色消息
        # 能反查到真名，避免 Gemini 校验失败 (400 INVALID_ARGUMENT)。
        call_id_to_name: dict[str, str] = {}

        for msg in messages:
            role = str(msg.get("role") or "")

            if role == "system":
                system_instruction = str(msg.get("content") or "")
                continue

            if role == "user":
                parts = cls._content_to_parts(msg.get("content"))
                if parts:
                    contents.append(types.Content(role="user", parts=parts))
                continue

            if role == "assistant":
                tool_calls = msg.get("tool_calls")
                parts: list[types.Part] = []
                # 先插入 pending_signatures 对应的前置 thought Parts，
                # 让 Gemini 保持推理上下文连续性
                for sig in msg.get("pending_signatures") or []:
                    if isinstance(sig, (bytes, bytearray)):
                        parts.append(types.Part(thought_signature=bytes(sig)))
                text = str(msg.get("content") or "").strip()
                if text:
                    parts.append(types.Part(text=text))
                if isinstance(tool_calls, list):
                    for tc in tool_calls:
                        fn = tc.get("function") or {}
                        raw_args = fn.get("arguments") or "{}"
                        if isinstance(raw_args, str):
                            try:
                                args = json.loads(raw_args)
                            except Exception:
                                args = {}
                        else:
                            args = raw_args
                        signature = tc.get("thought_signature")
                        name_for_log = str(fn.get("name") or "unknown_tool")
                        call_id = str(tc.get("id") or uuid.uuid4().hex)
                        # 记录 id → name，给后续 tool 角色消息反查用
                        call_id_to_name[call_id] = name_for_log
                        part_kwargs: dict[str, Any] = {
                            "function_call": types.FunctionCall(
                                id=call_id,
                                name=name_for_log,
                                args=args if isinstance(args, dict) else {},
                            ),
                        }
                        if isinstance(signature, (bytes, bytearray)):
                            part_kwargs["thought_signature"] = bytes(signature)
                        else:
                            logger.warning(
                                "GeminiLLM rebuild: function_call {} has no thought_signature "
                                "in history — Gemini will reject this request",
                                name_for_log,
                            )
                        parts.append(types.Part(**part_kwargs))
                if parts:
                    contents.append(types.Content(role="model", parts=parts))
                continue

            if role == "tool":
                result = msg.get("content") or ""
                if not isinstance(result, str):
                    result = json.dumps(result, ensure_ascii=False)
                tool_call_id = str(msg.get("tool_call_id") or "")
                # name 解析优先级：显式字段 > id 反查 > unknown_tool 兜底。
                # 兜底值不再用 "tool" —— 那只是历史遗留写法，Gemini 几乎必然拒绝；
                # "unknown_tool" 至少让问题在日志里可见。
                explicit_name = str(msg.get("name") or "").strip()
                resolved_name = (
                    explicit_name
                    or call_id_to_name.get(tool_call_id)
                    or "unknown_tool"
                )
                if resolved_name == "unknown_tool":
                    logger.warning(
                        "GeminiLLM: tool result tool_call_id={} 无法反查到对应 function_call 的 name，"
                        "Gemini 可能拒绝此请求",
                        tool_call_id,
                    )
                # FunctionResponse 要求 response 是 dict
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part(
                        function_response=types.FunctionResponse(
                            id=tool_call_id,
                            name=resolved_name,
                            response={"output": result},
                        )
                    )],
                ))
                continue

        return system_instruction, contents

    @staticmethod
    def _content_to_parts(content: Any) -> list[types.Part]:
        """将 OpenAI content 字段转换为 SDK Part 列表。"""
        if isinstance(content, str):
            return [types.Part(text=content)] if content else []
        if isinstance(content, dict):
            content = [content]
        if not isinstance(content, list):
            return [types.Part(text=json.dumps(content, ensure_ascii=False))]

        parts: list[types.Part] = []
        for item in content:
            if not isinstance(item, dict):
                text = str(item).strip()
                if text:
                    parts.append(types.Part(text=text))
                continue

            item_type = item.get("type")
            if item_type == "text":
                text = str(item.get("text") or "").strip()
                if text:
                    parts.append(types.Part(text=text))
            elif item_type == "image_url":
                image = item.get("image_url") or {}
                url = str(image.get("url") or "")
                if url.startswith("data:"):
                    # data URI → inline_data
                    header, _, data_b64 = url.partition(",")
                    mime_type = header.removeprefix("data:").split(";")[0]
                    import base64
                    parts.append(types.Part(
                        inline_data=types.Blob(
                            mime_type=mime_type,
                            data=base64.b64decode(data_b64),
                        )
                    ))
                elif url:
                    parts.append(types.Part(
                        file_data=types.FileData(file_uri=url)
                    ))
            else:
                parts.append(types.Part(text=json.dumps(item, ensure_ascii=False)))
        return parts

    @staticmethod
    def _convert_tools(tools: list[dict] | None) -> list[types.Tool] | None:
        """将 OpenAI 工具 schema 转换为 SDK Tool 对象。"""
        if not tools:
            return None
        declarations = []
        for tool in tools:
            fn = tool.get("function") or {}
            declarations.append(types.FunctionDeclaration(
                name=fn.get("name"),
                description=fn.get("description"),
                parameters_json_schema=fn.get("parameters") or {},
            ))
        return [types.Tool(function_declarations=declarations)]
