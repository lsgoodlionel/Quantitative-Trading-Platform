"""做空与双向持仓 (K2) 单元测试

覆盖 docs/contracts/waveKa-order-position.md §3：
开空 → 加空 → 部分平 → 全平的 FIFO 成本与已实现盈亏，以及 A 股禁止做空。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from app.data.models import Bar, Frequency, Market
from app.engine.backtest.broker import SimulatedBroker
from app.engine.backtest.commission import CommissionModel, CommissionResult
from app.engine.backtest.engine import BacktestConfig, BacktestEngine
from app.engine.backtest.position import Position
from app.engine.backtest.roundtrips import build_round_trips
from app.engine.backtest.slippage import NoSlippage
from app.strategy.base import StrategyBase
from app.strategy.context import StrategyContext

BASE_TIME = datetime(2024, 1, 2, tzinfo=UTC)
INITIAL_CASH = 1_000_000.0


class ZeroCommission(CommissionModel):
    """零费用模型：让 FIFO 盈亏断言保持整洁。"""

    @property
    def market(self) -> Market:
        return Market.US

    def calculate(self, price: float, qty: int, direction: str) -> CommissionResult:
        return CommissionResult(commission=0.0, fees=0.0, total=0.0)


def _bar(open_: float, symbol: str = "AAPL", day_offset: int = 0) -> Bar:
    return Bar(
        time=BASE_TIME + timedelta(days=day_offset),
        symbol=symbol,
        market=Market.US,
        frequency=Frequency.DAY_1,
        open=open_,
        high=open_ * 1.05,
        low=open_ * 0.95,
        close=open_,
        volume=10_000,
    )


class _NoopStrategy(StrategyBase):
    name = "noop"

    def on_bar(self, ctx: StrategyContext) -> None:
        return None


def _ctx(broker: SimulatedBroker, bar: Bar) -> StrategyContext:
    return StrategyContext(
        bar=bar,
        history=pd.DataFrame({"close": [bar.close]}, index=[bar.time]),
        broker=broker,
    )


def _broker(allow_short: bool = True, **kwargs) -> SimulatedBroker:
    return SimulatedBroker(
        initial_cash=INITIAL_CASH,
        market=Market.US,
        commission_model=ZeroCommission(),
        slippage_model=NoSlippage(),
        allow_short=allow_short,
        **kwargs,
    )


# ── Position 双向队列 ────────────────────────────────────────────


class TestPositionDirection:
    def test_flat_position(self) -> None:
        assert Position(symbol="AAPL").direction == "flat"

    def test_long_direction(self) -> None:
        pos = Position(symbol="AAPL")
        pos.add(100, 10.0)
        assert pos.direction == "long"
        assert pos.qty == 100

    def test_short_direction_has_negative_qty(self) -> None:
        pos = Position(symbol="AAPL")
        pos.add_short(100, 10.0)
        assert pos.direction == "short"
        assert pos.qty == -100
        assert pos.short_qty == 100

    def test_short_avg_cost_is_fifo_weighted(self) -> None:
        # Arrange
        pos = Position(symbol="AAPL")
        pos.add_short(100, 100.0)
        pos.add_short(100, 110.0)
        # Assert
        assert pos.avg_cost == pytest.approx(105.0)

    def test_short_market_value_is_negative(self) -> None:
        pos = Position(symbol="AAPL")
        pos.add_short(100, 100.0)
        assert pos.market_value(90.0) == pytest.approx(-9_000.0)

    def test_short_unrealized_pnl_positive_when_price_drops(self) -> None:
        pos = Position(symbol="AAPL")
        pos.add_short(100, 100.0)
        assert pos.unrealized_pnl(90.0) == pytest.approx(1_000.0)


class TestShortFifo:
    def test_partial_cover_consumes_first_lot(self) -> None:
        # Arrange: 100@100 + 100@110
        pos = Position(symbol="AAPL")
        pos.add_short(100, 100.0)
        pos.add_short(100, 110.0)
        # Act: 平掉 150 @ 90
        realized = pos.reduce_short(150, 90.0)
        # Assert: 100*(100-90) + 50*(110-90) = 1000 + 1000
        assert realized == pytest.approx(2_000.0)
        assert pos.short_qty == 50
        assert pos.avg_cost == pytest.approx(110.0)

    def test_full_cover_returns_flat(self) -> None:
        pos = Position(symbol="AAPL")
        pos.add_short(100, 100.0)
        realized = pos.reduce_short(100, 80.0)
        assert realized == pytest.approx(2_000.0)
        assert pos.direction == "flat"
        assert pos.is_empty is True

    def test_cover_more_than_held_raises(self) -> None:
        pos = Position(symbol="AAPL")
        pos.add_short(100, 100.0)
        with pytest.raises(ValueError, match="Cannot cover"):
            pos.reduce_short(200, 90.0)

    def test_commission_reduces_short_pnl(self) -> None:
        pos = Position(symbol="AAPL")
        pos.add_short(100, 100.0, commission=50.0)   # 净收 99.5/股
        realized = pos.reduce_short(100, 90.0, commission=50.0)
        assert realized == pytest.approx(100 * (99.5 - 90.0 - 0.5))


# ── 券商层做空 ──────────────────────────────────────────────────


class TestBrokerShortGate:
    def test_short_rejected_when_disabled(self) -> None:
        broker = _broker(allow_short=False)
        order = broker.sell("AAPL", 100)
        assert order.status.value == "rejected"
        assert "insufficient position" in (order.reject_reason or "")

    def test_a_share_short_raises(self) -> None:
        with pytest.raises(ValueError, match="A股"):
            SimulatedBroker(initial_cash=INITIAL_CASH, market=Market.A, allow_short=True)

    def test_a_share_short_raises_through_engine_config(self) -> None:
        # 验收 §5.5：A 股市场下 allow_short=True 必须抛错，而不是静默做空
        bars = [_bar(10.0, symbol="600000.SH", day_offset=i) for i in range(3)]
        engine = BacktestEngine(
            BacktestConfig(initial_cash=INITIAL_CASH, market=Market.A, allow_short=True)
        )
        with pytest.raises(ValueError, match="A股"):
            engine.run(_NoopStrategy(), bars)

    def test_a_share_without_short_is_fine(self) -> None:
        broker = SimulatedBroker(initial_cash=INITIAL_CASH, market=Market.A)
        assert broker.positions.get("600000.SH").qty == 0

    def test_negative_borrow_rate_raises(self) -> None:
        with pytest.raises(ValueError, match="short_borrow_rate"):
            SimulatedBroker(initial_cash=INITIAL_CASH, market=Market.US, short_borrow_rate=-0.1)


class TestBrokerShortFlow:
    def test_open_short_creates_negative_position(self) -> None:
        # Arrange
        broker = _broker()
        broker.sell("AAPL", 100)
        # Act
        fills = broker.process_bar(_bar(100.0))
        # Assert
        assert len(fills) == 1
        assert broker.positions.get("AAPL").qty == -100
        assert fills[0].direction == "short"
        assert fills[0].is_close is False

    def test_short_proceeds_increase_cash(self) -> None:
        broker = _broker()
        broker.sell("AAPL", 100)
        broker.process_bar(_bar(100.0))
        assert broker.cash == pytest.approx(INITIAL_CASH + 10_000.0)

    def test_cover_realizes_pnl(self) -> None:
        # Arrange: 开空 100 @ 100
        broker = _broker()
        broker.sell("AAPL", 100)
        broker.process_bar(_bar(100.0))
        # Act: 平空 100 @ 80
        broker.buy("AAPL", 100)
        fills = broker.process_bar(_bar(80.0, day_offset=1))
        # Assert
        assert fills[0].realized_pnl == pytest.approx(2_000.0)
        assert fills[0].direction == "short"
        assert fills[0].is_close is True
        assert broker.positions.get("AAPL").direction == "flat"
        assert broker.cash == pytest.approx(INITIAL_CASH + 2_000.0)

    def test_add_partial_cover_full_cover(self) -> None:
        # Arrange: 开空 100@100，加空 100@110
        broker = _broker()
        broker.sell("AAPL", 100)
        broker.process_bar(_bar(100.0))
        broker.sell("AAPL", 100)
        broker.process_bar(_bar(110.0, day_offset=1))
        assert broker.positions.get("AAPL").qty == -200

        # Act: 部分平 150 @ 90
        broker.buy("AAPL", 150)
        fills = broker.process_bar(_bar(90.0, day_offset=2))
        # Assert: 100*(100-90) + 50*(110-90) = 2000
        assert fills[0].realized_pnl == pytest.approx(2_000.0)
        assert broker.positions.get("AAPL").qty == -50

        # Act: 全平剩余 50 @ 90 → 50*(110-90) = 1000
        broker.buy("AAPL", 50)
        fills2 = broker.process_bar(_bar(90.0, day_offset=3))
        assert fills2[0].realized_pnl == pytest.approx(1_000.0)
        assert broker.positions.get("AAPL").is_empty is True

    def test_sell_beyond_long_flips_to_short(self) -> None:
        # Arrange: 先买 100
        broker = _broker()
        broker.buy("AAPL", 100)
        broker.process_bar(_bar(100.0))
        # Act: 卖 150 → 平 100 多 + 开 50 空
        broker.sell("AAPL", 150)
        fills = broker.process_bar(_bar(120.0, day_offset=1))
        # Assert
        assert fills[0].realized_pnl == pytest.approx(2_000.0)
        assert broker.positions.get("AAPL").qty == -50
        assert fills[0].is_close is True
        assert fills[0].direction == "long"

    def test_buy_beyond_short_flips_to_long(self) -> None:
        broker = _broker()
        broker.sell("AAPL", 100)
        broker.process_bar(_bar(100.0))
        broker.buy("AAPL", 150)
        fills = broker.process_bar(_bar(90.0, day_offset=1))
        assert fills[0].realized_pnl == pytest.approx(1_000.0)
        assert broker.positions.get("AAPL").qty == 50

    def test_portfolio_value_counts_short_as_negative(self) -> None:
        broker = _broker()
        broker.sell("AAPL", 100)
        broker.process_bar(_bar(100.0))
        # 现金 1_010_000，空头市值 -10_000 → 组合价值回到初始
        assert broker.portfolio_value({"AAPL": 100.0}) == pytest.approx(INITIAL_CASH)
        # 价格跌到 80 → 空头浮盈 2000
        assert broker.portfolio_value({"AAPL": 80.0}) == pytest.approx(INITIAL_CASH + 2_000.0)


class TestBorrowFee:
    def test_no_fee_when_rate_zero(self) -> None:
        broker = _broker()
        broker.sell("AAPL", 100)
        broker.process_bar(_bar(100.0))
        cash_before = broker.cash
        broker.process_bar(_bar(100.0, day_offset=1))
        assert broker.cash == pytest.approx(cash_before)

    def test_fee_accrues_per_calendar_day(self) -> None:
        # Arrange: 年化 36.5% → 每天 0.1%
        broker = _broker(short_borrow_rate=0.365)
        broker.sell("AAPL", 100)
        broker.process_bar(_bar(100.0))
        cash_before = broker.cash
        # Act: 前进一个自然日
        broker.process_bar(_bar(100.0, day_offset=1))
        # Assert: 名义 10_000 × 0.365 / 365 × 1 天 = 10.0
        assert broker.cash == pytest.approx(cash_before - 10.0)


class TestStrategyContextShortApi:
    def test_short_opens_position(self) -> None:
        # Arrange
        broker = _broker()
        bar = _bar(100.0)
        # Act
        _ctx(broker, bar).short(100)
        broker.process_bar(bar)
        # Assert
        assert broker.positions.get("AAPL").direction == "short"

    def test_cover_closes_position(self) -> None:
        broker = _broker()
        bar0 = _bar(100.0)
        _ctx(broker, bar0).short(100)
        broker.process_bar(bar0)

        bar1 = _bar(80.0, day_offset=1)
        _ctx(broker, bar1).cover(100, exit_reason="take_profit")
        fills = broker.process_bar(bar1)
        assert broker.positions.get("AAPL").direction == "flat"
        assert fills[0].exit_reason == "take_profit"

    def test_close_all_handles_short(self) -> None:
        broker = _broker()
        bar0 = _bar(100.0)
        _ctx(broker, bar0).short(100)
        broker.process_bar(bar0)

        bar1 = _bar(90.0, day_offset=1)
        order = _ctx(broker, bar1).close_all()
        assert order is not None
        broker.process_bar(bar1)
        assert broker.positions.get("AAPL").is_empty is True

    def test_close_all_handles_long(self) -> None:
        broker = _broker()
        bar0 = _bar(100.0)
        _ctx(broker, bar0).buy(100)
        broker.process_bar(bar0)

        bar1 = _bar(110.0, day_offset=1)
        _ctx(broker, bar1).close_all()
        broker.process_bar(bar1)
        assert broker.positions.get("AAPL").is_empty is True

    def test_close_all_on_flat_returns_none(self) -> None:
        broker = _broker()
        assert _ctx(broker, _bar(100.0)).close_all() is None

    def test_sell_all_ignores_short_position(self) -> None:
        # sell_all 语义不变：只平多头，空头持仓下返回 None
        broker = _broker()
        bar = _bar(100.0)
        _ctx(broker, bar).short(100)
        broker.process_bar(bar)
        assert _ctx(broker, _bar(100.0, day_offset=1)).sell_all() is None

    def test_unknown_order_type_raises(self) -> None:
        broker = _broker()
        with pytest.raises(ValueError, match="未知的订单类型"):
            _ctx(broker, _bar(100.0)).buy(10, order_type="TWAP")


# ── 回合重构支持 short → cover ─────────────────────────────────


class TestShortRoundTrips:
    def test_short_cover_produces_short_trip(self) -> None:
        # Arrange
        fills = [
            {
                "side": "SELL", "qty": 100, "price": 100.0, "commission": 0.0,
                "realized_pnl": 0.0, "filled_at": "2024-01-02T00:00:00",
                "symbol": "AAPL", "entry_tag": "mean_rev", "exit_reason": None,
            },
            {
                "side": "BUY", "qty": 100, "price": 80.0, "commission": 0.0,
                "realized_pnl": 2_000.0, "filled_at": "2024-01-03T00:00:00",
                "symbol": "AAPL", "entry_tag": None, "exit_reason": "signal",
            },
        ]
        # Act
        trips = build_round_trips(fills)
        # Assert
        assert len(trips) == 1
        assert trips[0].direction == "short"
        assert trips[0].entry_price == pytest.approx(100.0)
        assert trips[0].exit_price == pytest.approx(80.0)
        assert trips[0].pnl == pytest.approx(2_000.0)
        assert trips[0].entry_tag == "mean_rev"

    def test_long_only_flow_unchanged(self) -> None:
        fills = [
            {
                "side": "BUY", "qty": 100, "price": 100.0, "commission": 1.0,
                "realized_pnl": 0.0, "filled_at": "2024-01-02T00:00:00",
                "symbol": "AAPL", "entry_tag": "trend", "exit_reason": None,
            },
            {
                "side": "SELL", "qty": 100, "price": 120.0, "commission": 1.0,
                "realized_pnl": 1_998.0, "filled_at": "2024-01-03T00:00:00",
                "symbol": "AAPL", "entry_tag": None, "exit_reason": "take_profit",
            },
        ]
        trips = build_round_trips(fills)
        assert len(trips) == 1
        assert trips[0].direction == "long"
        assert trips[0].pnl == pytest.approx(1_998.0)

    def test_flip_fill_produces_close_then_new_open(self) -> None:
        # 多 100 → 卖 150（平 100 多 + 开 50 空）→ 买 50 平空
        fills = [
            {
                "side": "BUY", "qty": 100, "price": 100.0, "commission": 0.0,
                "realized_pnl": 0.0, "filled_at": "2024-01-02T00:00:00", "symbol": "AAPL",
            },
            {
                "side": "SELL", "qty": 150, "price": 120.0, "commission": 0.0,
                "realized_pnl": 2_000.0, "filled_at": "2024-01-03T00:00:00", "symbol": "AAPL",
            },
            {
                "side": "BUY", "qty": 50, "price": 110.0, "commission": 0.0,
                "realized_pnl": 500.0, "filled_at": "2024-01-04T00:00:00", "symbol": "AAPL",
            },
        ]
        trips = build_round_trips(fills)
        assert len(trips) == 2
        assert trips[0].direction == "long"
        assert trips[0].pnl == pytest.approx(2_000.0)
        assert trips[1].direction == "short"
        assert trips[1].pnl == pytest.approx(500.0)
        # 不变量：回合盈亏之和 == 券商已实现盈亏之和
        assert sum(t.pnl for t in trips) == pytest.approx(2_500.0)
