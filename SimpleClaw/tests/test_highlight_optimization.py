"""单元测试：跨集高光 connector_asset_id 扩展 + navigate_to 工具。"""
import json
import pytest

pytestmark = pytest.mark.unit


# ── Task 2 tests ──────────────────────────────────────────────────────────────

class TestCreateCrossEpisodeHighlightsTool:
    def _make_tool(self):
        from unittest.mock import MagicMock
        from Flowcut.tools.create_cross_episode_highlights import CreateCrossEpisodeHighlightsTool
        runtime = MagicMock()
        return CreateCrossEpisodeHighlightsTool(runtime=runtime)

    @pytest.mark.asyncio
    async def test_connector_asset_id_in_payload(self):
        tool = self._make_tool()
        result = await tool.prepare_task(
            drama_name="斗破苍穹",
            num_candidates=2,
            connector_asset_id=42,
        )
        assert result.payload["connector_asset_id"] == 42

    @pytest.mark.asyncio
    async def test_no_connector_asset_id_defaults_none(self):
        tool = self._make_tool()
        result = await tool.prepare_task(drama_name="斗破苍穹")
        assert result.payload.get("connector_asset_id") is None


# ── Task 3 tests ──────────────────────────────────────────────────────────────

class TestNavigateToTool:
    def _make_tool(self):
        from Flowcut.tools.navigate_to import NavigateToTool
        return NavigateToTool()

    @pytest.mark.asyncio
    async def test_valid_route_returns_navigate_directive(self):
        tool = self._make_tool()
        result = await tool.execute(route="/creative")
        data = json.loads(result.content)
        assert data["ok"] is True
        assert data["navigate"]["route"] == "/creative"

    @pytest.mark.asyncio
    async def test_invalid_route_returns_error(self):
        tool = self._make_tool()
        result = await tool.execute(route="/admin/secrets")
        assert result.ok is False

    @pytest.mark.asyncio
    async def test_route_with_params(self):
        tool = self._make_tool()
        result = await tool.execute(route="/workspace/:scriptId", params={"scriptId": 7})
        data = json.loads(result.content)
        assert data["navigate"]["route"] == "/workspace/7"

    @pytest.mark.asyncio
    async def test_param_with_path_traversal_rejected(self):
        tool = self._make_tool()
        result = await tool.execute(route="/workspace/:scriptId", params={"scriptId": "../secrets"})
        assert result.ok is False
        data = json.loads(result.content)
        assert data["error"] == "参数值含非法字符"

    def test_needs_followup_is_false(self):
        from Flowcut.tools.navigate_to import NavigateToTool
        assert NavigateToTool.needs_followup is False


# ── Task 4 tests ──────────────────────────────────────────────────────────────

class TestUIContextAttentionProvider:
    def _make_provider(self):
        from Flowcut.context.providers import UIContextAttentionProvider
        return UIContextAttentionProvider()

    @pytest.mark.asyncio
    async def test_no_context_returns_empty(self):
        provider = self._make_provider()
        from simpleclaw.context.providers import ContextBuildContext
        ctx = ContextBuildContext(history=[])
        packets = await provider.collect_attention(ctx)
        assert packets == []

    @pytest.mark.asyncio
    async def test_full_context_returns_packet(self):
        provider = self._make_provider()
        provider.set_ui_context({"route": "/creative", "tab": "highlight", "drama": "斗破苍穹"})
        from simpleclaw.context.providers import ContextBuildContext
        ctx = ContextBuildContext(history=[])
        packets = await provider.collect_attention(ctx)
        assert len(packets) == 1
        assert "/creative" in packets[0].content
        assert "highlight" in packets[0].content
        assert "斗破苍穹" in packets[0].content

    @pytest.mark.asyncio
    async def test_partial_context_no_drama(self):
        provider = self._make_provider()
        provider.set_ui_context({"route": "/material", "tab": "episode_source"})
        from simpleclaw.context.providers import ContextBuildContext
        ctx = ContextBuildContext(history=[])
        packets = await provider.collect_attention(ctx)
        assert len(packets) == 1
        assert "drama" not in packets[0].content

    @pytest.mark.asyncio
    async def test_set_none_clears_context(self):
        provider = self._make_provider()
        provider.set_ui_context({"route": "/creative"})
        provider.set_ui_context(None)
        from simpleclaw.context.providers import ContextBuildContext
        ctx = ContextBuildContext(history=[])
        packets = await provider.collect_attention(ctx)
        assert packets == []
