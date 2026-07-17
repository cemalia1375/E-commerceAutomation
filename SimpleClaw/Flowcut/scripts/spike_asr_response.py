"""ASR 探针：dump 一次完整 ASR 返回结构，确认词级时间戳是否存在。

用法:
    uv run python -m Flowcut.scripts.spike_asr_response <wav_file> > /tmp/asr_dump.json

测试不同 ASR config flag 组合（show_utterances / enable_word_time_offset / result_type）
以确定豆包 bigmodel SAUC 接口能否返回词级时间戳。
"""
from __future__ import annotations

import asyncio
import json
import os
import struct
import sys
import uuid
from typing import Any

import aiohttp
from dotenv import load_dotenv

_ASR_WS_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"
_ASR_RESOURCE_ID = "volc.bigasr.sauc.duration"
_ASR_CHUNK_BYTES = 5120  # 160ms @ 16kHz 16-bit mono


def _ws_frame(msg_type: int, payload: bytes, *, json_serial: bool = False) -> bytes:
    serial_byte = 0x10 if json_serial else 0x00
    header = struct.pack(">BBBB", 0x11, msg_type, serial_byte, 0x00)
    return header + struct.pack(">I", len(payload)) + payload


async def probe(wav_path: str, request_extras: dict[str, Any]) -> list[dict[str, Any]]:
    """跑一次 ASR 并收集所有返回 JSON。"""
    app_key = os.environ["FLOWCUT_ASR_APP_ID"]
    access_key = os.environ["FLOWCUT_ASR_ACCESS_KEY_ID"]

    with open(wav_path, "rb") as f:
        f.seek(44)
        pcm_data = f.read()

    headers = {
        "X-Api-App-Key": app_key,
        "X-Api-Access-Key": access_key,
        "X-Api-Resource-Id": _ASR_RESOURCE_ID,
        "X-Api-Connect-Id": uuid.uuid4().hex,
    }
    request_body: dict[str, Any] = {
        "model_name": "bigmodel",
        "enable_punc": True,
        "enable_itn": True,
    }
    request_body.update(request_extras)
    config_payload = json.dumps({
        "user": {"uid": "flowcut-spike"},
        "audio": {"format": "pcm", "rate": 16000, "bits": 16, "channel": 1, "codec": "raw"},
        "request": request_body,
    }).encode()

    responses: list[dict[str, Any]] = []

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(_ASR_WS_URL, headers=headers) as ws:
            await ws.send_bytes(_ws_frame(0x10, config_payload, json_serial=True))

            offset = 0
            while offset < len(pcm_data):
                chunk = pcm_data[offset: offset + _ASR_CHUNK_BYTES]
                offset += _ASR_CHUNK_BYTES
                is_last = offset >= len(pcm_data)
                msg_type = 0x22 if is_last else 0x20
                await ws.send_bytes(_ws_frame(msg_type, chunk))
                await asyncio.sleep(0.16)

            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    raw = msg.data
                    start = raw.find(b"{")
                    if start == -1:
                        continue
                    try:
                        obj = json.loads(raw[start:])
                    except Exception:
                        continue
                    responses.append(obj)
                    result = obj.get("result") or {}
                    if result.get("is_final"):
                        break
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break

    return responses


async def main() -> None:
    load_dotenv()
    if len(sys.argv) < 2:
        print("用法: python -m Flowcut.scripts.spike_asr_response <wav_file>", file=sys.stderr)
        sys.exit(1)
    wav_path = sys.argv[1]

    # 尝试多组 config flag，看哪种能让接口返回词级时间戳
    trials = [
        {"label": "baseline", "extras": {}},
        {"label": "show_utterances", "extras": {"show_utterances": True}},
        {"label": "result_type_full", "extras": {"result_type": "full"}},
        {
            "label": "show_utterances+result_type_full",
            "extras": {"show_utterances": True, "result_type": "full"},
        },
    ]

    dump: dict[str, Any] = {}
    for trial in trials:
        print(f"[spike] trying {trial['label']} ...", file=sys.stderr)
        try:
            responses = await probe(wav_path, trial["extras"])
            dump[trial["label"]] = {
                "extras": trial["extras"],
                "responses": responses,
            }
        except Exception as exc:
            dump[trial["label"]] = {
                "extras": trial["extras"],
                "error": repr(exc),
            }

    json.dump(dump, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    asyncio.run(main())
