"""纯逻辑测试：creatives 路由中的 preroll PATCH、download_creative、
export_highlight_creative。不启动 HTTP 客户端；直接调用路由函数并断言。
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException


# ─────────────────────────────────────────────────────────────────────────────
# 辅助 fake 类
# ─────────────────────────────────────────────────────────────────────────────

class _FakeCreativeRepo:
    def __init__(self, rows: dict[int, dict]):
        self._rows = rows
        self.last_preroll_set: tuple | None = None

    async def get(self, creative_id: int) -> dict | None:
        return self._rows.get(creative_id)

    async def set_preroll_asset(self, creative_id: int, preroll_id: int | None) -> None:
        self.last_preroll_set = (creative_id, preroll_id)


class _FakeHighlightAssetRepo:
    def __init__(self, assets: dict[int, dict]):
        self._assets = assets

    async def get(self, asset_id: int) -> dict | None:
        return self._assets.get(asset_id)


class _FakeContainer:
    def __init__(self, creative_repo, highlight_asset_repo):
        self.creative_repo = creative_repo
        self.highlight_asset_repo = highlight_asset_repo


class _FakeRequest:
    def __init__(self, container):
        class _State:
            pass
        self.app = type("app", (), {"state": type("state", (), {"container": container})()})()


# ─────────────────────────────────────────────────────────────────────────────
# 从路由模块抽取被测函数（不经过 FastAPI router 分发）
# ─────────────────────────────────────────────────────────────────────────────

from Flowcut.api.routes.creatives import (
    PrerollUpdate,
    set_creative_preroll,
    download_creative,
    export_highlight_creative,
)


# ─────────────────────────────────────────────────────────────────────────────
# PrerollUpdate 模型
# ─────────────────────────────────────────────────────────────────────────────

class TestPrerollUpdateModel:
    pytestmark = pytest.mark.unit

    def test_default_is_none(self):
        m = PrerollUpdate()
        assert m.preroll_asset_id is None

    def test_accepts_int(self):
        m = PrerollUpdate(preroll_asset_id=42)
        assert m.preroll_asset_id == 42

    def test_no_preroll_scope_field(self):
        """preroll_scope 字段不应存在。"""
        assert not hasattr(PrerollUpdate, "preroll_scope")
        assert "preroll_scope" not in PrerollUpdate.model_fields


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /creatives/{creative_id}/preroll
# ─────────────────────────────────────────────────────────────────────────────

class TestSetCreativePreroll:
    pytestmark = pytest.mark.unit

    def _make_deps(self, creative_row: dict, preroll_asset: dict | None = None):
        creative_repo = _FakeCreativeRepo({1: creative_row})
        assets = {99: preroll_asset} if preroll_asset else {}
        asset_repo = _FakeHighlightAssetRepo(assets)
        container = _FakeContainer(creative_repo, asset_repo)
        request = _FakeRequest(container)
        return request, creative_repo

    @pytest.mark.asyncio
    async def test_set_preroll_success(self):
        creative = {"id": 1, "tenant_key": "t1", "oss_key": "k"}
        asset = {"id": 99, "tenant_key": "t1", "asset_type": "preroll"}
        request, repo = self._make_deps(creative, asset)
        body = PrerollUpdate(preroll_asset_id=99)
        result = await set_creative_preroll(1, body, request, tenant_key="t1")
        assert result == {"ok": True, "creative_id": 1, "preroll_asset_id": 99}
        assert repo.last_preroll_set == (1, 99)

    @pytest.mark.asyncio
    async def test_clear_preroll_with_none(self):
        creative = {"id": 1, "tenant_key": "t1", "preroll_asset_id": 99}
        request, repo = self._make_deps(creative)
        body = PrerollUpdate(preroll_asset_id=None)
        result = await set_creative_preroll(1, body, request, tenant_key="t1")
        assert result["preroll_asset_id"] is None
        assert repo.last_preroll_set == (1, None)

    @pytest.mark.asyncio
    async def test_creative_not_found_raises_404(self):
        creative_repo = _FakeCreativeRepo({})
        container = _FakeContainer(creative_repo, _FakeHighlightAssetRepo({}))
        request = _FakeRequest(container)
        with pytest.raises(HTTPException) as exc_info:
            await set_creative_preroll(1, PrerollUpdate(preroll_asset_id=None), request, tenant_key="t1")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_wrong_tenant_raises_404(self):
        creative = {"id": 1, "tenant_key": "other_tenant"}
        request, _ = self._make_deps(creative)
        with pytest.raises(HTTPException) as exc_info:
            await set_creative_preroll(1, PrerollUpdate(preroll_asset_id=None), request, tenant_key="t1")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_invalid_asset_type_raises_422(self):
        creative = {"id": 1, "tenant_key": "t1"}
        # asset_type is wrong — not "preroll"
        asset = {"id": 99, "tenant_key": "t1", "asset_type": "digital_human_connector"}
        request, _ = self._make_deps(creative, asset)
        with pytest.raises(HTTPException) as exc_info:
            await set_creative_preroll(1, PrerollUpdate(preroll_asset_id=99), request, tenant_key="t1")
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_asset_belongs_to_other_tenant_raises_422(self):
        creative = {"id": 1, "tenant_key": "t1"}
        asset = {"id": 99, "tenant_key": "other_tenant", "asset_type": "preroll"}
        request, _ = self._make_deps(creative, asset)
        with pytest.raises(HTTPException) as exc_info:
            await set_creative_preroll(1, PrerollUpdate(preroll_asset_id=99), request, tenant_key="t1")
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_asset_not_found_raises_422(self):
        creative = {"id": 1, "tenant_key": "t1"}
        # no asset in repo
        request, _ = self._make_deps(creative, preroll_asset=None)
        with pytest.raises(HTTPException) as exc_info:
            await set_creative_preroll(1, PrerollUpdate(preroll_asset_id=99), request, tenant_key="t1")
        assert exc_info.value.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# download_creative — 302 fast-path 封锁逻辑
# ─────────────────────────────────────────────────────────────────────────────

class TestDownloadCreative:
    pytestmark = pytest.mark.unit

    def _make_request(self, creative_row: dict):
        creative_repo = _FakeCreativeRepo({1: creative_row})
        container = _FakeContainer(creative_repo, _FakeHighlightAssetRepo({}))
        return _FakeRequest(container)

    @pytest.mark.asyncio
    async def test_connector_set_blocks_download(self):
        row = {"id": 1, "tenant_key": "t1", "oss_key": "k",
               "connector_asset_id": 5, "preroll_asset_id": None}
        request = self._make_request(row)
        with pytest.raises(HTTPException) as exc_info:
            await download_creative(1, request, tenant_key="t1")
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_preroll_set_blocks_download(self):
        row = {"id": 1, "tenant_key": "t1", "oss_key": "k",
               "connector_asset_id": None, "preroll_asset_id": 7}
        request = self._make_request(row)
        with pytest.raises(HTTPException) as exc_info:
            await download_creative(1, request, tenant_key="t1")
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_both_set_blocks_download(self):
        row = {"id": 1, "tenant_key": "t1", "oss_key": "k",
               "connector_asset_id": 5, "preroll_asset_id": 7}
        request = self._make_request(row)
        with pytest.raises(HTTPException) as exc_info:
            await download_creative(1, request, tenant_key="t1")
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_neither_set_proceeds(self):
        """纯片（无 connector、无 preroll）应能通过 connector/preroll 检查，进入 OSS 步骤。"""
        from fastapi.responses import RedirectResponse

        row = {"id": 1, "tenant_key": "t1", "oss_key": "k",
               "connector_asset_id": None, "preroll_asset_id": None,
               "source_drama_name": "test_drama"}
        request = self._make_request(row)
        # OSS 可能配置也可能未配置：有配置返回 RedirectResponse，无配置抛 503
        try:
            result = await download_creative(1, request, tenant_key="t1")
            assert isinstance(result, RedirectResponse)
        except HTTPException as exc:
            # 503 = OSS 未配置 — 已通过 connector/preroll 检查
            assert exc.status_code == 503

    @pytest.mark.asyncio
    async def test_no_oss_key_raises_422(self):
        row = {"id": 1, "tenant_key": "t1", "oss_key": None,
               "connector_asset_id": None, "preroll_asset_id": None}
        request = self._make_request(row)
        with pytest.raises(HTTPException) as exc_info:
            await download_creative(1, request, tenant_key="t1")
        assert exc_info.value.status_code == 422


# ─────────────────────────────────────────────────────────────────────────────
# export_highlight_creative — 允许 preroll_asset_id 触发导出
# ─────────────────────────────────────────────────────────────────────────────

class _FakeRuntime:
    async def submit_task(self, envelope, *, tool_name, summary):
        return "fake_queue_id"


class _FakeContainerWithRuntime(_FakeContainer):
    def __init__(self, creative_repo, highlight_asset_repo):
        super().__init__(creative_repo, highlight_asset_repo)
        self.runtime = _FakeRuntime()


class TestExportHighlightCreative:
    pytestmark = pytest.mark.unit

    def _make_request(self, creative_row: dict):
        creative_repo = _FakeCreativeRepo({1: creative_row})
        container = _FakeContainerWithRuntime(creative_repo, _FakeHighlightAssetRepo({}))
        return _FakeRequest(container)

    @pytest.mark.asyncio
    async def test_no_connector_no_preroll_raises_422(self):
        row = {"id": 1, "tenant_key": "t1", "oss_key": "k",
               "connector_asset_id": None, "preroll_asset_id": None,
               "session_key": "s"}
        request = self._make_request(row)
        with pytest.raises(HTTPException) as exc_info:
            await export_highlight_creative(1, request, tenant_key="t1")
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_preroll_only_allows_export(self):
        row = {"id": 1, "tenant_key": "t1", "oss_key": "k",
               "connector_asset_id": None, "preroll_asset_id": 7,
               "session_key": "s"}
        request = self._make_request(row)
        result = await export_highlight_creative(1, request, tenant_key="t1")
        assert result["ok"] is True
        assert result["creative_id"] == 1

    @pytest.mark.asyncio
    async def test_connector_only_allows_export(self):
        row = {"id": 1, "tenant_key": "t1", "oss_key": "k",
               "connector_asset_id": 5, "preroll_asset_id": None,
               "session_key": "s"}
        request = self._make_request(row)
        result = await export_highlight_creative(1, request, tenant_key="t1")
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_both_connector_and_preroll_allows_export(self):
        row = {"id": 1, "tenant_key": "t1", "oss_key": "k",
               "connector_asset_id": 5, "preroll_asset_id": 7,
               "session_key": "s"}
        request = self._make_request(row)
        result = await export_highlight_creative(1, request, tenant_key="t1")
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_no_oss_key_raises_422(self):
        row = {"id": 1, "tenant_key": "t1", "oss_key": None,
               "connector_asset_id": 5, "preroll_asset_id": None,
               "session_key": "s"}
        request = self._make_request(row)
        with pytest.raises(HTTPException) as exc_info:
            await export_highlight_creative(1, request, tenant_key="t1")
        assert exc_info.value.status_code == 422
