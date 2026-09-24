"""回测 / 实盘一致性 — Wave L-d 契约 §六 验收 4（**本契约的核心用例**）

同一个 `FrameworkStrategy`、同一份 bar 序列：

    PortfolioBacktestEngine  ──┐
                               ├─→ 订单序列必须**逐张一致**
    LivePortfolioRunner + OMS ─┘

实盘侧的 OMS 用一个由 `PortfolioBroker` 支撑的替身：账户/持仓读该券商，
下单写回该券商。这样两条路径共用同一套撮合语义，剩下的差异就只有本契约
真正要验证的东西 —— 上下文实现与循环编排。

一旦有人在实盘侧另写一份 `target_weight`、把账户快照换成零值兜底、
或让订单绕过 OMS，这些用例会立刻红。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.data.models import Bar, Frequency, Market
from app.engine.backtest.broker import Order, OrderSide, OrderStatus
from app.engine.backtest.commission import CommissionResult
from app.engine.backtest.order_types import OrderType
from app.engine.backtest.portfolio_broker import PortfolioBroker
from app.engine.backtest.portfolio_engine import (
    PortfolioBacktestConfig,
    PortfolioBacktestEngine,
)
from app.engine.backtest.slippage import NoSlippage
from app.engine.controls import MaxOrderSize
from app.engine.framework import (
    AlphaModel,
    EqualWeightingPCM,
    FrameworkStrategy,
    ImmediateExecutionModel,
    Insight,
    InsightDirection,
)

# app.oms 必须先于 app.gateway 导入（既有循环依赖，见 test_controls_parity.py 说明）
from app.oms.manager import OrderManager
from app.oms.order import LiveOrder, LiveOrderSide, LiveOrderStatus, LiveOrderType
from app.strategy.live_runner import LivePortfolioRunner, flush_orders

BASE_TIME = datetime(2024, 1, 2, tzinfo=UTC)
SYMBOLS = ["AAPL", "MSFT"]
INITIAL_CASH = 100_000.0


# ── 夹具 ──────────────────────────────────────────────────────


class _ZeroCommission:
    def calculate(self, price: float, qty: int, side: str) -> CommissionResult:
        return CommissionResult(commission=0.0, fees=0.0, total=0.0)


def _bar(symbol: str, close: float, day: int) -> Bar:
    return Bar(
        time=BASE_TIME + timedelta(days=day),
        symbol=symbol,
        market=Market.US,
        frequency=Frequency.DAY_1,
        open=close,
        high=close * 1.001,
        low=close * 0.999,
        close=close,
        volume=1_000_000,
    )


def _universe(n: int = 40) -> dict[str, list[Bar]]:
    """两条形状不同的价格序列，保证均线来回穿越、既有买单也有卖单。"""
    import math

    return {
        "AAPL": [_bar("AAPL", 100.0 + 8 * math.sin(i / 3.0), i) for i in range(n)],
        "MSFT": [_bar("MSFT", 200.0 + 15 * math.cos(i / 4.0), i) for i in range(n)],
    }


class _CrossoverAlpha(AlphaModel):
    """3 日均线上穿 5 日均线做多，否则观望。只用 ctx 的公开接口。"""

    name = "crossover"

    def update(self, ctx) -> list[Insight]:
        insights: list[Insight] = []
        for symbol in sorted(ctx.bars):
            closes = ctx.close_series(symbol)
            if len(closes) < 5:
                continue
            fast = float(closes.iloc[-3:].mean())
            slow = float(closes.iloc[-5:].mean())
            insights.append(
                Insight(
                    symbol=symbol,
                    direction=InsightDirection.UP if fast > slow else InsightDirection.FLAT,
                    period=timedelta(days=3),
                    generated_at=ctx.time,
                    source=self.name,
                )
            )
        return insights


def _strategy() -> FrameworkStrategy:
    return FrameworkStrategy(
        alpha=_CrossoverAlpha(),
        portfolio_construction=EqualWeightingPCM(),
        execution=ImmediateExecutionModel(),
    )


def _config() -> PortfolioBacktestConfig:
    return PortfolioBacktestConfig(
        initial_cash=INITIAL_CASH,
        market=Market.US,
        commission_model=_ZeroCommission(),
        slippage_model=NoSlippage(),
        warmup_bars=0,
        adjust_prices=False,
    )


def _fresh_broker() -> PortfolioBroker:
    """与 `_config()` 完全同参的券商 —— 实盘侧的「券商替身」用它撮合。"""
    cfg = _config()
    return PortfolioBroker(
        initial_cash=cfg.initial_cash,
        market=cfg.market,
        commission_model=cfg.commission_model,
        slippage_model=cfg.slippage_model,
    )


class _BrokerBackedOms:
    """
    由 `PortfolioBroker` 支撑的 OMS 替身。

    只实现实盘循环真正用到的三个方法，签名与 `OrderManager` 一致。
    """

    def __init__(self, broker: PortfolioBroker, symbols: list[str]) -> None:
        self._broker = broker
        self._symbols = symbols

    async def get_account(self, market: str) -> dict:
        return {
            "cash": self._broker.cash,
            "portfolio_value": self._broker.portfolio_value(),
        }

    async def get_positions(self, market: str) -> list[dict]:
        held = self._broker.positions.net_quantities()
        return [
            {
                "symbol": symbol,
                "qty": qty,
                "avg_cost": self._broker.positions.get(symbol).avg_cost,
                "current_price": self._broker.last_known_price(symbol),
            }
            for symbol, qty in held.items()
        ]

    async def submit_order(
        self, *, symbol, market, side, qty, order_type, limit_price, strategy_id
    ) -> LiveOrder:
        kwargs = (
            {"order_type": OrderType.LIMIT, "limit_price": limit_price}
            if order_type is LiveOrderType.LIMIT
            else {}
        )
        placed = self._broker.submit_order(
            Order(
                symbol=symbol,
                market=Market(market),
                side=OrderSide.BUY if side is LiveOrderSide.BUY else OrderSide.SELL,
                qty=qty,
                **kwargs,
            )
        )
        return LiveOrder(
            symbol=symbol, market=market, side=side, qty=qty,
            order_type=order_type, limit_price=limit_price, strategy_id=strategy_id,
            status=(
                LiveOrderStatus.REJECTED
                if placed.status is OrderStatus.REJECTED
                else LiveOrderStatus.SUBMITTED
            ),
            reject_reason=placed.reject_reason,
        )


class _ReplayFeed:
    """
    按回测的时间轴重放 bar，并在每个时点**先撮合再推送** —— 与回测引擎
    `_step` 的「advance_day → process_bars → on_bars」顺序完全对齐。

    推完一个时点的 bar 后会等实盘循环处理完，避免 feeder 跑到消费者前面
    把两个时点搅在一起。
    """

    def __init__(self, broker: PortfolioBroker, universe: dict[str, list[Bar]]) -> None:
        self._broker = broker
        self._timeline = _timeline(universe)
        self.step_done = asyncio.Event()

    async def subscribe_bars(self, symbols, market, frequency):
        for timestamp, bars in self._timeline:
            self._broker.advance_day()
            self._broker.process_bars(timestamp, bars)
            for bar in bars.values():
                yield bar
            await self.step_done.wait()
            self.step_done.clear()


def _timeline(universe: dict[str, list[Bar]]) -> list[tuple[datetime, dict[str, Bar]]]:
    stamps = sorted({bar.time for bars in universe.values() for bar in bars})
    return [
        (ts, {s: b for s, bars in universe.items() for b in bars if b.time == ts})
        for ts in stamps
    ]


@pytest.fixture(autouse=True)
def _disable_live_only_risk_engine():
    """
    关掉 `RiskEngine`（敞口/集中度限额）。

    它是**实盘独有**的一道闸门，回测引擎里没有对应物 —— 默认配置会把
    「等权两标的 ⇒ 每个 50% 净值」直接拒掉。留着它两条路径必然不等价，
    但那不是本契约要验证的差异。它自身的行为由 `test_live_engine.py::
    test_flush_applies_the_live_only_risk_engine_gate` 单独覆盖。
    """
    from app.risk import engine as risk_engine
    from app.risk.models import RiskConfig

    saved = risk_engine._engine
    risk_engine.init_risk_engine(RiskConfig(name="parity-off", is_active=False))
    yield
    risk_engine._engine = saved


@pytest.fixture
def order_log(monkeypatch) -> list[tuple]:
    """录下**所有**进入券商的委托 —— 两条路径共用这一个探针，口径必然一致。"""
    log: list[tuple] = []
    original = PortfolioBroker.submit_order

    def spy(self, order: Order) -> Order:
        log.append((order.symbol, order.side.value, order.qty, order.order_type.value))
        return original(self, order)

    monkeypatch.setattr(PortfolioBroker, "submit_order", spy)
    return log


# ── 核心：订单序列一致 ────────────────────────────────────────


@pytest.mark.asyncio
async def test_backtest_and_live_produce_identical_order_sequences(order_log):
    universe = _universe()

    PortfolioBacktestEngine(_config()).run(_strategy(), universe)
    backtest_orders = list(order_log)
    order_log.clear()

    broker = _fresh_broker()
    feed = _ReplayFeed(broker, universe)
    runner = LivePortfolioRunner(
        instance_id="parity",
        strategy=_strategy(),
        symbols=sorted(universe),
        market=Market.US,
        frequency=Frequency.DAY_1,
        data_service=feed,
        oms_provider=lambda: _BrokerBackedOms(broker, sorted(universe)),
        window_seconds=30.0,
        on_step=lambda report: feed.step_done.set(),
    )
    await asyncio.wait_for(runner.run(), timeout=30.0)
    live_orders = list(order_log)

    assert backtest_orders, "夹具必须真的产生订单，否则本用例是空跑"
    assert len(backtest_orders) > 5, "样本太小，一致性没有说服力"
    assert live_orders == backtest_orders


@pytest.mark.asyncio
async def test_live_run_ends_with_the_same_portfolio_state(order_log):
    """订单一致 ⇒ 成交一致 ⇒ 期末持仓与净值也必须一致。"""
    universe = _universe()

    backtest = PortfolioBacktestEngine(_config()).run(_strategy(), universe)
    order_log.clear()

    broker = _fresh_broker()
    feed = _ReplayFeed(broker, universe)
    runner = LivePortfolioRunner(
        instance_id="parity",
        strategy=_strategy(),
        symbols=sorted(universe),
        market=Market.US,
        frequency=Frequency.DAY_1,
        data_service=feed,
        oms_provider=lambda: _BrokerBackedOms(broker, sorted(universe)),
        window_seconds=30.0,
        on_step=lambda report: feed.step_done.set(),
    )
    await asyncio.wait_for(runner.run(), timeout=30.0)

    assert broker.portfolio_value() == pytest.approx(backtest.final_value, rel=1e-9)
    assert runner.orders_placed == len(order_log)      # 每张订单都真的进了 OMS
    assert runner.snapshot_failures == 0


@pytest.mark.asyncio
async def test_live_runner_feeds_the_strategy_the_same_history_as_backtest():
    """实盘的滚动历史必须与回测的惰性切片等长 —— 否则指标会算在不同窗口上。"""
    seen: list[int] = []

    class _HistoryProbe(AlphaModel):
        name = "probe"

        def update(self, ctx) -> list[Insight]:
            seen.append(len(ctx.history("AAPL")))
            return []

    universe = _universe(10)
    PortfolioBacktestEngine(_config()).run(
        FrameworkStrategy(alpha=_HistoryProbe(), portfolio_construction=EqualWeightingPCM()),
        universe,
    )
    backtest_lengths = list(seen)
    seen.clear()

    broker = _fresh_broker()
    feed = _ReplayFeed(broker, universe)
    runner = LivePortfolioRunner(
        instance_id="probe",
        strategy=FrameworkStrategy(
            alpha=_HistoryProbe(), portfolio_construction=EqualWeightingPCM()
        ),
        symbols=sorted(universe),
        market=Market.US,
        frequency=Frequency.DAY_1,
        data_service=feed,
        oms_provider=lambda: _BrokerBackedOms(broker, sorted(universe)),
        window_seconds=30.0,
        on_step=lambda report: feed.step_done.set(),
    )
    await asyncio.wait_for(runner.run(), timeout=30.0)

    assert seen == backtest_lengths
    assert backtest_lengths == list(range(1, 11))


# ── 契约 §5.1：同一批控制器，两条路径同样拒单 ────────────────


async def _live_manager(control) -> OrderManager:
    from app.gateway.paper_gateway import PaperGateway  # 见模块头部的循环依赖说明

    manager = OrderManager()
    gateway = PaperGateway(market="US", initial_cash=INITIAL_CASH, currency="USD")
    await gateway.connect()
    manager.register_gateway("US", gateway)
    manager.set_controls([control])
    return manager


@pytest.mark.asyncio
async def test_same_control_instance_rejects_on_both_paths():
    """
    L-a 的承诺在 L-d 落地：一份 `controls` 配置，回测拒掉的单实盘也拒，
    且拒单原因逐字一致。冲刷路径若绕过 `OrderManager.submit_order`，本用例即红。
    """
    control = MaxOrderSize(max_shares=5)

    broker = PortfolioBroker(
        initial_cash=INITIAL_CASH,
        market=Market.US,
        commission_model=_ZeroCommission(),
        slippage_model=NoSlippage(),
        controls=[control],
    )
    backtest_order = broker.submit_order(
        Order(symbol="AAPL", market=Market.US, side=OrderSide.BUY, qty=10)
    )

    manager = await _live_manager(control)
    results = await flush_orders(
        manager,
        [Order(symbol="AAPL", market=Market.US, side=OrderSide.BUY, qty=10)],
        strategy_id="parity",
    )

    assert backtest_order.status is OrderStatus.REJECTED
    live = results[0].live_order
    assert live is not None
    assert live.status is LiveOrderStatus.REJECTED
    assert live.reject_reason == backtest_order.reject_reason


@pytest.mark.asyncio
async def test_control_allows_the_same_order_on_both_paths():
    """反向：同一批控制器放行的单，两条路径都放行。"""
    control = MaxOrderSize(max_shares=50)

    broker = PortfolioBroker(
        initial_cash=INITIAL_CASH,
        market=Market.US,
        commission_model=_ZeroCommission(),
        slippage_model=NoSlippage(),
        controls=[control],
    )
    backtest_order = broker.submit_order(
        Order(symbol="AAPL", market=Market.US, side=OrderSide.BUY, qty=10)
    )

    manager = await _live_manager(control)
    results = await flush_orders(
        manager,
        [Order(symbol="AAPL", market=Market.US, side=OrderSide.BUY, qty=10)],
        strategy_id="parity",
    )

    assert backtest_order.status is not OrderStatus.REJECTED
    live = results[0].live_order
    assert live is not None
    assert live.status is not LiveOrderStatus.REJECTED
