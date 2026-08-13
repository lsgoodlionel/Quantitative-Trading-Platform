"""日度盈亏拆解测试（Wave K-c / K1）

验收要点（契约 waveKc §七.3）：
- trading_pnl + holding_pnl == total_pnl
- total_pnl - commission - slippage == net_pnl
- 组合日结 == 各合约日结的加总
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.data.models import Market
from app.engine.backtest.broker import Fill, OrderSide
from app.engine.backtest.daily_result import (
    DailyPnlTracker,
    build_contract_daily_result,
    build_portfolio_daily_result,
)


def _fill(symbol: str, side: OrderSide, qty: int, price: float, commission: float = 1.0) -> Fill:
    return Fill(
        order_id="o1",
        symbol=symbol,
        market=Market.US,
        side=side,
        qty=qty,
        price=price,
        commission=commission,
        filled_at=datetime(2024, 1, 2, tzinfo=UTC),
    )


class TestContractDailyResult:
    def test_pnl_components_sum_to_total(self) -> None:
        # Arrange: 昨日持仓 100 股，今日再买 50 股
        fills = [_fill("AAPL", OrderSide.BUY, 50, 101.0, commission=2.0)]

        # Act
        result = build_contract_daily_result(
            symbol="AAPL",
            day=date(2024, 1, 2),
            close_price=105.0,
            pre_close=100.0,
            start_pos=100,
            fills=fills,
        )

        # Assert
        assert result.trading_pnl + result.holding_pnl == pytest.approx(result.total_pnl)
        assert result.total_pnl - result.commission - result.slippage == pytest.approx(
            result.net_pnl
        )

    def test_holding_pnl_uses_start_position(self) -> None:
        result = build_contract_daily_result(
            symbol="AAPL",
            day=date(2024, 1, 2),
            close_price=105.0,
            pre_close=100.0,
            start_pos=100,
            fills=[],
        )

        assert result.holding_pnl == pytest.approx(100 * 5.0)
        assert result.trading_pnl == 0.0
        assert result.end_pos == 100

    def test_trading_pnl_marks_fills_to_close(self) -> None:
        fills = [_fill("AAPL", OrderSide.BUY, 50, 101.0, commission=0.0)]

        result = build_contract_daily_result(
            symbol="AAPL",
            day=date(2024, 1, 2),
            close_price=105.0,
            pre_close=100.0,
            start_pos=0,
            fills=fills,
        )

        # 买入 50 股，成交价 101，收盘 105 → 50 × 4 = 200
        assert result.trading_pnl == pytest.approx(200.0)
        assert result.end_pos == 50
        assert result.turnover == pytest.approx(50 * 101.0)
        assert result.trade_count == 1

    def test_sell_reduces_end_position(self) -> None:
        fills = [_fill("AAPL", OrderSide.SELL, 30, 106.0, commission=0.0)]

        result = build_contract_daily_result(
            symbol="AAPL",
            day=date(2024, 1, 2),
            close_price=105.0,
            pre_close=100.0,
            start_pos=100,
            fills=fills,
        )

        assert result.end_pos == 70
        # 卖出 30 股于 106，收盘 105 → -30 × (105 - 106) = +30
        assert result.trading_pnl == pytest.approx(30.0)

    def test_short_start_position_holding_pnl_is_negative_when_price_rises(self) -> None:
        result = build_contract_daily_result(
            symbol="AAPL",
            day=date(2024, 1, 2),
            close_price=105.0,
            pre_close=100.0,
            start_pos=-100,
            fills=[],
        )

        assert result.holding_pnl == pytest.approx(-500.0)


class TestPortfolioDailyResult:
    def test_aggregates_contract_results(self) -> None:
        a = build_contract_daily_result(
            symbol="AAPL", day=date(2024, 1, 2), close_price=105.0, pre_close=100.0,
            start_pos=100, fills=[_fill("AAPL", OrderSide.BUY, 10, 101.0, 1.5)],
        )
        b = build_contract_daily_result(
            symbol="MSFT", day=date(2024, 1, 2), close_price=200.0, pre_close=210.0,
            start_pos=50, fills=[],
        )

        portfolio = build_portfolio_daily_result(date(2024, 1, 2), [a, b])

        assert portfolio.total_pnl == pytest.approx(a.total_pnl + b.total_pnl)
        assert portfolio.commission == pytest.approx(a.commission + b.commission)
        assert portfolio.turnover == pytest.approx(a.turnover + b.turnover)
        assert portfolio.trade_count == a.trade_count + b.trade_count
        assert portfolio.net_pnl == pytest.approx(
            portfolio.total_pnl - portfolio.commission - portfolio.slippage
        )
        assert set(portfolio.contracts) == {"AAPL", "MSFT"}

    def test_empty_day_is_all_zero(self) -> None:
        portfolio = build_portfolio_daily_result(date(2024, 1, 2), [])

        assert portfolio.total_pnl == 0.0
        assert portfolio.net_pnl == 0.0
        assert portfolio.trade_count == 0


class TestDailyPnlTracker:
    def test_tracks_days_and_carries_pre_close(self) -> None:
        tracker = DailyPnlTracker()

        tracker.begin_day(datetime(2024, 1, 2, tzinfo=UTC), {"AAPL": 0})
        tracker.record(
            [_fill("AAPL", OrderSide.BUY, 100, 100.0, commission=0.0)],
            {"AAPL": 102.0},
        )
        tracker.begin_day(datetime(2024, 1, 3, tzinfo=UTC), {"AAPL": 100})
        tracker.record([], {"AAPL": 107.0})
        results = tracker.finish()

        assert len(results) == 2
        # 第二日无成交，持仓盈亏 = 100 × (107 - 102)
        assert results[1].holding_pnl == pytest.approx(500.0)
        assert results[1].trading_pnl == 0.0
        assert results[0].trading_pnl == pytest.approx(100 * 2.0)

    def test_begin_day_reports_day_rollover(self) -> None:
        tracker = DailyPnlTracker()

        assert tracker.begin_day(datetime(2024, 1, 2, 9, tzinfo=UTC), {}) is True
        assert tracker.begin_day(datetime(2024, 1, 2, 15, tzinfo=UTC), {}) is False
        assert tracker.begin_day(datetime(2024, 1, 3, 9, tzinfo=UTC), {}) is True

    def test_record_before_begin_day_raises(self) -> None:
        with pytest.raises(RuntimeError):
            DailyPnlTracker().record([], {})

    def test_finish_is_idempotent(self) -> None:
        tracker = DailyPnlTracker()
        tracker.begin_day(datetime(2024, 1, 2, tzinfo=UTC), {})
        tracker.record([], {"AAPL": 100.0})

        assert tracker.finish() == tracker.finish()

    def test_no_update_yields_no_results(self) -> None:
        assert DailyPnlTracker().finish() == []
