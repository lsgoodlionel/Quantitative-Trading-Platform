"""TimeInForce (K3 §2.4) 单元测试

DAY / GTD 过期撤单：产生 OrderStatus.CANCELLED，且不产生 Fill。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.data.models import Bar, Frequency, Market
from app.engine.backtest.broker import Order, OrderSide, OrderStatus, SimulatedBroker
from app.engine.backtest.commission import USCommissionModel
from app.engine.backtest.order_types import OrderType, TimeInForce
from app.engine.backtest.slippage import NoSlippage

BASE_TIME = datetime(2024, 1, 2, 9, 30, tzinfo=UTC)


def _bar(day_offset: int = 0, hours: int = 0, open_: float = 100.0) -> Bar:
    return Bar(
        time=BASE_TIME + timedelta(days=day_offset, hours=hours),
        symbol="AAPL",
        market=Market.US,
        frequency=Frequency.DAY_1,
        open=open_,
        high=open_ + 1.0,
        low=open_ - 1.0,
        close=open_,
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


def _limit_buy(
    broker: SimulatedBroker,
    tif: TimeInForce,
    limit_price: float = 1.0,
    good_till_date: datetime | None = None,
) -> Order:
    """挂一个远离市价、永远不会成交的限价买单，用于观察过期行为。"""
    return broker.submit_order(
        Order(
            symbol="AAPL",
            market=Market.US,
            side=OrderSide.BUY,
            qty=10,
            order_type=OrderType.LIMIT,
            limit_price=limit_price,
            time_in_force=tif,
            good_till_date=good_till_date,
        )
    )


class TestGtcDefault:
    def test_default_is_gtc(self, broker: SimulatedBroker) -> None:
        order = broker.buy("AAPL", 10)
        assert order.time_in_force == TimeInForce.GTC

    def test_gtc_survives_many_days(self, broker: SimulatedBroker) -> None:
        order = _limit_buy(broker, TimeInForce.GTC)
        for day in range(5):
            broker.process_bar(_bar(day_offset=day))
        assert order.status == OrderStatus.PENDING


class TestDayOrder:
    def test_day_order_cancelled_on_next_day(self, broker: SimulatedBroker) -> None:
        # Arrange: 第 0 天挂单（先跑一根 bar 让券商知道当前交易日）
        broker.process_bar(_bar(day_offset=0))
        order = _limit_buy(broker, TimeInForce.DAY)
        # Act: 同日再来一根 bar → 不过期
        broker.process_bar(_bar(day_offset=0, hours=1))
        assert order.status == OrderStatus.PENDING
        # Act: 次日 bar → 过期撤单
        broker.process_bar(_bar(day_offset=1))
        # Assert
        assert order.status == OrderStatus.CANCELLED

    def test_cancelled_day_order_produces_no_fill(self, broker: SimulatedBroker) -> None:
        broker.process_bar(_bar(day_offset=0))
        # 限价 0.01 永远触不到 → 次日撤单，且不产生任何 Fill
        order = _limit_buy(broker, TimeInForce.DAY, limit_price=0.01)
        broker.process_bar(_bar(day_offset=1))
        assert order.status == OrderStatus.CANCELLED
        assert broker.fills == []

    def test_day_order_can_still_fill_before_expiry(self, broker: SimulatedBroker) -> None:
        broker.process_bar(_bar(day_offset=0))
        order = _limit_buy(broker, TimeInForce.DAY, limit_price=999.0)
        fills = broker.process_bar(_bar(day_offset=1))
        # 撮合先于过期检查：仍然成交
        assert len(fills) == 1
        assert order.status == OrderStatus.FILLED


class TestGtdOrder:
    def test_gtd_survives_until_date(self, broker: SimulatedBroker) -> None:
        broker.process_bar(_bar(day_offset=0))
        order = _limit_buy(
            broker,
            TimeInForce.GTD,
            good_till_date=BASE_TIME + timedelta(days=2),
        )
        broker.process_bar(_bar(day_offset=1))
        assert order.status == OrderStatus.PENDING
        broker.process_bar(_bar(day_offset=2))
        assert order.status == OrderStatus.PENDING

    def test_gtd_cancelled_after_date(self, broker: SimulatedBroker) -> None:
        broker.process_bar(_bar(day_offset=0))
        order = _limit_buy(
            broker,
            TimeInForce.GTD,
            good_till_date=BASE_TIME + timedelta(days=2),
        )
        broker.process_bar(_bar(day_offset=3))
        assert order.status == OrderStatus.CANCELLED

    def test_gtd_without_date_behaves_like_gtc(self, broker: SimulatedBroker) -> None:
        broker.process_bar(_bar(day_offset=0))
        order = _limit_buy(broker, TimeInForce.GTD)
        broker.process_bar(_bar(day_offset=10))
        assert order.status == OrderStatus.PENDING
