from __future__ import annotations

import argparse
import asyncio
import base64
import json
import mimetypes
import os
import statistics
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx


DEFAULT_BASE_URL = "https://api.xmsmartlink.cn"
DEFAULT_TOKEN_URL = "https://api.xmsmartlink.cn/console/token"
DEFAULT_GEMINI_MODEL = "gemini-3.1-flash-lite-preview"
DEFAULT_OPENAI_MODEL = "gemini-3.1-flash-lite-preview"

TEXT_PROMPT = (
    "Reply with a short JSON object: "
    '{"ok": true, "purpose": "xmsmartlink connectivity smoke test"}.'
)

VIDEO_PROMPT = """\
Please analyze this e-commerce short video. Return strict JSON only:
{
  "summary": "one sentence",
  "segments": [
    {
      "start_time": 0,
      "end_time": 3,
      "visual": "pure visual description",
      "copy": "spoken words, or empty string",
      "category": "真人口播 or 产品展示"
    }
  ]
}
Keep the response compact but complete.
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 12:
        return "***"
    return f"{value[:6]}...{value[-4:]}"


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = int(round((len(ordered) - 1) * p))
    return ordered[max(0, min(idx, len(ordered) - 1))]


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def read_video_part(path: Path, max_inline_mb: float) -> dict[str, Any]:
    size_mb = path.stat().st_size / 1_000_000
    if size_mb > max_inline_mb:
        raise ValueError(
            f"{path} is {size_mb:.1f} MB, larger than --max-inline-mb={max_inline_mb}. "
            "Compress it first or raise the limit deliberately."
        )
    mime_type = mimetypes.guess_type(path.name)[0] or "video/mp4"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return {
        "mime_type": mime_type,
        "data": encoded,
        "size_mb": round(size_mb, 3),
    }


def ffmpeg_compress(src: Path, dst_dir: Path, width: int, crf: str, fps: str) -> Path:
    out = dst_dir / f"{src.stem}.bench.mp4"
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(src),
        "-vf",
        f"scale={width}:-2,fps={fps}",
        "-c:v",
        "libx264",
        "-crf",
        crf,
        "-preset",
        "ultrafast",
        "-an",
        str(out),
    ]
    subprocess.run(cmd, check=True)
    return out


def build_gemini_url(args: argparse.Namespace) -> str:
    base = args.base_url.rstrip("/")
    path = args.gemini_path.format(model=args.model)
    url = f"{base}{path}"
    if args.key_placement == "query":
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{urlencode({'key': args.api_key})}"
    return url


def build_openai_url(args: argparse.Namespace) -> str:
    return f"{args.base_url.rstrip('/')}{args.openai_path}"


def build_headers(args: argparse.Namespace) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "flowcut-xmsmartlink-video-bench/1.0",
    }
    if args.key_placement == "bearer":
        headers["Authorization"] = f"Bearer {args.api_key}"
    elif args.key_placement == "x-goog-api-key":
        headers["x-goog-api-key"] = args.api_key
    return headers


def build_gemini_payload(video: dict[str, Any] | None, prompt: str) -> dict[str, Any]:
    parts: list[dict[str, Any]] = []
    if video is not None:
        parts.append({"text": "[video: primary test sample]"})
        parts.append({
            "inline_data": {
                "mime_type": video["mime_type"],
                "data": video["data"],
            }
        })
    parts.append({"text": prompt})
    return {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 4096,
        },
    }


def build_openai_payload(args: argparse.Namespace, video: dict[str, Any] | None, prompt: str) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    if video is not None:
        data_url = f"data:{video['mime_type']};base64,{video['data']}"
        content.append({
            "type": "video_url",
            "video_url": {"url": data_url},
        })
    return {
        "model": args.model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.2,
        "max_tokens": 4096,
    }


def extract_text(mode: str, payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    if mode == "gemini":
        candidates = payload.get("candidates") or []
        if not candidates:
            return ""
        content = candidates[0].get("content") or {}
        parts = content.get("parts") or []
        return "\n".join(str(part.get("text") or "") for part in parts if isinstance(part, dict))
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content") or ""
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False)


def classify_error(status_code: int | None, exc: BaseException | None, body: str) -> str | None:
    if exc is not None:
        if isinstance(exc, httpx.TimeoutException):
            return "timeout"
        if isinstance(exc, httpx.ConnectError):
            return "connect_error"
        return type(exc).__name__
    if status_code is None or 200 <= status_code < 300:
        return None
    if status_code == 429:
        return "rate_limited_429"
    if status_code >= 500:
        return f"server_{status_code}"
    if status_code in (401, 403):
        return f"auth_{status_code}"
    if status_code == 413:
        return "request_too_large_413"
    if "quota" in body.lower():
        return "quota"
    return f"http_{status_code}"


@dataclass(frozen=True)
class Case:
    name: str
    video_path: Path | None
    prompt: str


async def run_one(
    client: httpx.AsyncClient,
    args: argparse.Namespace,
    case: Case,
    video: dict[str, Any] | None,
    attempt: int,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    async with semaphore:
        url = build_gemini_url(args) if args.mode == "gemini" else build_openai_url(args)
        payload = (
            build_gemini_payload(video, case.prompt)
            if args.mode == "gemini"
            else build_openai_payload(args, video, case.prompt)
        )
        started = time.perf_counter()
        status_code: int | None = None
        body_text = ""
        response_json: Any = None
        exc: BaseException | None = None
        try:
            response = await client.post(url, headers=build_headers(args), json=payload)
            status_code = response.status_code
            body_text = response.text[:2000]
            try:
                response_json = response.json()
            except ValueError:
                response_json = None
            response.raise_for_status()
        except BaseException as error:  # noqa: BLE001 - benchmark logs exact error class
            exc = error

        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        text = extract_text(args.mode, response_json)
        ok = exc is None and status_code is not None and 200 <= status_code < 300 and bool(text)
        return {
            "created_at": utc_now(),
            "case": case.name,
            "attempt": attempt,
            "mode": args.mode,
            "base_host": urlparse(args.base_url).netloc,
            "model": args.model,
            "video": str(case.video_path) if case.video_path else None,
            "video_size_mb": video.get("size_mb") if video else None,
            "status_code": status_code,
            "ok": ok,
            "latency_ms": latency_ms,
            "response_chars": len(text),
            "error_type": classify_error(status_code, exc, body_text),
            "error_message": str(exc)[:500] if exc else None,
            "response_preview": text[:500],
            "http_body_preview": body_text[:500] if not ok else None,
        }


async def probe_url(args: argparse.Namespace) -> dict[str, Any]:
    if not args.probe_token_url:
        return {}
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=args.timeout) as client:
        try:
            response = await client.get(args.probe_token_url)
            return {
                "url": args.probe_token_url,
                "status_code": response.status_code,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "body_preview": response.text[:300],
            }
        except BaseException as exc:  # noqa: BLE001
            return {
                "url": args.probe_token_url,
                "status_code": None,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                "error": str(exc)[:300],
            }


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = [float(r["latency_ms"]) for r in records if r.get("ok")]
    errors: dict[str, int] = {}
    by_case: dict[str, dict[str, int]] = {}
    for record in records:
        name = str(record["case"])
        stats = by_case.setdefault(name, {"total": 0, "ok": 0, "failed": 0})
        stats["total"] += 1
        if record.get("ok"):
            stats["ok"] += 1
        else:
            stats["failed"] += 1
            err = str(record.get("error_type") or "unknown")
            errors[err] = errors.get(err, 0) + 1
    total = len(records)
    ok_count = sum(1 for r in records if r.get("ok"))
    return {
        "total": total,
        "ok": ok_count,
        "failed": total - ok_count,
        "success_rate": round(ok_count / total * 100, 2) if total else 0,
        "latency_ms": {
            "avg": round(statistics.mean(latencies), 2) if latencies else None,
            "p50": percentile(latencies, 0.50),
            "p90": percentile(latencies, 0.90),
            "p95": percentile(latencies, 0.95),
            "p99": percentile(latencies, 0.99),
            "max": max(latencies) if latencies else None,
        },
        "errors": errors,
        "by_case": by_case,
    }


def write_report(
    path: Path,
    args: argparse.Namespace,
    records: list[dict[str, Any]],
    probe: dict[str, Any],
) -> None:
    summary = summarize(records)
    lines = [
        "# XMSmartLink Video Benchmark Report",
        "",
        f"- Time: {utc_now()}",
        f"- Mode: `{args.mode}`",
        f"- Base URL: `{args.base_url}`",
        f"- Model: `{args.model}`",
        f"- API key: `{redact(args.api_key)}`",
        f"- Concurrency: `{args.concurrency}`",
        f"- Repeat: `{args.repeat}`",
        f"- Timeout: `{args.timeout}s`",
        "",
        "## Summary",
        "",
        f"- Total: {summary['total']}",
        f"- OK: {summary['ok']}",
        f"- Failed: {summary['failed']}",
        f"- Success rate: {summary['success_rate']}%",
        f"- Latency ms: `{json.dumps(summary['latency_ms'], ensure_ascii=False)}`",
        f"- Errors: `{json.dumps(summary['errors'], ensure_ascii=False)}`",
        "",
        "## Cases",
        "",
    ]
    for name, stats in summary["by_case"].items():
        lines.append(f"- `{name}`: total={stats['total']}, ok={stats['ok']}, failed={stats['failed']}")
    if probe:
        lines.extend([
            "",
            "## Token URL Probe",
            "",
            f"```json\n{json.dumps(probe, ensure_ascii=False, indent=2)}\n```",
        ])
    failures = [record for record in records if not record.get("ok")]
    if failures:
        lines.extend(["", "## Failure Samples", ""])
        for record in failures[:10]:
            sample = {
                "case": record.get("case"),
                "attempt": record.get("attempt"),
                "status_code": record.get("status_code"),
                "error_type": record.get("error_type"),
                "error_message": record.get("error_message"),
                "http_body_preview": record.get("http_body_preview"),
            }
            lines.append(f"```json\n{json.dumps(sample, ensure_ascii=False, indent=2)}\n```")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark XMSmartLink video handling for Flowcut/Gemini replacement."
    )
    parser.add_argument("--mode", choices=("gemini", "openai"), default="gemini")
    parser.add_argument("--base-url", default=os.getenv("XMSMARTLINK_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--api-key", default=os.getenv("XMSMARTLINK_API_KEY", ""))
    parser.add_argument("--model", default=os.getenv("XMSMARTLINK_MODEL", DEFAULT_GEMINI_MODEL))
    parser.add_argument("--key-placement", choices=("query", "bearer", "x-goog-api-key"), default="query")
    parser.add_argument("--gemini-path", default="/v1beta/models/{model}:generateContent")
    parser.add_argument("--openai-path", default="/v1/chat/completions")
    parser.add_argument("--video", action="append", default=[], help="Video file path. Can be repeated.")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--max-inline-mb", type=float, default=8.0)
    parser.add_argument("--compress", action="store_true", help="Compress videos with ffmpeg before inline upload.")
    parser.add_argument("--compress-width", type=int, default=640)
    parser.add_argument("--compress-crf", default="34")
    parser.add_argument("--compress-fps", default="8")
    parser.add_argument("--include-text-smoke", action="store_true")
    parser.add_argument("--probe-token-url", default=os.getenv("XMSMARTLINK_TOKEN_URL", DEFAULT_TOKEN_URL))
    parser.add_argument("--out-dir", default="reports/xmsmartlink")
    args = parser.parse_args()
    if not args.api_key:
        raise SystemExit("Missing API key. Set XMSMARTLINK_API_KEY or pass --api-key.")
    if args.mode == "openai" and args.model == DEFAULT_GEMINI_MODEL:
        args.model = os.getenv("XMSMARTLINK_MODEL", DEFAULT_OPENAI_MODEL)
    if args.repeat < 1:
        raise SystemExit("--repeat must be >= 1")
    if args.concurrency < 1:
        raise SystemExit("--concurrency must be >= 1")
    return args


async def main_async() -> None:
    args = parse_args()
    out_dir = Path(args.out_dir)
    ensure_dir(out_dir)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    jsonl_path = out_dir / f"xmsmartlink_video_{run_id}.jsonl"
    report_path = out_dir / f"xmsmartlink_video_{run_id}.md"

    temp_dir_ctx = tempfile.TemporaryDirectory(prefix="xmsmartlink_bench_") if args.compress else None
    temp_dir = Path(temp_dir_ctx.name) if temp_dir_ctx else None
    try:
        cases: list[Case] = []
        if args.include_text_smoke:
            cases.append(Case("text_smoke", None, TEXT_PROMPT))
        for raw_path in args.video:
            path = Path(raw_path).expanduser().resolve()
            if not path.exists():
                raise SystemExit(f"Video not found: {path}")
            final_path = path
            if args.compress:
                assert temp_dir is not None
                final_path = ffmpeg_compress(
                    path,
                    temp_dir,
                    width=args.compress_width,
                    crf=args.compress_crf,
                    fps=args.compress_fps,
                )
            cases.append(Case(f"video_{path.stem}", final_path, VIDEO_PROMPT))
        if not cases:
            raise SystemExit("No cases selected. Pass --include-text-smoke and/or --video PATH.")

        video_cache: dict[Path, dict[str, Any]] = {}
        for case in cases:
            if case.video_path is not None:
                video_cache[case.video_path] = read_video_part(case.video_path, args.max_inline_mb)

        probe = await probe_url(args)
        records: list[dict[str, Any]] = []
        semaphore = asyncio.Semaphore(args.concurrency)
        async with httpx.AsyncClient(timeout=args.timeout) as client:
            tasks = []
            for case in cases:
                video = video_cache.get(case.video_path) if case.video_path else None
                for attempt in range(1, args.repeat + 1):
                    tasks.append(run_one(client, args, case, video, attempt, semaphore))
            for completed in asyncio.as_completed(tasks):
                record = await completed
                records.append(record)
                with jsonl_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                state = "OK" if record["ok"] else "FAIL"
                print(
                    f"[{state}] case={record['case']} attempt={record['attempt']} "
                    f"status={record['status_code']} latency_ms={record['latency_ms']} "
                    f"error={record['error_type']}"
                )

        write_report(report_path, args, records, probe)
        print(f"\nJSONL: {jsonl_path}")
        print(f"Report: {report_path}")
        print(json.dumps(summarize(records), ensure_ascii=False, indent=2))
    finally:
        if temp_dir_ctx is not None:
            temp_dir_ctx.cleanup()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
