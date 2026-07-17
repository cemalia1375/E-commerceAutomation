"""Douyin short-link resolver + video info fetcher + downloader."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

DOUYIN_SHARE_HOST = "v.douyin.com"
BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Referer": "https://www.douyin.com/",
}


@dataclass(frozen=True)
class DouyinVideoInfo:
    aweme_id: str
    title: str
    duration_ms: int
    play_url: str  # watermark-free
    cover_url: str


class DouyinClient:
    def __init__(self, http_client: httpx.AsyncClient | None = None) -> None:
        self._http = http_client

    async def _client(self) -> httpx.AsyncClient:
        if self._http is not None:
            return self._http
        return httpx.AsyncClient(
            timeout=httpx.Timeout(60, connect=15),
            follow_redirects=False,
            headers=BASE_HEADERS,
        )

    async def resolve_short_link(self, share_url: str) -> str:
        """Follow v.douyin.com redirect to obtain the real douyin URL."""
        parsed = urlparse(share_url)
        if DOUYIN_SHARE_HOST not in (parsed.hostname or ""):
            raise ValueError(f"不是抖音分享链接: {share_url}")

        client = await self._client() if self._http else httpx.AsyncClient(
            timeout=httpx.Timeout(30, connect=15),
            follow_redirects=False,
            headers=BASE_HEADERS,
        )
        try:
            resp = await client.get(share_url)
            resp.raise_for_status()
        finally:
            if self._http is None:
                await client.aclose()

        location = resp.headers.get("Location") or resp.headers.get("location", "")
        if not location:
            raise ValueError("短链重定向失败：未获取到真实 URL")
        return location

    @staticmethod
    def extract_aweme_id(real_url: str) -> str:
        """Parse aweme_id from a douyin real URL.

        Handles patterns like:
          - /video/7412345678901234567
          - /note/7412345678901234567
          - ?modal_id=7412345678901234567
        """
        # Try path-based extraction first
        m = re.search(r"/(video|note)/(\d+)", real_url)
        if m:
            return m.group(2)

        # Try query param
        parsed = urlparse(real_url)
        from urllib.parse import parse_qs
        params = parse_qs(parsed.query)
        modal_id = params.get("modal_id")
        if modal_id:
            return modal_id[0]

        raise ValueError(f"无法从 URL 提取视频 ID: {real_url}")

    async def get_video_info(self, aweme_id: str) -> DouyinVideoInfo:
        """Fetch video details from the douyin public web API."""
        api_url = "https://www.douyin.com/aweme/v1/web/aweme/detail/"
        client = await self._client() if self._http else httpx.AsyncClient(
            timeout=httpx.Timeout(30, connect=15),
            follow_redirects=True,
            headers={**BASE_HEADERS, "Accept": "application/json"},
        )
        try:
            resp = await client.get(api_url, params={"aweme_id": aweme_id})
            resp.raise_for_status()
            data: dict[str, Any] = resp.json()
        finally:
            if self._http is None:
                await client.aclose()

        aweme = data.get("aweme_detail") or {}
        if not aweme:
            raise ValueError(f"抖音 API 未返回视频数据 (aweme_id={aweme_id})")

        video = aweme.get("video") or {}
        play_addr = video.get("play_addr") or {}
        play_urls: list[str] = play_addr.get("url_list", [])
        if not play_urls:
            raise ValueError("未找到无水印视频链接")

        # Replace playwm → play to ensure watermark-free
        play_url = play_urls[0].replace("playwm", "play")

        cover = video.get("cover") or {}
        cover_urls: list[str] = cover.get("url_list", [])
        cover_url = cover_urls[0] if cover_urls else ""

        title = str(aweme.get("desc") or "")
        duration_ms = int(video.get("duration", 0))

        return DouyinVideoInfo(
            aweme_id=aweme_id,
            title=title,
            duration_ms=duration_ms,
            play_url=play_url,
            cover_url=cover_url,
        )

    async def download_video(self, url: str, dest: str | Path) -> None:
        """Stream-download video from url to dest path."""
        dest = Path(dest)
        client = await self._client() if self._http else httpx.AsyncClient(
            timeout=httpx.Timeout(300, connect=15),
            follow_redirects=True,
            headers=BASE_HEADERS,
        )
        try:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                with open(dest, "wb") as f:
                    async for chunk in resp.aiter_bytes():
                        f.write(chunk)
        finally:
            if self._http is None:
                await client.aclose()
