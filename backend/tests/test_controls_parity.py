"""回测 / 实盘控制器一致性 — Wave L-a / L1 的**核心用例**

契约 docs/contracts/waveLa-trading-controls.md §六 验收 3。

本契约的价值不在「多几个控制器」，而在「同一个控制器实例在回测与实盘给出
相同判定」。因此这里不测控制器内部逻辑（那是 test_trading_controls.py 的事），
只测两件事：

1. 两条路径构造出的 `ControlContext` 在控制器实际读取的字段上完全等价；
2. 同一个控制器实例，喂等价状态，两条路径的拒单结论与拒单原因逐字一致。

一旦有人给控制器塞进 broker/OMS 引用、或在某一侧另写一套判定，这些用例会立刻红。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.data.models import Bar, Frequency, Market
from app.engine.backtest.broker import Order, OrderSide, OrderStatus, SimulatedBroker
from app.engine.backtest.commission import USCommissionModel
from app.engine.backtest.slippage import NoSlippage
from app.engine.controls import (
    ControlContext,
    LongOnly,
    MaxOrderSize,
    MaxPositionSize,
    RestrictedList,
)
from app.gateway.paper_gateway import PaperGateway
from app.oms.manager import OrderManager
from app.oms.order import LiveOrder, LiveOrderSide, LiveOrderStatus, LiveOrderType

BASE_TIME = datetime(2024, 1, 2, tzinfo=UTC)
SYMBOL = "AAPL"
REFERENCE_PRICE = 100.0

#: 控制器实际读取的字段 —— 两条路径必须在这些字段上等价。
#: `orders_today` 不在其中：回测按 **bar 日期** 归零、实盘按 **UTC 自然日** 归零，
#: 两个时钟在实盘运行时重合、在测试夹具里不重合。它由
#: `test_order_count_parity_within_one_day` 单独在同一天内比对。
CONTROL_INPUT_FIELDS = (
    "symbol", "side", "qty", "order_type", "limit_price", "current_qty",
)


def _bar(day_offset: int = 0, close: float = REFERENCE_PRICE) -> Bar:
    return Bar(
        time=BASE_TIME + timedelta(days=day_offset),
        symbol=SYMBOL,
        market=Market.US,
        frequency=Frequency.DAY_1,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000_000,
    )


def _control_inputs(ctx: ControlContext) -> dict:
    """摘出控制器会读到的字段，外加参考价（属性而非字段）。"""
    snapshot = {name: getattr(ctx, name) for name in CONTROL_INPUT_FIELDS}
    snapshot["reference_price"] = ctx.reference_price
    return snapshot


# ── 回测侧场景搭建 ───────────────────────────────────────────────


def _backtest_broker(current_qty: int = 0, allow_short: bool = False) -> SimulatedBroker:
    """
    造一个「已持有 current_qty 股、最后收盘价 100」的券商。

    建仓单在装控制器**之前**下，避免搭台阶的单被待测控制器拦掉。
    """
    broker = SimulatedBroker(
        initial_cash=1_000_000.0,
        market=Market.US,
        commission_model=USCommissionModel(),
        slippage_model=NoSlippage(),
        allow_short=allow_short,
    )
    broker.process_bar(_bar(day_offset=0))
    if current_qty > 0:
        broker.buy(SYMBOL, current_qty)
    broker.process_bar(_bar(day_offset=1))
    return broker


def _backtest_verdict(
    controls, side: str, qty: int, setup_qty: int = 0, allow_short: bool = False
) -> str | None:
    """回测路径的判定：返回拒单原因，None 表示放行。"""
    broker = _backtest_broker(current_qty=setup_qty, allow_short=allow_short)
    broker.set_controls(controls)
    order = broker.buy(SYMBOL, qty) if side == "BUY" else broker.sell(SYMBOL, qty)
    if order.status is OrderStatus.REJECTED:
        return order.reject_reason
    return None


# ── 实盘侧场景搭建 ───────────────────────────────────────────────


async def _live_manager(current_qty: int = 0) -> OrderManager:
    """造一个与 `_backtest_broker` 等价的 OMS：同样的持仓、同样的当日单数。"""
    manager = OrderManager(redis_client=None)
    gateway = PaperGateway(market="US", initial_cash=1_000_000.0, currency="USD")
    await gateway.connect()
    manager.register_gateway("US", gateway)
    manager.set_price_lookup(lambda symbol, market: REFERENCE_PRICE)

    if current_qty > 0:
        order = await manager.submit_order(
            symbol=SYMBOL, market="US", side=LiveOrderSide.BUY, qty=current_qty
        )
        assert order.status is LiveOrderStatus.FILLED, "建仓单必须成交，否则持仓不等价"
    return manager


async def _live_verdict(
    controls, side: str, qty: int, setup_qty: int = 0
) -> str | None:
    """实盘路径的判定：返回拒单原因，None 表示放行。"""
    manager = await _live_manager(current_qty=setup_qty)
    manager.set_controls(controls)
    order = await manager.submit_order(
        symbol=SYMBOL,
        market="US",
        side=LiveOrderSide.BUY if side == "BUY" else LiveOrderSide.SELL,
        qty=qty,
    )
    if order.status is LiveOrderStatus.REJECTED:
        return order.reject_reason
    return None


# ── 1. Context 等价性 ────────────────────────────────────────────


async def test_control_contexts_are_equivalent_for_buy():
    # Arrange：两侧都是「空仓 + 当日已下 0 单 + 参考价 100」
    broker = _backtest_broker(current_qty=0)
    manager = await _live_manager(current_qty=0)

    # Act
    backtest_ctx = broker._control_context(
        Order(symbol=SYMBOL, market=Market.US, side=OrderSide.BUY, qty=100)
    )
    live_ctx = manager._control_context(
        _live_order(LiveOrderSide.BUY, 100)
    )

    # Assert
    assert _control_inputs(backtest_ctx) == _control_inputs(live_ctx)


async def test_control_contexts_are_equivalent_with_position():
    broker = _backtest_broker(current_qty=200)
    manager = await _live_manager(current_qty=200)

    backtest_ctx = broker._control_context(
        Order(symbol=SYMBOL, market=Market.US, side=OrderSide.SELL, qty=50)
    )
    live_ctx = manager._control_context(_live_order(LiveOrderSide.SELL, 50))

    assert backtest_ctx.current_qty == 200
    assert _control_inputs(backtest_ctx) == _control_inputs(live_ctx)


def _live_order(side: LiveOrderSide, qty: int) -> LiveOrder:
    return LiveOrder(
        symbol=SYMBOL,
        market="US",
        side=side,
        qty=qty,
        order_type=LiveOrderType.MARKET,
    )


# ── 2. 端到端判定一致性（契约点名的 4 个控制器）────────────────


@pytest.mark.parametrize(
    ("label", "make_controls", "side", "qty", "setup_qty", "allow_short"),
    [
        ("MaxOrderSize 放行", lambda: [MaxOrderSize(max_shares=100)], "BUY", 100, 0, False),
        ("MaxOrderSize 拦截", lambda: [MaxOrderSize(max_shares=100)], "BUY", 101, 0, False),
        (
            "MaxOrderSize 金额拦截",
            lambda: [MaxOrderSize(max_notional=10_000.0)], "BUY", 101, 0, False,
        ),
        (
            "MaxPositionSize 放行（含底仓）",
            lambda: [MaxPositionSize(max_shares=500)], "BUY", 300, 200, False,
        ),
        (
            "MaxPositionSize 拦截（含底仓）",
            lambda: [MaxPositionSize(max_shares=500)], "BUY", 301, 200, False,
        ),
        (
            "MaxPositionSize 金额拦截",
            lambda: [MaxPositionSize(max_notional=50_000.0)], "BUY", 501, 0, False,
        ),
        ("LongOnly 放行平仓", lambda: [LongOnly()], "SELL", 100, 100, False),
        ("LongOnly 放行减仓", lambda: [LongOnly()], "SELL", 40, 100, False),
        # 开空场景必须给回测侧 allow_short=True：否则券商自带的持仓校验会先于
        # 控制器拒单，比的就不是控制器了。这也正是「LongOnly 与 allow_short 正交」。
        ("LongOnly 拦截开空", lambda: [LongOnly()], "SELL", 100, 0, True),
        ("LongOnly 拦截超卖", lambda: [LongOnly()], "SELL", 150, 100, True),
        ("RestrictedList 拦截", lambda: [RestrictedList({SYMBOL})], "BUY", 10, 0, False),
        ("RestrictedList 放行", lambda: [RestrictedList({"TSLA"})], "BUY", 10, 0, False),
        (
            "组合短路",
            lambda: [RestrictedList({SYMBOL}), MaxOrderSize(max_shares=1)],
            "BUY", 10, 0, False,
        ),
    ],
)
async def test_backtest_and_live_agree(
    label, make_controls, side, qty, setup_qty, allow_short
):
    """同一批控制器实例喂给两条路径，拒单结论与原因必须逐字一致。"""
    shared_controls = make_controls()

    backtest = _backtest_verdict(
        shared_controls, side, qty, setup_qty=setup_qty, allow_short=allow_short
    )
    live = await _live_verdict(shared_controls, side, qty, setup_qty=setup_qty)

    assert backtest == live, f"{label}: 回测={backtest!r} 实盘={live!r}"


async def test_order_count_parity_within_one_day():
    """
    `MaxOrderCount` 的计数口径：回测按 bar 日期、实盘按 UTC 自然日归零。
    实盘运行时两个时钟重合，因此同一天内两侧的当日单数必须相同。
    """
    broker = SimulatedBroker(
        initial_cash=1_000_000.0,
        market=Market.US,
        commission_model=USCommissionModel(),
        slippage_model=NoSlippage(),
    )
    broker.process_bar(_bar(day_offset=0))
    broker.buy(SYMBOL, 10)
    broker.buy(SYMBOL, 10)

    manager = await _live_manager()
    await manager.submit_order(SYMBOL, "US", LiveOrderSide.BUY, 10)
    await manager.submit_order(SYMBOL, "US", LiveOrderSide.BUY, 10)

    backtest_ctx = broker._control_context(
        Order(symbol=SYMBOL, market=Market.US, side=OrderSide.BUY, qty=10)
    )
    live_ctx = manager._control_context(_live_order(LiveOrderSide.BUY, 10))

    assert backtest_ctx.orders_today == live_ctx.orders_today == 2


async def test_same_instance_shared_between_paths():
    """
    真正共享**同一个对象**（而不是两份等价配置）时也必须一致 ——
    控制器一旦持有 broker/OMS 引用，这个用例就会挂。
    """
    control = MaxPositionSize(max_shares=150)
    controls = [control]

    backtest = _backtest_verdict(controls, "BUY", 200)
    live = await _live_verdict(controls, "BUY", 200)


    assert backtest is not None
    assert backtest == live
    assert backtest.startswith("[MaxPositionSize]")


async def test_controls_do_not_hold_broker_or_oms_reference():
    """控制器实例的属性里不得出现 broker / OMS —— 这是共用的前提。"""
    controls = [
        MaxOrderSize(max_shares=10),
        MaxPositionSize(max_shares=10),
        LongOnly(),
        RestrictedList({SYMBOL}),
    ]
    manager = await _live_manager()
    broker = _backtest_broker()

    for control in controls:
        for value in vars(control).values():
            assert not isinstance(value, SimulatedBroker | OrderManager)
        assert control not in (broker, manager)


# ── 3. 未装控制器时两侧都不受影响 ────────────────────────────────


async def test_no_controls_means_no_rejections_on_either_path():
    assert _backtest_verdict([], "BUY", 100) is None
    assert await _live_verdict([], "BUY", 100) is None
