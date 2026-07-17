"""Unit tests for make_highlight_export_executor (Task 4: preroll + connector branches)."""
from __future__ import annotations

import pytest

import Flowcut.runtime.executors as ex
from Flowcut.runtime.executors import make_highlight_export_executor
from Flowcut.runtime.streams import FlowcutTaskStream
from simpleclaw.runtime.task_protocol import TaskEnvelope

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fake dependencies
# ---------------------------------------------------------------------------

class _FakeCreativeRepo:
    def __init__(self, row: dict | None) -> None:
        self._row = row

    async def get(self, creative_id: int) -> dict | None:
        return self._row


class _FakeHighlightAssetRepo:
    def __init__(self, assets: dict[int, dict]) -> None:
        self._assets = assets

    async def get(self, asset_id: int) -> dict | None:
        return self._assets.get(asset_id)


class _FakeOSS:
    def __init__(self) -> None:
        self.downloads: list[tuple[str, str]] = []
        self.uploads: list[tuple[str, str]] = []

    def download(self, oss_key: str, local_path: str) -> None:
        self.downloads.append((oss_key, local_path))
        with open(local_path, "wb") as f:
            f.write(b"\x00")

    def upload(self, local_path: str, oss_key: str) -> str:
        self.uploads.append((local_path, oss_key))
        return oss_key

    def presigned_get_url(self, oss_key: str, *, disposition_filename: str = "") -> str:
        return f"https://example.com/{oss_key}?fn={disposition_filename}"


def _make_task(creative_id: int = 1) -> TaskEnvelope:
    return TaskEnvelope(
        task_type="highlight_export",
        payload={"creative_id": creative_id},
        stream=FlowcutTaskStream.HIGHLIGHT_PLAN,  # stream value is not validated in executor
        tenant_key="test_tenant",
    )


# ---------------------------------------------------------------------------
# Helpers: patch ffmpeg to no-ops
# ---------------------------------------------------------------------------

@pytest.fixture
def patch_ffmpeg(monkeypatch):
    monkeypatch.setattr(ex, "_ffmpeg_normalize_clip",
                        lambda src, dst: open(dst, "wb").close())
    monkeypatch.setattr(ex, "_ffmpeg_normalize_with_overlay",
                        lambda src, ovr, dst: open(dst, "wb").close())
    monkeypatch.setattr(ex, "_ffmpeg_concat",
                        lambda lst, dst: open(dst, "wb").close())
    monkeypatch.setattr(ex, "_write_concat_list",
                        lambda path, files: open(path, "w").close())


# ---------------------------------------------------------------------------
# Test: creative not found
# ---------------------------------------------------------------------------

class TestCreativeNotFound:
    @pytest.mark.asyncio
    async def test_returns_failed_when_creative_missing(self, patch_ffmpeg):
        executor = make_highlight_export_executor(
            creative_repo=_FakeCreativeRepo(None),
            highlight_asset_repo=_FakeHighlightAssetRepo({}),
            oss_client=_FakeOSS(),
        )
        result = await executor(_make_task(creative_id=99))
        assert result.status == "failed"
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_returns_failed_when_no_oss_key(self, patch_ffmpeg):
        creative = {"id": 1, "oss_key": "", "tenant_key": "t",
                    "preroll_asset_id": None, "connector_asset_id": None}
        executor = make_highlight_export_executor(
            creative_repo=_FakeCreativeRepo(creative),
            highlight_asset_repo=_FakeHighlightAssetRepo({}),
            oss_client=_FakeOSS(),
        )
        result = await executor(_make_task())
        assert result.status == "failed"
        assert "1 分钟片" in result.error


# ---------------------------------------------------------------------------
# Test: preroll asset validation
# ---------------------------------------------------------------------------

class TestPrerollValidation:
    @pytest.mark.asyncio
    async def test_returns_failed_when_preroll_asset_missing(self, patch_ffmpeg):
        creative = {
            "id": 1, "oss_key": "clips/clip.mp4", "tenant_key": "t",
            "preroll_asset_id": 42, "connector_asset_id": None,
        }
        executor = make_highlight_export_executor(
            creative_repo=_FakeCreativeRepo(creative),
            highlight_asset_repo=_FakeHighlightAssetRepo({}),  # asset 42 not present
            oss_client=_FakeOSS(),
        )
        result = await executor(_make_task())
        assert result.status == "failed"
        assert "preroll_asset_id=42" in result.error


