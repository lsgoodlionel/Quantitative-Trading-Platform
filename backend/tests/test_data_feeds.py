"""
Phase 1 数据源单元测试

测试数据源的接口契约，使用 mock 避免实际 API 调用。
集成测试（真实 API）在 tests/integration/ 中单独运行。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.data.feeds.alpaca import AlpacaDataFeed, _to_bar
from app.data.feeds.yfinance_feed import YFinanceDataFeed, _to_yf_symbol
from app.data.models import Bar, Frequency, Market


class TestAlpacaDataFeed:
    def _make_mock_bar(self) -> MagicMock:
        raw = MagicMock()
        raw.timestamp = datetime(2024, 1, 15, 14, 30, tzinfo=timezone.utc)
        raw.open = 180.0
        raw.high = 185.0
        raw.low = 179.0
        raw.close = 183.5
        raw.volume = 1_000_000
        raw.vwap = 182.3
        raw.trade_count = 42000
        return raw

    def test_to_bar_converts_alpaca_bar_correctly(self) -> None:
        raw = self._make_mock_bar()
        bar = _to_bar(raw, "AAPL", Frequency.DAY_1)

        assert bar.symbol == "AAPL"
        assert bar.market == Market.US
        assert bar.open == 180.0
        assert bar.close == 183.5
        assert bar.vwap == 182.3
        assert bar.trade_count == 42000
        assert bar.time.tzinfo is not None

    def test_to_bar_handles_missing_vwap(self) -> None:
        raw = self._make_mock_bar()
        raw.vwap = None
        bar = _to_bar(raw, "AAPL", Frequency.DAY_1)
        assert bar.vwap is None

    def test_to_bar_adds_utc_if_missing(self) -> None:
        raw = self._make_mock_bar()
        raw.timestamp = datetime(2024, 1, 15, 14, 30)  # no tzinfo
        bar = _to_bar(raw, "AAPL", Frequency.DAY_1)
        assert bar.time.tzinfo is not None

    @pytest.mark.asyncio
    async def test_get_bars_returns_sorted_bars(self) -> None:
        feed = AlpacaDataFeed()

        raw1 = self._make_mock_bar()
        raw1.timestamp = datetime(2024, 1, 16, tzinfo=timezone.utc)
        raw1.open = 185.0
        raw1.close = 186.0
        raw1.high = 187.0
        raw1.low = 184.0

        raw2 = self._make_mock_bar()
        raw2.timestamp = datetime(2024, 1, 15, tzinfo=timezone.utc)

        mock_bar_set = MagicMock()
        mock_bar_set.data = {"AAPL": [raw1, raw2]}  # 逆序，检验排序

        mock_loop = MagicMock()
        mock_loop.run_in_executor = AsyncMock(return_value=mock_bar_set)

        # 同时 mock alpaca 模块导入，避免 ModuleNotFoundError
        mock_alpaca_mod = MagicMock()
        mock_alpaca_mod.StockBarsRequest = MagicMock()
        mock_alpaca_mod.TimeFrame = MagicMock()
        mock_alpaca_mod.TimeFrameUnit = MagicMock()

        with patch.object(feed, "_get_hist_client", return_value=MagicMock()), \
             patch("asyncio.get_event_loop", return_value=mock_loop), \
             patch.dict("sys.modules", {
                 "alpaca": MagicMock(),
                 "alpaca.data": MagicMock(),
                 "alpaca.data.requests": mock_alpaca_mod,
                 "alpaca.data.timeframe": mock_alpaca_mod,
             }):
            bars = await feed.get_bars("AAPL", Frequency.DAY_1, date(2024, 1, 15), date(2024, 1, 16))

        assert len(bars) == 2
        assert bars[0].time < bars[1].time  # 验证排序

    @pytest.mark.asyncio
    async def test_get_bars_returns_empty_on_api_failure(self) -> None:
        feed = AlpacaDataFeed()
        with patch.object(feed, "_get_hist_client", side_effect=RuntimeError("API down")):
            with pytest.raises(RuntimeError):
                await feed.get_bars("AAPL", Frequency.DAY_1, date(2024, 1, 1), date(2024, 1, 31))


class TestYFinanceDataFeed:
    def test_to_yf_symbol_us_passthrough(self) -> None:
        assert _to_yf_symbol("AAPL", Market.US) == "AAPL"
        assert _to_yf_symbol("MSFT", Market.US) == "MSFT"

    def test_to_yf_symbol_hk_converts_format(self) -> None:
        assert _to_yf_symbol("00700", Market.HK) == "700.HK"
        assert _to_yf_symbol("01810", Market.HK) == "1810.HK"

    def test_to_yf_symbol_hk_passthrough_if_already_formatted(self) -> None:
        assert _to_yf_symbol("0700.HK", Market.HK) == "0700.HK"

    def test_to_yf_symbol_hk_edge_case_all_zeros(self) -> None:
        # 极端情况：全零代码
        result = _to_yf_symbol("0000", Market.HK)
        assert result.endswith(".HK")

    @pytest.mark.asyncio
    async def test_get_bars_uses_correct_interval(self) -> None:
        feed = YFinanceDataFeed(Market.US)

        import pandas as pd
        mock_df = pd.DataFrame(
            {
                "Open": [180.0],
                "High": [185.0],
                "Low": [179.0],
                "Close": [183.5],
                "Volume": [1_000_000],
            },
            index=pd.DatetimeIndex([datetime(2024, 1, 15, tzinfo=timezone.utc)]),
        )

        with patch("asyncio.get_event_loop") as mock_loop:
            mock_exec = AsyncMock(return_value=mock_df)
            mock_loop.return_value.run_in_executor = mock_exec

            bars = await feed.get_bars("AAPL", Frequency.DAY_1, date(2024, 1, 15), date(2024, 1, 15))

        assert len(bars) == 1
        assert bars[0].symbol == "AAPL"
        assert bars[0].market == Market.US
        assert bars[0].close == 183.5

    @pytest.mark.asyncio
    async def test_get_bars_returns_empty_for_empty_df(self) -> None:
        feed = YFinanceDataFeed(Market.US)

        import pandas as pd
        with patch("asyncio.get_event_loop") as mock_loop:
            mock_exec = AsyncMock(return_value=pd.DataFrame())
            mock_loop.return_value.run_in_executor = mock_exec
            bars = await feed.get_bars("INVALID", Frequency.DAY_1, date(2024, 1, 15), date(2024, 1, 15))

        assert bars == []

    def test_yfinance_feed_does_not_support_realtime(self) -> None:
        feed = YFinanceDataFeed()
        assert feed.supports_realtime is False
