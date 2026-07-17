"""POST /materials/upload + /materials/upload-token 后缀白名单测试。

约束：
- 仅接受 视频 / 音频 / 图片 后缀
- .zip 必须 415 并指向 /materials/upload-zip
- 其它未知后缀 415
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from Flowcut.api.server import app
    with TestClient(app) as c:
        yield c


@pytest.mark.integration
def test_upload_rejects_zip_with_hint(client: TestClient):
    files = {"file": ("foo.zip", io.BytesIO(b"PK\x03\x04"), "application/zip")}
    data = {"tenant_key": "t_reject_zip", "product": "p"}
    r = client.post("/materials/upload", files=files, data=data)
    assert r.status_code == 415, r.text
    assert "upload-zip" in r.text


@pytest.mark.integration
def test_upload_rejects_unknown_extension(client: TestClient):
    files = {"file": ("foo.exe", io.BytesIO(b"\x00\x00"), "application/octet-stream")}
    data = {"tenant_key": "t_reject_exe", "product": "p"}
    r = client.post("/materials/upload", files=files, data=data)
    assert r.status_code == 415


@pytest.mark.integration
def test_upload_token_rejects_zip(client: TestClient):
    body = {"tenant_key": "t_reject_zip2", "filename": "foo.zip", "product": "p"}
    r = client.post("/materials/upload-token", json=body)
    assert r.status_code == 415
    assert "upload-zip" in r.text


@pytest.mark.integration
def test_upload_token_rejects_unknown_extension(client: TestClient):
    body = {"tenant_key": "t_reject_exe2", "filename": "x.bin", "product": "p"}
    r = client.post("/materials/upload-token", json=body)
    assert r.status_code == 415