# ---------------------------------------------------------------------------
# Test: preroll only (no connector) — filename suffix "前贴"
# ---------------------------------------------------------------------------

class TestPrerollNoConnector:
    @pytest.mark.asyncio
    async def test_succeeds_with_preroll_suffix(self, patch_ffmpeg):
        creative = {
            "id": 1, "oss_key": "clips/clip.mp4", "tenant_key": "test_tenant",
            "preroll_asset_id": 10, "connector_asset_id": None,
            "source_drama_name": "斗破苍穹",
        }
        preroll_asset = {"id": 10, "oss_key": "assets/preroll.png", "oss_url": ""}
        oss = _FakeOSS()
        executor = make_highlight_export_executor(
            creative_repo=_FakeCreativeRepo(creative),
            highlight_asset_repo=_FakeHighlightAssetRepo({10: preroll_asset}),
            oss_client=oss,
        )
        result = await executor(_make_task())
        assert result.status == "succeeded"
        assert result.details["result_url"].endswith("_前贴.mp4")
        downloaded_keys = [k for k, _ in oss.downloads]
        assert "assets/preroll.png" in downloaded_keys
        # Only clip + preroll downloaded (no connector)
        assert len(oss.downloads) == 2

    @pytest.mark.asyncio
    async def test_overlay_ffmpeg_called_not_normalize(self, monkeypatch):
        normalize_calls: list[tuple] = []
        overlay_calls: list[tuple] = []

        monkeypatch.setattr(
            ex, "_ffmpeg_normalize_clip",
            lambda src, dst: (normalize_calls.append((src, dst)), open(dst, "wb").close()),
        )
        monkeypatch.setattr(
            ex, "_ffmpeg_normalize_with_overlay",
            lambda src, ovr, dst: (overlay_calls.append((src, ovr, dst)), open(dst, "wb").close()),
        )
        monkeypatch.setattr(ex, "_ffmpeg_concat", lambda lst, dst: open(dst, "wb").close())
        monkeypatch.setattr(ex, "_write_concat_list", lambda p, f: open(p, "w").close())

        creative = {
            "id": 1, "oss_key": "clips/clip.mp4", "tenant_key": "t",
            "preroll_asset_id": 10, "connector_asset_id": None,
            "source_drama_name": "剧名",
        }
        preroll_asset = {"id": 10, "oss_key": "assets/pre.png", "oss_url": ""}
        executor = make_highlight_export_executor(
            creative_repo=_FakeCreativeRepo(creative),
            highlight_asset_repo=_FakeHighlightAssetRepo({10: preroll_asset}),
            oss_client=_FakeOSS(),
        )
        result = await executor(_make_task())
        assert result.status == "succeeded"
        # overlay must have been called once; normalize_clip not called (no connector dh_norm)
        assert len(overlay_calls) == 1
        assert normalize_calls == []


# ---------------------------------------------------------------------------
# Test: connector only (no preroll) — filename suffix "数字人"
# ---------------------------------------------------------------------------

