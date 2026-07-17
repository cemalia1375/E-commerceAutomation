"""FlowCut OSS 客户端 — 封装 Volcengine TOS SDK。"""

from __future__ import annotations

import logging
import time

import tos
from tos.enum import HttpMethodType
from tos.models2 import CORSRule

from Flowcut.config import make_oss_config

logger = logging.getLogger(__name__)


def _content_disposition_attachment(filename: str) -> str:
    """构造 RFC 5987 的 attachment Content-Disposition，兼容非 ASCII 文件名。"""
    from urllib.parse import quote

    safe = filename.replace('"', "").replace("\r", "").replace("\n", "")
    ascii_fallback = safe.encode("ascii", "ignore").decode("ascii") or "download"
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(safe, safe='')}"


class OSSClient:
    """封装 tos.TosClientV2，提供 FlowCut 业务所需的对象存储操作。"""

    def __init__(
        self,
        endpoint: str,
        ak: str,
        sk: str,
        bucket: str,
        region: str,
    ) -> None:
        self._bucket = bucket
        self._endpoint = endpoint
        self._configured = bool(endpoint and ak and sk and bucket and region)
        if self._configured:
            self._client = tos.TosClientV2(
                ak=ak,
                sk=sk,
                endpoint=endpoint,
                region=region,
            )
        else:
            self._client = None
            logger.warning(
                "OSSClient: OSS 配置不完整，所有操作将返回空结果。"
                " 请设置环境变量 FLOWCUT_OSS_ENDPOINT / FLOWCUT_OSS_ACCESS_KEY_ID / "
                "FLOWCUT_OSS_ACCESS_KEY_SECRET / FLOWCUT_OSS_BUCKET / FLOWCUT_OSS_REGION"
            )

    def presigned_put_url(self, key: str, expires: int = 3600) -> str:
        """生成前端直传用的预签名 PUT URL。"""
        if not self._configured:
            return ""
        result = self._client.pre_signed_url(
            http_method=HttpMethodType.Http_Method_Put,
            bucket=self._bucket,
            key=key,
            expires=expires,
        )
        return result.signed_url if hasattr(result, "signed_url") else str(result)

    def presigned_get_url(
        self, key: str, expires: int = 3600, *, disposition_filename: str | None = None,
    ) -> str:
        """生成下载/预览用的预签名 GET URL。

        disposition_filename 非空时附带 response-content-disposition=attachment，
        浏览器导航打开即按该文件名下载（绕开 fetch/CORS）。预览用 URL 不要传，
        否则 <video> 会被强制下载而不是播放。
        """
        if not self._configured:
            return ""
        query = (
            {"response-content-disposition": _content_disposition_attachment(disposition_filename)}
            if disposition_filename
            else None
        )
        result = self._client.pre_signed_url(
            http_method=HttpMethodType.Http_Method_Get,
            bucket=self._bucket,
            key=key,
            expires=expires,
            query=query,
        )
        return result.signed_url if hasattr(result, "signed_url") else str(result)

    def get_public_url(self, key: str) -> str:
        """构造公开访问 URL（仅当 bucket 开启 public-read ACL 时可用）。"""
        if not self._configured:
            return ""
        return f"https://{self._bucket}.{self._endpoint}/{key}"

    def upload(self, local_path: str, key: str) -> None:
        """上传本地文件到 OSS。"""
        if not self._configured:
            logger.warning("OSSClient: 跳过上传操作（OSS 未配置） key=%s", key)
            return
        self._client.put_object_from_file(
            bucket=self._bucket,
            key=key,
            file_path=local_path,
        )

    def download(self, key: str, local_path: str) -> None:
        """从 OSS 下载对象到本地文件（使用 TOS SDK，不走预签名 URL）。"""
        if not self._configured:
            raise RuntimeError("OSS not configured, cannot download")
        self._client.get_object_to_file(
            bucket=self._bucket,
            key=key,
            file_path=local_path,
        )

    def ensure_cors(self, allowed_origins: list[str] | None = None) -> None:
        """在 bucket 上写入 CORS 规则，允许浏览器直传。启动时调用一次即可。"""
        if not self._configured:
            return
        origins = allowed_origins or ["*"]
        try:
            self._client.put_bucket_cors(
                bucket=self._bucket,
                cors_rule=[
                    CORSRule(
                        allowed_origins=origins,
                        allowed_methods=["GET", "PUT", "POST", "DELETE", "HEAD"],
                        allowed_headers=["*"],
                        expose_headers=["ETag", "x-tos-request-id"],
                        max_age_seconds=3600,
                    )
                ],
            )
            logger.info("OSSClient: bucket CORS 规则已更新 origins=%s", origins)
        except Exception as exc:
            logger.warning("OSSClient: 设置 CORS 失败（不影响服务启动）: %s", exc)

    def delete_object(self, key: str) -> None:
        """从 bucket 中删除一个对象。"""
        if not self._configured:
            logger.warning("OSSClient: 跳过删除操作（OSS 未配置） key=%s", key)
            return
        self._client.delete_object(bucket=self._bucket, key=key)

    def object_exists(self, key: str) -> bool:
        """判断对象是否存在。OSS 未配置时抛错（审计场景必须强配置）。"""
        if not self._configured:
            raise RuntimeError("OSS not configured, cannot probe object existence")
        return self._client.does_object_exist(bucket=self._bucket, key=key)


class OssUrlCache:
    """进程内 TTL 缓存，用于复用已生成的预签名 OSS GET URL。

    presigned_get_url() 每次调用会产生不同的签名字符串，导致前端
    <video>/<img> 的 src 属性变化 → 浏览器视为新资源重新请求。
    此缓存在 URL 有效期内（默认 50 分钟，留 10 分钟安全余量）返回
    相同的 URL，消除视频闪烁和重复加载。
    """

    def __init__(self, ttl_seconds: int = 3000) -> None:
        self._store: dict[str, tuple[str, float]] = {}
        self._ttl = ttl_seconds

    def get(self, key: str) -> str | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        url, ts = entry
        if time.monotonic() - ts > self._ttl:
            del self._store[key]
            return None
        return url

    def set(self, key: str, url: str) -> None:
        self._store[key] = (url, time.monotonic())

    def get_or_set(self, key: str, factory) -> str:
        cached = self.get(key)
        if cached is not None:
            return cached
        url = factory()
        self.set(key, url)
        return url


_url_cache: OssUrlCache | None = None


def get_url_cache() -> OssUrlCache:
    """返回进程级单例 OssUrlCache。"""
    global _url_cache
    if _url_cache is None:
        _url_cache = OssUrlCache()
    return _url_cache


def build_oss_client() -> OSSClient:
    """从环境变量读取 OSS 配置并构造 OSSClient。"""
    cfg = make_oss_config()
    return OSSClient(
        endpoint=cfg["endpoint"],
        ak=cfg["access_key_id"],
        sk=cfg["access_key_secret"],
        bucket=cfg["bucket"],
        region=cfg["region"],
    )
