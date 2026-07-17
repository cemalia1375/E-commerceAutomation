import pytest
from unittest.mock import AsyncMock, MagicMock


def _make_repo():
    mock_cur = AsyncMock()
    mock_conn = MagicMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)
    mock_conn.cursor = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=mock_cur),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    mock_db = MagicMock()
    mock_db.acquire = MagicMock(return_value=mock_conn)
    return mock_cur, mock_db


@pytest.mark.unit
@pytest.mark.asyncio
async def test_set_preroll_asset_sets_correct_id():
    mock_cur, mock_db = _make_repo()
    from Flowcut.storage.creative_repo import CreativeRepository

    repo = CreativeRepository(mock_db)
    await repo.set_preroll_asset(42, 7)

    call_args = mock_cur.execute.call_args[0]
    assert "preroll_asset_id" in call_args[0]
    assert call_args[1][0] == 7    # preroll_asset_id
    assert call_args[1][2] == 42   # creative_id


@pytest.mark.unit
@pytest.mark.asyncio
async def test_set_preroll_asset_clear_passes_none():
    mock_cur, mock_db = _make_repo()
    from Flowcut.storage.creative_repo import CreativeRepository

    repo = CreativeRepository(mock_db)
    await repo.set_preroll_asset(42, None)

    params = mock_cur.execute.call_args[0][1]
    assert params[0] is None
    assert params[2] == 42
