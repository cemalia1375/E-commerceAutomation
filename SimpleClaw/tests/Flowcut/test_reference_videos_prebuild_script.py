"""POST /reference-videos 同步预建 fc_script(PROCESSING) 集成测试。

验证：
- POST /upload 时立即预建一条 fc_script(status=PROCESSING, segments=[])
- 响应携带 script_id；fc_reference_video.script_id 已回填
- POST /{id}/decompose 若 ref_video 已有 script_id，则复用，不重复建
- POST /{id}/decompose 若 ref_video 没有 script_id（老数据），则按需补建
"""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_client(monkeypatch):
    """启动 FlowCut app 并 mock 掉真实 OSS 上传 / submit_task。"""
    # mock OSS upload —— 不真传字节
    from Flowcut.storage import oss_client as oss_mod

    def _noop_upload(self, local_path: str, key: str) -> None:
        return None

    monkeypatch.setattr(oss_mod.OSSClient, "upload", _noop_upload)

    from Flowcut.api.server import app

    with TestClient(app) as client:
        # mock submit_task，避免真触发 worker 拉任务（同时省去 Redis/queue 副作用）
        container = app.state.container

        async def _fake_submit_task(envelope):  # type: ignore[no-untyped-def]
            return "task-fake-0001"

        monkeypatch.setattr(container.runtime, "submit_task", _fake_submit_task)
        yield client


def _post_upload(client: TestClient, *, tenant_key: str, product: str | None = None):
    files = {"file": ("test.mp4", io.BytesIO(b"fake-bytes"), "video/mp4")}
    data = {"tenant_key": tenant_key}
    if product is not None:
        data["product"] = product
    return client.post("/reference-videos/upload", files=files, data=data)


@pytest.mark.integration
def test_upload_creates_script_in_processing(app_client: TestClient):
    """上传完应预建 script(PROCESSING, segments=[])，响应含 script_id。"""
    resp = _post_upload(app_client, tenant_key="t_prebuild_upload", product="洗发水Y")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert "script_id" in body, body
    assert "ref_video_id" in body
    ref_video_id = body["ref_video_id"]
    script_id = body["script_id"]
    assert isinstance(script_id, int) and script_id > 0

    # GET /flowcut/scripts/{id} 验证
    get_resp = app_client.get(f"/flowcut/scripts/{script_id}")
    assert get_resp.status_code == 200, get_resp.text
    script = get_resp.json()
    assert script["status"] == "PROCESSING"
    assert script["segments"] == []
    assert script["reference_video_id"] == ref_video_id
    assert script["source"] == "decomposed"
    assert script["product"] == "洗发水Y"

    # ref_video.script_id 已回填
    rv_resp = app_client.get(f"/reference-videos/{ref_video_id}")
    assert rv_resp.status_code == 200
    assert rv_resp.json()["script_id"] == script_id


@pytest.mark.integration
def test_decompose_route_reuses_existing_script(app_client: TestClient):
    """已 /upload 预建过 script，再 /decompose 不应重复建。"""
    resp = _post_upload(app_client, tenant_key="t_prebuild_reuse")
    assert resp.status_code == 200
    body = resp.json()
    ref_video_id = body["ref_video_id"]
    original_script_id = body["script_id"]

    decompose_resp = app_client.post(f"/reference-videos/{ref_video_id}/decompose")
    assert decompose_resp.status_code == 200, decompose_resp.text
    decompose_body = decompose_resp.json()
    assert decompose_body.get("script_id") == original_script_id

    rv_resp = app_client.get(f"/reference-videos/{ref_video_id}")
    assert rv_resp.json()["script_id"] == original_script_id


@pytest.mark.integration
def test_decompose_route_creates_script_if_missing(app_client: TestClient):
    """老数据：ref_video 无 script_id，调 /decompose 应补建。"""
    # 借 /upload 造一条 ref_video，然后用同步 MySQL 把 script_id 清空，模拟老数据。
    upload_resp = _post_upload(app_client, tenant_key="t_prebuild_missing")
    assert upload_resp.status_code == 200
    ref_video_id = upload_resp.json()["ref_video_id"]
    original_script_id = upload_resp.json()["script_id"]

    import os

    import pymysql

    conn = pymysql.connect(
        host=os.environ["MYSQL_HOST"],
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DB"],
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE fc_reference_video SET script_id=NULL WHERE id=%s",
                (ref_video_id,),
            )
            # 同时删掉那条预建的 script，避免污染
            cur.execute("DELETE FROM fc_script WHERE id=%s", (original_script_id,))
        conn.commit()
    finally:
        conn.close()

    rv_before = app_client.get(f"/reference-videos/{ref_video_id}").json()
    assert rv_before.get("script_id") is None

    decompose_resp = app_client.post(f"/reference-videos/{ref_video_id}/decompose")
    assert decompose_resp.status_code == 200, decompose_resp.text
    body = decompose_resp.json()
    assert "script_id" in body
    new_script_id = body["script_id"]
    assert isinstance(new_script_id, int) and new_script_id > 0
    assert new_script_id != original_script_id

    # 验证：fc_reference_video.script_id 已回填，且 script(PROCESSING, segments=[])
    rv_after = app_client.get(f"/reference-videos/{ref_video_id}").json()
    assert rv_after["script_id"] == new_script_id

    script = app_client.get(f"/flowcut/scripts/{new_script_id}").json()
    assert script["status"] == "PROCESSING"
    assert script["segments"] == []
    assert script["reference_video_id"] == ref_video_id
    assert script["source"] == "decomposed"
