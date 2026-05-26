"""
Phase 1 数据模型单元测试
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.data.models import Bar, Frequency, Market, Tick


class TestBarModel:
    def _make_bar(self, **kwargs) -> Bar:
        defaults = {
            "time": datetime(2024, 1, 15, 9, 30, tzinfo=timezone.utc),
            "symbol": "AAPL",
            "market": Market.US,
            "frequency": Frequency.DAY_1,
            "open": 180.0,
            "high": 185.0,
            "low": 179.0,
            "close": 183.5,
            "volume": 1_000_000,
        }
        defaults.update(kwargs)
        return Bar(**defaults)

    def test_valid_bar_creation(self) -> None:
        bar = self._make_bar()
        assert bar.symbol == "AAPL"
        assert bar.market == Market.US
        assert bar.close == 183.5

    def test_bar_rejects_high_less_than_low(self) -> None:
        with pytest.raises(ValueError, match="high.*low"):
            self._make_bar(high=170.0, low=185.0)

    def test_bar_rejects_negative_volume(self) -> None:
        with pytest.raises(ValueError, match="Negative volume"):
            self._make_bar(volume=-1)

    def test_bar_change_calculation(self) -> None:
        bar = self._make_bar(open=180.0, close=183.5)
        assert bar.change == pytest.approx(3.5)

    def test_bar_change_pct_calculation(self) -> None:
        bar = self._make_bar(open=100.0, close=110.0)
        assert bar.change_pct == pytest.approx(0.1)

    def test_bar_mid_price(self) -> None:
        bar = self._make_bar(high=185.0, low=179.0)
        assert bar.mid == pytest.approx(182.0)

    def test_bar_is_frozen(self) -> None:
        bar = self._make_bar()
        with pytest.raises((AttributeError, TypeError)):
            bar.close = 999.0  # type: ignore[misc]

    def test_bar_optional_fields_default_to_none(self) -> None:
        bar = self._make_bar()
        assert bar.vwap is None
        assert bar.turnover is None
        assert bar.trade_count is None

    def test_hk_bar_creation(self) -> None:
        bar = self._make_bar(
            symbol="00700",
            market=Market.HK,
            open=350.0,
            high=360.0,
            low=348.0,
            close=355.0,
        )
        assert bar.market == Market.HK
        assert bar.symbol == "00700"


class TestTickModel:
    def _make_tick(self, **kwargs) -> Tick:
        defaults = {
            "time": datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc),
            "symbol": "AAPL",
            "market": Market.US,
            "last_price": 183.5,
            "last_size": 100,
            "bid_price": 183.4,
            "ask_price": 183.6,
        }
        defaults.update(kwargs)
        return Tick(**defaults)

    def test_valid_tick_creation(self) -> None:
        tick = self._make_tick()
        assert tick.last_price == 183.5

    def test_tick_spread_calculation(self) -> None:
        tick = self._make_tick(bid_price=183.4, ask_price=183.6)
        assert tick.spread == pytest.approx(0.2)

    def test_tick_spread_none_when_missing_prices(self) -> None:
        tick = self._make_tick(bid_price=None, ask_price=None)
        assert tick.spread is None


class TestMarketEnum:
    def test_market_values(self) -> None:
        assert Market.US.value == "US"
        assert Market.HK.value == "HK"
        assert Market.A.value == "A"

    def test_frequency_values(self) -> None:
        assert Frequency.DAY_1.value == "1d"
        assert Frequency.MIN_1.value == "1m"
        assert Frequency.HOUR_1.value == "1h"