class TestConnectorNoPreroll:
    @pytest.mark.asyncio
    async def test_succeeds_with_connector_suffix(self, patch_ffmpeg):
        creative = {
            "id": 2, "oss_key": "clips/clip.mp4", "tenant_key": "test_tenant",
            "preroll_asset_id": None, "connector_asset_id": 20,
            "source_drama_name": "庆余年",
        }
        connector_asset = {"id": 20, "oss_key": "assets/dh.mp4", "oss_url": ""}
        oss = _FakeOSS()
        executor = make_highlight_export_executor(
            creative_repo=_FakeCreativeRepo(creative),
            highlight_asset_repo=_FakeHighlightAssetRepo({20: connector_asset}),
            oss_client=oss,
        )
        result = await executor(_make_task(creative_id=2))
        assert result.status == "succeeded"
        assert result.details["result_url"].endswith("_数字人.mp4")
        downloaded_keys = [k for k, _ in oss.downloads]
        assert "assets/dh.mp4" in downloaded_keys

    @pytest.mark.asyncio
    async def test_normalize_clip_called_not_overlay(self, monkeypatch):
        normalize_calls: list[tuple] = []
        overlay_calls: list[tuple] = []

        monkeypatch.setattr(
            ex, "_ffmpeg_normalize_clip",
            lambda src, dst: (normalize_calls.append((src, dst)), open(dst, "wb").close()),
        )
        monkeypatch.setattr(
            ex, "_ffmpeg_normalize_with_overlay",
            lambda src, ovr, dst: (overlay_calls.append((src, ovr, dst)), open(dst, "wb").close()),
        )
        monkeypatch.setattr(ex, "_ffmpeg_concat", lambda lst, dst: open(dst, "wb").close())
        monkeypatch.setattr(ex, "_write_concat_list", lambda p, f: open(p, "w").close())

        creative = {
            "id": 2, "oss_key": "clips/clip.mp4", "tenant_key": "t",
            "preroll_asset_id": None, "connector_asset_id": 20,
            "source_drama_name": "剧名",
        }
        connector_asset = {"id": 20, "oss_key": "assets/dh.mp4", "oss_url": ""}
        executor = make_highlight_export_executor(
            creative_repo=_FakeCreativeRepo(creative),
            highlight_asset_repo=_FakeHighlightAssetRepo({20: connector_asset}),
            oss_client=_FakeOSS(),
        )
        result = await executor(_make_task(creative_id=2))
        assert result.status == "succeeded"
        assert overlay_calls == []
        # normalize called twice: clip_processed + dh_norm
        assert len(normalize_calls) == 2


# ---------------------------------------------------------------------------
# Test: both preroll and connector
# ---------------------------------------------------------------------------

class TestPrerollAndConnector:
    @pytest.mark.asyncio
    async def test_succeeds_with_connector_suffix_when_both_set(self, patch_ffmpeg):
        """When both preroll and connector are present, suffix is 数字人 (connector wins)."""
        creative = {
            "id": 3, "oss_key": "clips/clip.mp4", "tenant_key": "test_tenant",
            "preroll_asset_id": 10, "connector_asset_id": 20,
            "source_drama_name": "仙逆",
        }
        assets = {
            10: {"id": 10, "oss_key": "assets/pre.png", "oss_url": ""},
            20: {"id": 20, "oss_key": "assets/dh.mp4", "oss_url": ""},
        }
        oss = _FakeOSS()
        executor = make_highlight_export_executor(
            creative_repo=_FakeCreativeRepo(creative),
            highlight_asset_repo=_FakeHighlightAssetRepo(assets),
            oss_client=oss,
        )
        result = await executor(_make_task(creative_id=3))
        assert result.status == "succeeded"
        assert result.details["result_url"].endswith("_数字人.mp4")
        downloaded_keys = [k for k, _ in oss.downloads]
        assert "assets/pre.png" in downloaded_keys
        assert "assets/dh.mp4" in downloaded_keys


# ---------------------------------------------------------------------------
# Test: neither preroll nor connector — plain normalize, suffix "前贴"
# ---------------------------------------------------------------------------

class TestNoPrerollNoConnector:
    @pytest.mark.asyncio
    async def test_succeeds_with_preroll_suffix_no_extra_assets(self, patch_ffmpeg):
        creative = {
            "id": 4, "oss_key": "clips/clip.mp4", "tenant_key": "test_tenant",
            "preroll_asset_id": None, "connector_asset_id": None,
            "source_drama_name": "无名之辈",
        }
        oss = _FakeOSS()
        executor = make_highlight_export_executor(
            creative_repo=_FakeCreativeRepo(creative),
            highlight_asset_repo=_FakeHighlightAssetRepo({}),
            oss_client=oss,
        )
        result = await executor(_make_task(creative_id=4))
        assert result.status == "succeeded"
        assert result.details["result_url"].endswith("_前贴.mp4")
        # Only clip downloaded
        assert len(oss.downloads) == 1
