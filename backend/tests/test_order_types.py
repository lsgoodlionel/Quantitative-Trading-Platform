"""订单类型体系 (K3) 单元测试

覆盖 docs/contracts/waveKa-order-position.md §2.3 撮合规则表：
每种 OrderType × (触发 / 未触发) × (BUY / SELL) 的成交价断言。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.data.models import Bar, Frequency, Market
from app.engine.backtest.broker import Order, OrderSide, OrderStatus, SimulatedBroker
from app.engine.backtest.commission import USCommissionModel
from app.engine.backtest.order_types import (
    OrderType,
    TimeInForce,
    match_order,
    trailing_trigger_price,
    validate_order_spec,
)
from app.engine.backtest.slippage import NoSlippage

BASE_TIME = datetime(2024, 1, 2, tzinfo=UTC)


def _bar(
    open_: float = 100.0,
    high: float = 110.0,
    low: float = 90.0,
    close: float = 105.0,
    symbol: str = "AAPL",
    day_offset: int = 0,
) -> Bar:
    return Bar(
        time=BASE_TIME + timedelta(days=day_offset),
        symbol=symbol,
        market=Market.US,
        frequency=Frequency.DAY_1,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=10_000,
    )


@pytest.fixture
def broker() -> SimulatedBroker:
    return SimulatedBroker(
        initial_cash=1_000_000.0,
        market=Market.US,
        commission_model=USCommissionModel(),
        slippage_model=NoSlippage(),
    )


# ── 纯函数层：match_order ────────────────────────────────────────


class TestMatchMarket:
    def test_market_fills_at_open(self) -> None:
        # Arrange / Act
        result = match_order(OrderType.MARKET, is_buy=True, bar=_bar(open_=100.0))
        # Assert
        assert result.filled is True
        assert result.price == 100.0

    def test_market_on_open_same_as_market(self) -> None:
        result = match_order(OrderType.MARKET_ON_OPEN, is_buy=False, bar=_bar(open_=100.0))
        assert result.filled is True
        assert result.price == 100.0

    def test_market_on_close_fills_at_close(self) -> None:
        result = match_order(OrderType.MARKET_ON_CLOSE, is_buy=True, bar=_bar(close=105.0))
        assert result.filled is True
        assert result.price == 105.0


class TestMatchLimit:
    def test_buy_limit_triggers_when_low_reaches_limit(self) -> None:
        # 开盘 100，limit 95，最低 90 → 触及，成交价 min(100, 95) = 95
        result = match_order(
            OrderType.LIMIT, is_buy=True, bar=_bar(open_=100.0, low=90.0), limit_price=95.0
        )
        assert result.filled is True
        assert result.price == 95.0

    def test_buy_limit_fills_at_open_when_open_below_limit(self) -> None:
        # 跳空低开：开盘 92 已优于 limit 95 → 以开盘价成交
        result = match_order(
            OrderType.LIMIT, is_buy=True, bar=_bar(open_=92.0, low=90.0), limit_price=95.0
        )
        assert result.price == 92.0

    def test_buy_limit_not_triggered(self) -> None:
        result = match_order(
            OrderType.LIMIT, is_buy=True, bar=_bar(open_=100.0, low=98.0), limit_price=95.0
        )
        assert result.filled is False
        assert result.price is None

    def test_sell_limit_triggers_when_high_reaches_limit(self) -> None:
        result = match_order(
            OrderType.LIMIT, is_buy=False, bar=_bar(open_=100.0, high=110.0), limit_price=105.0
        )
        assert result.filled is True
        assert result.price == 105.0

    def test_sell_limit_fills_at_open_when_open_above_limit(self) -> None:
        result = match_order(
            OrderType.LIMIT, is_buy=False, bar=_bar(open_=108.0, high=110.0), limit_price=105.0
        )
        assert result.price == 108.0

    def test_sell_limit_not_triggered(self) -> None:
        result = match_order(
            OrderType.LIMIT, is_buy=False, bar=_bar(open_=100.0, high=102.0), limit_price=105.0
        )
        assert result.filled is False


class TestMatchStopMarket:
    def test_buy_stop_triggers_when_high_reaches_stop(self) -> None:
        result = match_order(
            OrderType.STOP_MARKET, is_buy=True, bar=_bar(open_=100.0, high=110.0), stop_price=105.0
        )
        assert result.filled is True
        assert result.price == 105.0

    def test_buy_stop_gap_up_fills_at_open(self) -> None:
        # 跳空高开越过 stop → 以更不利的开盘价成交
        result = match_order(
            OrderType.STOP_MARKET, is_buy=True, bar=_bar(open_=108.0, high=110.0), stop_price=105.0
        )
        assert result.price == 108.0

    def test_buy_stop_not_triggered(self) -> None:
        result = match_order(
            OrderType.STOP_MARKET, is_buy=True, bar=_bar(open_=100.0, high=102.0), stop_price=105.0
        )
        assert result.filled is False

    def test_sell_stop_triggers_when_low_reaches_stop(self) -> None:
        result = match_order(
            OrderType.STOP_MARKET, is_buy=False, bar=_bar(open_=100.0, low=90.0), stop_price=95.0
        )
        assert result.filled is True
        assert result.price == 95.0

    def test_sell_stop_gap_down_fills_at_open(self) -> None:
        result = match_order(
            OrderType.STOP_MARKET, is_buy=False, bar=_bar(open_=92.0, low=90.0), stop_price=95.0
        )
        assert result.price == 92.0

    def test_sell_stop_not_triggered(self) -> None:
        result = match_order(
            OrderType.STOP_MARKET, is_buy=False, bar=_bar(open_=100.0, low=98.0), stop_price=95.0
        )
        assert result.filled is False


class TestMatchStopLimit:
    def test_triggers_and_fills_in_same_bar(self) -> None:
        # BUY stop 105 触发后按 limit 107 撮合；bar low 90 <= 107 → 成交 min(100,107)=100
        result = match_order(
            OrderType.STOP_LIMIT,
            is_buy=True,
            bar=_bar(open_=100.0, high=110.0, low=90.0),
            stop_price=105.0,
            limit_price=107.0,
        )
        assert result.triggered is True
        assert result.filled is True
        assert result.price == 100.0

    def test_triggers_but_limit_not_reached(self) -> None:
        # SELL stop 95 触发；limit 120 但最高只有 110 → 触发但未成交
        result = match_order(
            OrderType.STOP_LIMIT,
            is_buy=False,
            bar=_bar(open_=100.0, high=110.0, low=90.0),
            stop_price=95.0,
            limit_price=120.0,
        )
        assert result.triggered is True
        assert result.filled is False

    def test_not_triggered_stays_untriggered(self) -> None:
        result = match_order(
            OrderType.STOP_LIMIT,
            is_buy=True,
            bar=_bar(open_=100.0, high=101.0, low=99.0),
            stop_price=105.0,
            limit_price=107.0,
        )
        assert result.triggered is False
        assert result.filled is False

    def test_already_triggered_skips_stop_check(self) -> None:
        # 上一根 bar 已触发：本根只做 limit 判定
        result = match_order(
            OrderType.STOP_LIMIT,
            is_buy=True,
            bar=_bar(open_=100.0, high=101.0, low=94.0),
            stop_price=105.0,
            limit_price=95.0,
            triggered=True,
        )
        assert result.filled is True
        assert result.price == 95.0


class TestTrailingTriggerPrice:
    def test_sell_trigger_below_extreme(self) -> None:
        assert trailing_trigger_price(100.0, 0.05, is_buy=False) == pytest.approx(95.0)

    def test_buy_trigger_above_extreme(self) -> None:
        assert trailing_trigger_price(100.0, 0.05, is_buy=True) == pytest.approx(105.0)


class TestValidateOrderSpec:
    def test_limit_requires_limit_price(self) -> None:
        assert validate_order_spec(OrderType.LIMIT, None, None, None) is not None

    def test_stop_requires_stop_price(self) -> None:
        assert validate_order_spec(OrderType.STOP_MARKET, None, None, None) is not None

    def test_stop_limit_requires_both(self) -> None:
        assert validate_order_spec(OrderType.STOP_LIMIT, 10.0, None, None) is not None

    def test_trailing_requires_positive_pct(self) -> None:
        assert validate_order_spec(OrderType.TRAILING_STOP, None, None, 0.0) is not None

    def test_market_needs_nothing(self) -> None:
        assert validate_order_spec(OrderType.MARKET, None, None, None) is None

    def test_valid_limit_passes(self) -> None:
        assert validate_order_spec(OrderType.LIMIT, 100.0, None, None) is None

    def test_valid_trailing_passes(self) -> None:
        assert validate_order_spec(OrderType.TRAILING_STOP, None, None, 0.05) is None


# ── 券商层：端到端撮合 ─────────────────────────────────────────


class TestBrokerOrderTypes:
    def test_default_order_type_is_market(self, broker: SimulatedBroker) -> None:
        order = broker.buy("AAPL", 10)
        assert order.order_type == OrderType.MARKET
        assert order.time_in_force == TimeInForce.GTC

    def test_market_fills_at_open(self, broker: SimulatedBroker) -> None:
        broker.buy("AAPL", 10)
        fills = broker.process_bar(_bar(open_=100.0))
        assert len(fills) == 1
        assert fills[0].price == 100.0

    def test_market_on_close_fills_at_close(self, broker: SimulatedBroker) -> None:
        broker.submit_order(
            Order(
                symbol="AAPL",
                market=Market.US,
                side=OrderSide.BUY,
                qty=10,
                order_type=OrderType.MARKET_ON_CLOSE,
            )
        )
        fills = broker.process_bar(_bar(open_=100.0, close=105.0))
        assert fills[0].price == 105.0

    def test_limit_buy_stays_pending_until_touched(self, broker: SimulatedBroker) -> None:
        order = broker.submit_order(
            Order(
                symbol="AAPL",
                market=Market.US,
                side=OrderSide.BUY,
                qty=10,
                order_type=OrderType.LIMIT,
                limit_price=95.0,
            )
        )
        # 第一根 bar 最低 98 未触及
        assert broker.process_bar(_bar(open_=100.0, high=101.0, low=98.0)) == []
        assert order.status == OrderStatus.PENDING
        # 第二根 bar 最低 90 触及
        fills = broker.process_bar(_bar(open_=99.0, high=100.0, low=90.0, day_offset=1))
        assert len(fills) == 1
        assert fills[0].price == 95.0
        assert order.status == OrderStatus.FILLED

    def test_limit_order_missing_price_is_rejected(self, broker: SimulatedBroker) -> None:
        order = broker.submit_order(
            Order(
                symbol="AAPL",
                market=Market.US,
                side=OrderSide.BUY,
                qty=10,
                order_type=OrderType.LIMIT,
            )
        )
        assert order.status == OrderStatus.REJECTED
        assert order.reject_reason is not None

    def test_stop_limit_two_phase_across_bars(self, broker: SimulatedBroker) -> None:
        order = broker.submit_order(
            Order(
                symbol="AAPL",
                market=Market.US,
                side=OrderSide.BUY,
                qty=10,
                order_type=OrderType.STOP_LIMIT,
                stop_price=105.0,
                limit_price=101.0,
            )
        )
        # bar1: 触发（high 110 >= 105）但 limit 101 未触及（low 106）
        assert broker.process_bar(_bar(open_=106.0, high=110.0, low=106.0)) == []
        assert order._triggered is True
        # bar2: 已触发，low 100 <= 101 → 成交 min(open=102, 101) = 101
        fills = broker.process_bar(_bar(open_=102.0, high=103.0, low=100.0, day_offset=1))
        assert fills[0].price == 101.0

    def test_trailing_stop_follows_extreme(self, broker: SimulatedBroker) -> None:
        broker.buy("AAPL", 100)
        broker.process_bar(_bar(open_=100.0, high=100.0, low=100.0, close=100.0))
        order = broker.submit_order(
            Order(
                symbol="AAPL",
                market=Market.US,
                side=OrderSide.SELL,
                qty=100,
                order_type=OrderType.TRAILING_STOP,
                trailing_pct=0.10,
            )
        )
        # bar2: 开盘 100 → 极值种子 100，触发线 90；最低 95 未触发；收盘时极值升到 120
        assert broker.process_bar(
            _bar(open_=100.0, high=120.0, low=95.0, day_offset=1)
        ) == []
        assert order._extreme_price == pytest.approx(120.0)
        # bar3: 触发线 108；最低 100 <= 108 → 成交 min(open=110, 108) = 108
        fills = broker.process_bar(_bar(open_=110.0, high=115.0, low=100.0, day_offset=2))
        assert len(fills) == 1
        assert fills[0].price == pytest.approx(108.0)

    def test_trailing_stop_buy_side_tracks_minimum(self, broker: SimulatedBroker) -> None:
        broker_short = SimulatedBroker(
            initial_cash=1_000_000.0,
            market=Market.US,
            commission_model=USCommissionModel(),
            slippage_model=NoSlippage(),
            allow_short=True,
        )
        broker_short.sell("AAPL", 100)
        broker_short.process_bar(_bar(open_=100.0, high=100.0, low=100.0, close=100.0))
        order = broker_short.submit_order(
            Order(
                symbol="AAPL",
                market=Market.US,
                side=OrderSide.BUY,
                qty=100,
                order_type=OrderType.TRAILING_STOP,
                trailing_pct=0.10,
            )
        )
        # bar2: 种子 100 → 触发线 110；最高 105 未触发；极值降到 80
        assert broker_short.process_bar(
            _bar(open_=100.0, high=105.0, low=80.0, day_offset=1)
        ) == []
        assert order._extreme_price == pytest.approx(80.0)
        # bar3: 触发线 88；最高 95 >= 88 → 成交 max(open=85, 88) = 88
        fills = broker_short.process_bar(_bar(open_=85.0, high=95.0, low=84.0, day_offset=2))
        assert fills[0].price == pytest.approx(88.0)


# ── LIMIT_IF_TOUCHED（Wave L-a 补做 Wave K 契约疏漏）─────────────
#
# 契约 docs/contracts/waveLa-trading-controls.md §五：
# 与 STOP_LIMIT 互为镜像 —— 同样是「触发 → 转限价」两段结构，
# 只把触发方向的比较符反过来。成交价与 LIMIT 一致，不得用触发价直接成交。


class TestLimitIfTouchedMatching:
    """纯函数层：BUY / SELL × 触发 / 未触发 × 成交价。"""

    def test_buy_not_triggered_when_price_stays_above_trigger(self) -> None:
        # 回落抄底：最低 96 > 触发价 95 → 没碰到，不触发
        result = match_order(
            OrderType.LIMIT_IF_TOUCHED,
            is_buy=True,
            bar=_bar(open_=100.0, high=105.0, low=96.0),
            trigger_price=95.0,
            limit_price=94.0,
        )

        assert result.filled is False
        assert result.triggered is False

    def test_buy_triggered_and_filled_at_limit(self) -> None:
        # 最低 93 <= 触发价 95 → 触发；limit 94 已触及 → 成交 min(open=100, 94) = 94
        result = match_order(
            OrderType.LIMIT_IF_TOUCHED,
            is_buy=True,
            bar=_bar(open_=100.0, high=105.0, low=93.0),
            trigger_price=95.0,
            limit_price=94.0,
        )

        assert result.filled is True
        assert result.triggered is True
        assert result.price == pytest.approx(94.0)

    def test_buy_never_fills_at_trigger_price(self) -> None:
        """触发但限价未触及时不得成交 —— 拿触发价成交就是偷看。"""
        result = match_order(
            OrderType.LIMIT_IF_TOUCHED,
            is_buy=True,
            bar=_bar(open_=100.0, high=105.0, low=95.0),
            trigger_price=95.0,
            limit_price=90.0,
        )

        assert result.filled is False
        assert result.triggered is True

    def test_buy_gap_down_fills_at_open_not_limit(self) -> None:
        """跳空低开越过限价时按开盘价成交，不白拿限价的便宜。"""
        result = match_order(
            OrderType.LIMIT_IF_TOUCHED,
            is_buy=True,
            bar=_bar(open_=90.0, high=92.0, low=88.0),
            trigger_price=95.0,
            limit_price=94.0,
        )

        assert result.price == pytest.approx(90.0)

    def test_sell_not_triggered_when_price_stays_below_trigger(self) -> None:
        # 反弹摸顶：最高 104 < 触发价 105 → 不触发
        result = match_order(
            OrderType.LIMIT_IF_TOUCHED,
            is_buy=False,
            bar=_bar(open_=100.0, high=104.0, low=95.0),
            trigger_price=105.0,
            limit_price=106.0,
        )

        assert result.filled is False
        assert result.triggered is False

    def test_sell_triggered_and_filled_at_limit(self) -> None:
        # 最高 107 >= 触发价 105 → 触发；limit 106 已触及 → 成交 max(open=100, 106) = 106
        result = match_order(
            OrderType.LIMIT_IF_TOUCHED,
            is_buy=False,
            bar=_bar(open_=100.0, high=107.0, low=95.0),
            trigger_price=105.0,
            limit_price=106.0,
        )

        assert result.filled is True
        assert result.price == pytest.approx(106.0)

    def test_sell_gap_up_fills_at_open(self) -> None:
        result = match_order(
            OrderType.LIMIT_IF_TOUCHED,
            is_buy=False,
            bar=_bar(open_=110.0, high=112.0, low=108.0),
            trigger_price=105.0,
            limit_price=106.0,
        )

        assert result.price == pytest.approx(110.0)

    def test_is_mirror_image_of_stop_limit(self) -> None:
        """同一根 bar、同样的价位：STOP_LIMIT 不触发时 LIMIT_IF_TOUCHED 触发。"""
        bar = _bar(open_=100.0, high=101.0, low=93.0)

        stop_limit = match_order(
            OrderType.STOP_LIMIT, is_buy=True, bar=bar,
            stop_price=105.0, limit_price=99.0,
        )
        limit_if_touched = match_order(
            OrderType.LIMIT_IF_TOUCHED, is_buy=True, bar=bar,
            trigger_price=95.0, limit_price=99.0,
        )

        assert stop_limit.triggered is False
        assert limit_if_touched.triggered is True

    def test_missing_trigger_price_raises(self) -> None:
        with pytest.raises(ValueError, match="trigger_price"):
            match_order(
                OrderType.LIMIT_IF_TOUCHED,
                is_buy=True,
                bar=_bar(),
                limit_price=95.0,
            )


class TestLimitIfTouchedSpec:
    def test_requires_trigger_price(self) -> None:
        assert (
            validate_order_spec(OrderType.LIMIT_IF_TOUCHED, 100.0, None, None, None)
            is not None
        )

    def test_requires_limit_price(self) -> None:
        assert (
            validate_order_spec(OrderType.LIMIT_IF_TOUCHED, None, None, None, 95.0)
            is not None
        )

    def test_rejects_non_positive_trigger_price(self) -> None:
        assert (
            validate_order_spec(OrderType.LIMIT_IF_TOUCHED, 100.0, None, None, 0.0)
            is not None
        )

    def test_valid_spec_passes(self) -> None:
        assert (
            validate_order_spec(OrderType.LIMIT_IF_TOUCHED, 94.0, None, None, 95.0)
            is None
        )

    def test_other_types_unaffected_by_new_param(self) -> None:
        """既有四参数调用点行为不变。"""
        assert validate_order_spec(OrderType.MARKET, None, None, None) is None
        assert validate_order_spec(OrderType.LIMIT, 100.0, None, None) is None


class TestLimitIfTouchedBroker:
    """券商层：两段状态机跨 bar 的端到端行为。"""

    def test_two_phase_across_bars(self, broker: SimulatedBroker) -> None:
        order = broker.submit_order(
            Order(
                symbol="AAPL",
                market=Market.US,
                side=OrderSide.BUY,
                qty=10,
                order_type=OrderType.LIMIT_IF_TOUCHED,
                trigger_price=95.0,
                limit_price=90.0,
            )
        )
        # bar1: 触发（low 94 <= 95），但限价 90 未触及
        assert broker.process_bar(_bar(open_=100.0, high=105.0, low=94.0)) == []
        assert order._triggered is True

        # bar2: 已触发，low 88 <= 90 → 成交 min(open=92, 90) = 90
        fills = broker.process_bar(
            _bar(open_=92.0, high=93.0, low=88.0, day_offset=1)
        )
        assert len(fills) == 1
        assert fills[0].price == pytest.approx(90.0)

    def test_stays_pending_when_never_touched(self, broker: SimulatedBroker) -> None:
        order = broker.submit_order(
            Order(
                symbol="AAPL",
                market=Market.US,
                side=OrderSide.BUY,
                qty=10,
                order_type=OrderType.LIMIT_IF_TOUCHED,
                trigger_price=80.0,
                limit_price=79.0,
            )
        )

        assert broker.process_bar(_bar(open_=100.0, high=105.0, low=95.0)) == []
        assert order._triggered is False
        assert order.status is OrderStatus.PENDING

    def test_rejects_order_without_trigger_price(self, broker: SimulatedBroker) -> None:
        order = broker.submit_order(
            Order(
                symbol="AAPL",
                market=Market.US,
                side=OrderSide.BUY,
                qty=10,
                order_type=OrderType.LIMIT_IF_TOUCHED,
                limit_price=90.0,
            )
        )

        assert order.status is OrderStatus.REJECTED
        assert order.reject_reason is not None
        assert "trigger_price" in order.reject_reason

    def test_sell_side_end_to_end(self, broker: SimulatedBroker) -> None:
        broker.buy("AAPL", 100)
        broker.process_bar(_bar(open_=100.0, high=100.0, low=100.0, close=100.0))
        broker.submit_order(
            Order(
                symbol="AAPL",
                market=Market.US,
                side=OrderSide.SELL,
                qty=100,
                order_type=OrderType.LIMIT_IF_TOUCHED,
                trigger_price=105.0,
                limit_price=108.0,
            )
        )

        # 最高 110 >= 105 触发；limit 108 已触及 → 成交 max(open=104, 108) = 108
        fills = broker.process_bar(
            _bar(open_=104.0, high=110.0, low=103.0, day_offset=1)
        )

        assert len(fills) == 1
        assert fills[0].price == pytest.approx(108.0)
