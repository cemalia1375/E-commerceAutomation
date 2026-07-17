"""/admin/lab 回填用的火山 TOS 直传封装。

tos SDK 是同步阻塞的，统一用 asyncio.to_thread 包装；SDK 延迟 import，
未安装 tos 依赖时不影响主服务启动，只在使用 lab 回填时报错。
"""

from __future__ import annotations

import asyncio
from pathlib import PurePosixPath

_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


class TosUploaderError(RuntimeError):
    """TOS 配置缺失或上传失败（消息为中文，前端可直接展示）。"""


class TosUploader:
    """上传照片字节到 TOS，返回公网 URL。

    key 形如 test-photos/{user_id}_{YYYYMMDD}_{原文件名}，与
    script/probe_lifecycle.py 现有测试照片同前缀。bucket 当前可匿名读，
    默认拼公网 URL；ACL 收紧时可改用 presign=True 生成带签名 URL。
    """

    def __init__(self, config: dict[str, str], *, presign: bool = False) -> None:
        self._access_key = config.get("access_key", "")
        self._secret_key = config.get("secret_key", "")
        self._bucket = config.get("bucket", "")
        self._region = config.get("region", "")
        self._endpoint = config.get("endpoint", "")
        self._presign = presign
        self._client = None

    @property
    def configured(self) -> bool:
        return bool(self._access_key and self._secret_key and self._bucket and self._endpoint)

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        if not self.configured:
            raise TosUploaderError(
                "TOS 未配置：请在 .env 中填写 TOS_ACCESS_KEY / TOS_SECRET_KEY"
            )
        try:
            import tos
        except ImportError as exc:
            raise TosUploaderError(
                "未安装 tos SDK：请先 pip install -r requirements.txt"
            ) from exc
        self._client = tos.TosClientV2(
            self._access_key,
            self._secret_key,
            self._endpoint,
            self._region,
        )
        return self._client

    async def upload(self, *, key: str, data: bytes) -> str:
        """上传字节到 TOS，返回可访问 URL。失败抛 TosUploaderError。"""
        client = self._ensure_client()
        suffix = PurePosixPath(key).suffix.lower()
        content_type = _CONTENT_TYPES.get(suffix, "application/octet-stream")
        try:
            await asyncio.to_thread(
                client.put_object,
                self._bucket,
                key,
                content=data,
                content_type=content_type,
            )
        except Exception as exc:
            raise TosUploaderError(f"TOS 上传失败（{key}）：{exc}") from exc

        if self._presign:
            return await asyncio.to_thread(self._presigned_url, key)
        return f"https://{self._bucket}.{self._endpoint}/{key}"

    def _presigned_url(self, key: str) -> str:
        import tos

        client = self._ensure_client()
        out = client.pre_signed_url(
            tos.HttpMethodType.Http_Method_Get,
            self._bucket,
            key,
            expires=7 * 24 * 3600,
        )
        return str(out.signed_url)


def make_photo_key(*, user_id: str, day: str, filename: str) -> str:
    """生成 TOS object key：test-photos/{user_id}_{day}_{安全文件名}。"""
    safe_name = PurePosixPath(filename).name.replace(" ", "_")
    return f"test-photos/{user_id}_{day}_{safe_name}"
