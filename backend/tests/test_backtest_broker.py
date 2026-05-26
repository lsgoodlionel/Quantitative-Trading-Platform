"""SimulatedBroker 单元测试"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.data.models import Bar, Market, Frequency
from app.engine.backtest.broker import SimulatedBroker, OrderSide, OrderStatus
from app.engine.backtest.commission import USCommissionModel
from app.engine.backtest.slippage import NoSlippage


def _bar(symbol: str = "AAPL", open_: float = 100.0, close: float = 105.0) -> Bar:
    return Bar(
        time=datetime(2024, 1, 2, tzinfo=timezone.utc),
        symbol=symbol,
        market=Market.US,
        frequency=Frequency.DAY_1,
        open=open_,
        high=max(open_, close) + 1,
        low=min(open_, close) - 1,
        close=close,
        volume=10_000,
    )


@pytest.fixture
def broker() -> SimulatedBroker:
    return SimulatedBroker(
        initial_cash=100_000.0,
        market=Market.US,
        commission_model=USCommissionModel(),
        slippage_model=NoSlippage(),
    )


class TestBrokerInit:
    def test_initial_cash(self, broker: SimulatedBroker) -> None:
        assert broker.cash == 100_000.0

    def test_no_positions(self, broker: SimulatedBroker) -> None:
        assert broker.positions.get("AAPL").qty == 0


class TestBuyOrder:
    def test_buy_reduces_cash(self, broker: SimulatedBroker) -> None:
        broker.buy("AAPL", 100)
        bar = _bar(open_=100.0)
        broker.process_bar(bar)
        assert broker.cash < 100_000.0

    def test_buy_increases_position(self, broker: SimulatedBroker) -> None:
        broker.buy("AAPL", 100)
        broker.process_bar(_bar(open_=100.0))
        assert broker.positions.get("AAPL").qty == 100

    def test_buy_fill_recorded(self, broker: SimulatedBroker) -> None:
        broker.buy("AAPL", 50)
        fills = broker.process_bar(_bar(open_=100.0))
        assert len(fills) == 1
        assert fills[0].qty == 50
        assert fills[0].side == OrderSide.BUY

    def test_buy_rejected_zero_qty(self, broker: SimulatedBroker) -> None:
        order = broker.buy("AAPL", 0)
        assert order.status == OrderStatus.REJECTED


class TestSellOrder:
    def test_sell_rejected_when_no_position(self, broker: SimulatedBroker) -> None:
        order = broker.sell("AAPL", 10)
        assert order.status == OrderStatus.REJECTED

    def test_sell_after_buy(self, broker: SimulatedBroker) -> None:
        broker.buy("AAPL", 100)
        broker.process_bar(_bar(open_=100.0, close=105.0))

        broker.sell("AAPL", 100)
        fills = broker.process_bar(_bar(open_=110.0, close=112.0))

        assert len(fills) == 1
        assert broker.positions.get("AAPL").qty == 0
        # 卖出后现金增加（110 * 100 - 买入成本）
        assert broker.cash > 100_000.0

    def test_sell_generates_realized_pnl(self, broker: SimulatedBroker) -> None:
        broker.buy("AAPL", 100)
        broker.process_bar(_bar(open_=100.0))

        broker.sell("AAPL", 100)
        fills = broker.process_bar(_bar(open_=110.0))

        assert fills[0].realized_pnl > 0


class TestPortfolioValue:
    def test_portfolio_value_equals_cash_initially(self, broker: SimulatedBroker) -> None:
        prices = {}
        assert broker.portfolio_value(prices) == 100_000.0

    def test_portfolio_value_after_buy(self, broker: SimulatedBroker) -> None:
        broker.buy("AAPL", 100)
        broker.process_bar(_bar(open_=100.0, close=120.0))
        prices = {"AAPL": 120.0}
        value = broker.portfolio_value(prices)
        # 持有 100 股 × 120 = 12000，加上剩余现金
        assert value > 99_000  # 扣除佣金后仍然接近初始值或更高


class TestNextBarFill:
    def test_orders_fill_on_next_bar_open(self, broker: SimulatedBroker) -> None:
        """挂单在下一根 bar 的 open 价格成交。"""
        broker.buy("AAPL", 10)
        fills = broker.process_bar(_bar(open_=200.0, close=210.0))
        assert fills[0].price == 200.0  # NoSlippage，直接用 open

    def test_orders_for_different_symbol_not_filled(self, broker: SimulatedBroker) -> None:
        broker.buy("MSFT", 10)
        fills = broker.process_bar(_bar(symbol="AAPL", open_=100.0))
        assert len(fills) == 0
        assert len(broker._pending) == 1
