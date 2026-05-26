"""
Phase 1 DataService 集成测试（使用 mock 数据源和内存存储）
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.data.models import Bar, Frequency, Market
from app.data.service import DataService, _FeedRegistry


def _make_bar(symbol: str = "AAPL", market: Market = Market.US, close: float = 180.0) -> Bar:
    return Bar(
        time=datetime(2024, 1, 15, tzinfo=timezone.utc),
        symbol=symbol,
        market=market,
        frequency=Frequency.DAY_1,
        open=close - 5,
        high=close + 5,
        low=close - 8,
        close=close,
        volume=1_000_000,
    )


class TestDataServiceRouting:
    @pytest.mark.asyncio
    async def test_us_market_uses_alpaca_feed(self) -> None:
        mock_session = AsyncMock()
        svc = DataService(mock_session)

        mock_bars = [_make_bar("AAPL", Market.US, 183.5)]
        mock_repo = AsyncMock()
        mock_repo.get_bars.return_value = []  # 缓存未命中
        mock_repo.save_bars.return_value = 1
        svc._repo = mock_repo

        mock_alpaca = AsyncMock()
        mock_alpaca.get_bars.return_value = mock_bars

        with patch.object(svc._registry, "get_feeds", return_value=(mock_alpaca, None)):
            result = await svc.get_bars("AAPL", Market.US, Frequency.DAY_1, date(2024, 1, 15), date(2024, 1, 15))

        mock_alpaca.get_bars.assert_called_once()
        assert len(result) == 1
        assert result[0].symbol == "AAPL"

    @pytest.mark.asyncio
    async def test_cache_hit_skips_api_call(self) -> None:
        mock_session = AsyncMock()
        svc = DataService(mock_session)

        cached_bars = [_make_bar() for _ in range(250)]  # 足够多，覆盖率高
        mock_repo = AsyncMock()
        mock_repo.get_bars.return_value = cached_bars
        svc._repo = mock_repo

        mock_alpaca = AsyncMock()
        with patch.object(svc._registry, "get_feeds", return_value=(mock_alpaca, None)):
            result = await svc.get_bars(
                "AAPL", Market.US, Frequency.DAY_1,
                date(2024, 1, 1), date(2024, 1, 15)
            )

        mock_alpaca.get_bars.assert_not_called()
        assert len(result) == 250

    @pytest.mark.asyncio
    async def test_fallback_activates_on_primary_failure(self) -> None:
        mock_session = AsyncMock()
        svc = DataService(mock_session)

        mock_repo = AsyncMock()
        mock_repo.get_bars.return_value = []
        mock_repo.save_bars.return_value = 1
        svc._repo = mock_repo

        mock_primary = AsyncMock()
        mock_primary.get_bars.side_effect = Exception("Alpaca down")
        mock_primary.name = "AlpacaFeed"

        fallback_bars = [_make_bar()]
        mock_fallback = AsyncMock()
        mock_fallback.get_bars.return_value = fallback_bars

        with patch.object(svc._registry, "get_feeds", return_value=(mock_primary, mock_fallback)):
            result = await svc.get_bars("AAPL", Market.US, Frequency.DAY_1, date(2024, 1, 15), date(2024, 1, 15))

        mock_fallback.get_bars.assert_called_once()
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_unsupported_a_share_market_raises(self) -> None:
        mock_session = AsyncMock()
        svc = DataService(mock_session)

        mock_repo = AsyncMock()
        mock_repo.get_bars.return_value = []
        svc._repo = mock_repo

        with pytest.raises((ValueError, Exception)):
            await svc.get_bars("000001", Market.A, Frequency.DAY_1, date(2024, 1, 1), date(2024, 1, 15))

    @pytest.mark.asyncio
    async def test_bars_written_to_cache_after_api_call(self) -> None:
        mock_session = AsyncMock()
        svc = DataService(mock_session)

        api_bars = [_make_bar()]
        mock_repo = AsyncMock()
        mock_repo.get_bars.return_value = []
        mock_repo.save_bars.return_value = 1
        svc._repo = mock_repo

        mock_feed = AsyncMock()
        mock_feed.get_bars.return_value = api_bars

        with patch.object(svc._registry, "get_feeds", return_value=(mock_feed, None)):
            await svc.get_bars("AAPL", Market.US, Frequency.DAY_1, date(2024, 1, 15), date(2024, 1, 15))

        mock_repo.save_bars.assert_called_once_with(api_bars)


class TestFeedRegistry:
    def test_us_market_returns_alpaca_primary(self) -> None:
        registry = _FeedRegistry.instance()
        primary, fallback = registry.get_feeds(Market.US)
        assert "Alpaca" in primary.__class__.__name__
        assert fallback is not None

    def test_hk_market_returns_futu_primary(self) -> None:
        registry = _FeedRegistry.instance()
        primary, fallback = registry.get_feeds(Market.HK)
        assert "Futu" in primary.__class__.__name__
        assert fallback is not None

    def test_a_share_market_raises_not_supported(self) -> None:
        registry = _FeedRegistry.instance()
        with pytest.raises(ValueError, match="not yet supported"):
            registry.get_feeds(Market.A)
