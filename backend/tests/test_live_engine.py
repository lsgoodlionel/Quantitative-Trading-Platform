"""实盘多标的循环 — Wave L-d 契约 §三、§五 / §六 验收 3

覆盖四件事：
1. bar 聚合：齐了立即触发；不齐就等窗口超时，**绝不无限等**；
2. 缺失标的按最后已知价估值，不产生假清仓；
3. 账户快照拉取失败 → 跳过本时点、不下任何单；连续 N 次 → 置 ERROR；
4. 订单冲刷必须走 `OrderManager.submit_order`（不得直连 gateway）。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.data.models import Bar, Frequency, Market
from app.engine.backtest.broker import Order, OrderSide
from app.engine.backtest.order_types import OrderType

# app.oms 必须先于 app.gateway 导入（既有循环依赖，见 test_controls_parity.py 说明）
from app.oms.manager import OrderManager
from app.oms.order import LiveOrder, LiveOrderSide, LiveOrderType
from app.strategy.base import PortfolioStrategyBase
from app.strategy.live_context import AccountSnapshot, LivePortfolioContext
from app.strategy.live_runner import (
    AccountSnapshotError,
    BarAggregator,
    LivePortfolioRunner,
    fetch_account_snapshot,
    floor_bar_time,
    flush_orders,
)

BASE_TIME = datetime(2024, 1, 2, tzinfo=UTC)
SYMBOLS = ["AAPL", "MSFT"]
WINDOW = 0.05        # 测试用的聚合窗口（秒），够短以免拖慢测试


# ── 夹具 ──────────────────────────────────────────────────────


def _bar(symbol: str, close: float, minute: int = 0) -> Bar:
    return Bar(
        time=BASE_TIME + timedelta(minutes=minute),
        symbol=symbol,
        market=Market.US,
        frequency=Frequency.MIN_1,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000,
    )


class _RecordingStrategy(PortfolioStrategyBase):
    """记录每次 on_bars 收到的上下文，可选地下一张固定的单。"""

    name = "recording"

    def __init__(self, buy: tuple[str, int] | None = None) -> None:
        super().__init__({})
        self.contexts: list[LivePortfolioContext] = []
        self.started = 0
        self._buy = buy

    def on_start(self, ctx) -> None:
        self.started += 1

    def on_bars(self, ctx) -> None:
        self.contexts.append(ctx)
        if self._buy is not None:
            ctx.buy(self._buy[0], self._buy[1])


class _FakeDataService:
    """按脚本推送 bar 的数据源。`hold_open=True` 时推完不结束（模拟持续订阅）。"""

    def __init__(self, script: list[Bar], hold_open: bool = False) -> None:
        self._script = script
        self._hold_open = hold_open
        self.subscribed: list[list[str]] = []

    async def subscribe_bars(self, symbols, market, frequency):
        self.subscribed.append(list(symbols))
        for bar in self._script:
            yield bar
            await asyncio.sleep(0)
        if self._hold_open:
            await asyncio.Event().wait()     # 永不返回


class _FakeOms:
    """最小 OMS 替身：账户/持仓来自构造参数，下单只记录。"""

    def __init__(
        self,
        portfolio_value: float = 100_000.0,
        cash: float = 100_000.0,
        positions: list[dict] | None = None,
        fail_times: int | None = None,
    ) -> None:
        self._portfolio_value = portfolio_value
        self._cash = cash
        self._positions = positions or []
        self._fail_times = fail_times      # None = 一直失败；数字 = 前 N 次失败
        self.calls = 0
        self.submitted: list[dict] = []

    def _should_fail(self) -> bool:
        if self._fail_times is None:
            return False
        return self.calls <= self._fail_times

    async def get_account(self, market: str) -> dict:
        self.calls += 1
        if self._should_fail():
            raise ConnectionError("broker unreachable")
        return {"cash": self._cash, "portfolio_value": self._portfolio_value}

    async def get_positions(self, market: str) -> list[dict]:
        return list(self._positions)

    async def submit_order(self, **kwargs) -> LiveOrder:
        self.submitted.append(kwargs)
        return LiveOrder(
            symbol=kwargs["symbol"],
            market=kwargs["market"],
            side=kwargs["side"],
            qty=kwargs["qty"],
            order_type=kwargs.get("order_type", LiveOrderType.MARKET),
            broker_order_id="broker-1",
        )


def _runner(strategy, data_service, oms, **kwargs) -> LivePortfolioRunner:
    return LivePortfolioRunner(
        instance_id="live-1",
        strategy=strategy,
        symbols=list(SYMBOLS),
        market=Market.US,
        frequency=Frequency.MIN_1,
        data_service=data_service,
        oms_provider=lambda: oms,
        window_seconds=kwargs.pop("window_seconds", WINDOW),
        **kwargs,
    )


# ── 1. bar 时点归一 ───────────────────────────────────────────


@pytest.mark.parametrize(
    ("frequency", "raw", "expected"),
    [
        (Frequency.MIN_1, datetime(2024, 1, 2, 9, 31, 42), datetime(2024, 1, 2, 9, 31)),
        (Frequency.MIN_5, datetime(2024, 1, 2, 9, 37, 5), datetime(2024, 1, 2, 9, 35)),
        (Frequency.HOUR_1, datetime(2024, 1, 2, 9, 59, 59), datetime(2024, 1, 2, 9, 0)),
        (Frequency.DAY_1, datetime(2024, 1, 2, 16, 0), datetime(2024, 1, 2)),
        (Frequency.WEEK_1, datetime(2024, 1, 4, 16, 0), datetime(2024, 1, 1)),
    ],
)
def test_floor_bar_time(frequency, raw, expected):
    assert floor_bar_time(raw, frequency) == expected


# ── 2. bar 聚合 ───────────────────────────────────────────────


def test_aggregator_completes_when_every_symbol_arrived():
    agg = BarAggregator(SYMBOLS, Frequency.MIN_1, window_seconds=5.0)
    assert not agg.is_complete
    agg.add(_bar("AAPL", 100.0), now=0.0)
    assert not agg.is_complete
    agg.add(_bar("MSFT", 50.0), now=0.1)
    assert agg.is_complete
    ts, bars = agg.flush()
    assert ts == BASE_TIME
    assert set(bars) == {"AAPL", "MSFT"}
    assert agg.flush() is None                  # 冲刷后桶就空了


def test_aggregator_never_waits_forever():
    """窗口是有限的：桶不齐时 remaining 必须单调走到 0。"""
    agg = BarAggregator(SYMBOLS, Frequency.MIN_1, window_seconds=5.0)
    assert agg.remaining(now=0.0) is None       # 空桶：没有截止时间
    agg.add(_bar("AAPL", 100.0), now=10.0)
    assert agg.remaining(now=10.0) == pytest.approx(5.0)
    assert agg.remaining(now=13.0) == pytest.approx(2.0)
    assert agg.remaining(now=99.0) == 0.0


def test_aggregator_defers_a_bar_that_belongs_to_the_next_timepoint():
    agg = BarAggregator(SYMBOLS, Frequency.MIN_1, window_seconds=5.0)
    agg.add(_bar("AAPL", 100.0, minute=0), now=0.0)
    deferred = agg.add(_bar("AAPL", 101.0, minute=1), now=1.0)
    assert deferred is not None
    assert deferred.close == 101.0
    assert set(agg.flush()[1]) == {"AAPL"}


@pytest.mark.asyncio
async def test_complete_bucket_fires_immediately_without_waiting_for_the_window():
    """两个标的都到齐 → 立刻触发。窗口设成 30s，用例仍必须秒级返回。"""
    strategy = _RecordingStrategy()
    feed = _FakeDataService([_bar("AAPL", 100.0), _bar("MSFT", 50.0)])
    runner = _runner(strategy, feed, _FakeOms(), window_seconds=30.0)

    await asyncio.wait_for(runner.run(), timeout=3.0)

    assert len(strategy.contexts) == 1
    assert set(strategy.contexts[0].bars) == {"AAPL", "MSFT"}
    assert feed.subscribed == [SYMBOLS]          # 一次订阅全部标的


@pytest.mark.asyncio
async def test_incomplete_bucket_fires_after_the_window_expires():
    strategy = _RecordingStrategy()
    feed = _FakeDataService([_bar("AAPL", 100.0)], hold_open=True)
    runner = _runner(strategy, feed, _FakeOms())

    task = asyncio.create_task(runner.run())
    for _ in range(200):
        if strategy.contexts:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(strategy.contexts) == 1
    assert set(strategy.contexts[0].bars) == {"AAPL"}


@pytest.mark.asyncio
async def test_missing_symbol_is_valued_at_last_known_price():
    """MSFT 第二个时点停牌：估值价必须沿用快照价，不能变成 None/0（假清仓）。"""
    strategy = _RecordingStrategy()
    script = [
        _bar("AAPL", 100.0, 0), _bar("MSFT", 50.0, 0),
        _bar("AAPL", 101.0, 1),
    ]
    oms = _FakeOms(
        positions=[
            {"symbol": "MSFT", "qty": 10, "avg_cost": 45.0, "current_price": 50.0},
        ]
    )
    feed = _FakeDataService(script)
    runner = _runner(strategy, feed, oms)

    await asyncio.wait_for(runner.run(), timeout=3.0)

    assert len(strategy.contexts) == 2
    second = strategy.contexts[1]
    assert second.bar("MSFT") is None
    assert second.price("MSFT") == 50.0
    assert second.qty("MSFT") == 10


# ── 3. 账户快照 ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_account_snapshot_merges_positions_and_prices():
    oms = _FakeOms(
        portfolio_value=120_000.0,
        cash=20_000.0,
        positions=[{"symbol": "AAPL", "qty": 100, "avg_cost": 90.0, "current_price": 110.0}],
    )
    snap = await fetch_account_snapshot(
        oms, market="US", symbols=SYMBOLS, last_prices={"MSFT": 50.0}
    )
    assert isinstance(snap, AccountSnapshot)
    assert snap.portfolio_value == 120_000.0
    assert snap.positions["AAPL"] == 100
    assert snap.prices == {"MSFT": 50.0, "AAPL": 110.0}
    assert snap.avg_costs["AAPL"] == 90.0


@pytest.mark.asyncio
async def test_fetch_account_snapshot_raises_instead_of_defaulting_to_zero():
    oms = _FakeOms(fail_times=99)
    with pytest.raises(AccountSnapshotError):
        await fetch_account_snapshot(oms, market="US", symbols=SYMBOLS, last_prices={})


@pytest.mark.asyncio
async def test_fetch_account_snapshot_rejects_zero_portfolio_value():
    """券商返回 0 净值与「拉取失败」同样危险 —— 必须当成失败，不得放行。"""
    oms = _FakeOms(portfolio_value=0.0)
    with pytest.raises(AccountSnapshotError, match="portfolio_value"):
        await fetch_account_snapshot(oms, market="US", symbols=SYMBOLS, last_prices={})


@pytest.mark.asyncio
async def test_snapshot_failure_skips_the_timepoint_and_places_no_orders():
    strategy = _RecordingStrategy(buy=("AAPL", 10))
    oms = _FakeOms(fail_times=99)
    feed = _FakeDataService([_bar("AAPL", 100.0), _bar("MSFT", 50.0)])
    runner = _runner(strategy, feed, oms, max_snapshot_failures=99)

    await asyncio.wait_for(runner.run(), timeout=3.0)

    assert strategy.contexts == []               # on_bars 根本没被调用
    assert oms.submitted == []                   # 尤其不能出现清仓单
    assert runner.snapshot_failures == 1


@pytest.mark.asyncio
async def test_consecutive_snapshot_failures_mark_the_instance_as_error():
    fatal: list[str] = []
    strategy = _RecordingStrategy(buy=("AAPL", 10))
    oms = _FakeOms(fail_times=99)
    script = [_bar("AAPL", 100.0, m) for m in range(5)]
    feed = _FakeDataService(script, hold_open=True)
    runner = _runner(
        strategy, feed, oms, max_snapshot_failures=3, on_fatal=fatal.append
    )

    await asyncio.wait_for(runner.run(), timeout=5.0)

    assert runner.snapshot_failures == 3
    assert len(fatal) == 1
    assert "快照" in fatal[0]
    assert oms.submitted == []


@pytest.mark.asyncio
async def test_snapshot_failure_counter_resets_after_a_success():
    strategy = _RecordingStrategy()
    oms = _FakeOms(fail_times=1)                 # 第一次失败，之后成功
    script = [_bar("AAPL", 100.0, m) for m in range(3)]
    feed = _FakeDataService(script)
    runner = _runner(strategy, feed, oms, max_snapshot_failures=3)

    await asyncio.wait_for(runner.run(), timeout=3.0)

    assert runner.snapshot_failures == 0
    assert len(strategy.contexts) == 2           # 第一个时点被跳过


# ── 4. 订单冲刷走 OrderManager.submit_order ───────────────────


def _local_order(symbol: str, qty: int, side: OrderSide = OrderSide.BUY, **kw) -> Order:
    return Order(symbol=symbol, market=Market.US, side=side, qty=qty, **kw)


@pytest.mark.asyncio
async def test_flush_routes_through_order_manager_submit_order():
    """必须走 OMS 的 submit_order —— 直连 gateway 会绕过风控/控制器/防护。"""
    manager = OrderManager()
    with patch.object(
        OrderManager, "submit_order", new_callable=AsyncMock
    ) as submit:
        submit.return_value = LiveOrder(
            symbol="AAPL", market="US", side=LiveOrderSide.BUY, qty=5
        )
        results = await flush_orders(
            manager, [_local_order("AAPL", 5)], strategy_id="live-1"
        )

    submit.assert_awaited_once()
    kwargs = submit.await_args.kwargs
    assert kwargs["symbol"] == "AAPL"
    assert kwargs["side"] is LiveOrderSide.BUY
    assert kwargs["qty"] == 5
    assert kwargs["strategy_id"] == "live-1"
    assert len(results) == 1
    assert results[0].error is None


@pytest.mark.asyncio
async def test_flush_maps_limit_orders():
    oms = _FakeOms()
    order = _local_order(
        "AAPL", 5, OrderSide.SELL, order_type=OrderType.LIMIT, limit_price=99.5
    )
    await flush_orders(oms, [order], strategy_id="live-1")
    assert oms.submitted[0]["order_type"] is LiveOrderType.LIMIT
    assert oms.submitted[0]["limit_price"] == 99.5
    assert oms.submitted[0]["side"] is LiveOrderSide.SELL


@pytest.mark.asyncio
async def test_flush_reports_unsupported_order_types_instead_of_dropping_them():
    oms = _FakeOms()
    order = _local_order(
        "AAPL", 5, order_type=OrderType.STOP_MARKET, stop_price=90.0
    )
    results = await flush_orders(oms, [order], strategy_id="live-1")
    assert oms.submitted == []
    assert results[0].live_order is None
    assert "STOP_MARKET" in results[0].error


@pytest.mark.asyncio
async def test_flush_keeps_going_when_one_order_is_rejected():
    oms = _FakeOms()
    calls = {"n": 0}
    original = oms.submit_order

    async def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("gateway down")
        return await original(**kwargs)

    oms.submit_order = flaky
    results = await flush_orders(
        oms, [_local_order("AAPL", 1), _local_order("MSFT", 2)], strategy_id="live-1"
    )
    assert results[0].error == "gateway down"
    assert results[1].error is None


@pytest.mark.asyncio
async def test_runner_submits_strategy_orders_and_counts_them():
    strategy = _RecordingStrategy(buy=("AAPL", 10))
    oms = _FakeOms()
    feed = _FakeDataService([_bar("AAPL", 100.0), _bar("MSFT", 50.0)])
    runner = _runner(strategy, feed, oms)

    await asyncio.wait_for(runner.run(), timeout=3.0)

    assert len(oms.submitted) == 1
    assert oms.submitted[0]["symbol"] == "AAPL"
    assert oms.submitted[0]["strategy_id"] == "live-1"
    assert runner.orders_placed == 1
    assert runner.bars_processed == 2
    assert strategy.started == 1                 # on_start 只跑一次


@pytest.mark.asyncio
async def test_strategy_exception_does_not_kill_the_loop():
    class _Boom(PortfolioStrategyBase):
        name = "boom"

        def __init__(self) -> None:
            super().__init__({})
            self.calls = 0

        def on_bars(self, ctx) -> None:
            self.calls += 1
            raise RuntimeError("策略炸了")

    strategy = _Boom()
    script = [_bar("AAPL", 100.0, m) for m in range(3)]
    runner = _runner(strategy, _FakeDataService(script), _FakeOms())

    await asyncio.wait_for(runner.run(), timeout=3.0)

    assert strategy.calls == 3                   # 每个时点都跑到了


@pytest.mark.asyncio
async def test_data_stream_error_marks_the_instance_as_error():
    fatal: list[str] = []

    class _BrokenFeed:
        async def subscribe_bars(self, symbols, market, frequency):
            yield _bar("AAPL", 100.0)
            raise ConnectionError("feed dropped")

    runner = _runner(
        _RecordingStrategy(), _BrokenFeed(), _FakeOms(), on_fatal=fatal.append
    )
    await asyncio.wait_for(runner.run(), timeout=3.0)
    assert len(fatal) == 1
    assert "feed dropped" in fatal[0]


# ── 5. StrategyEngine 的组合策略入口 ──────────────────────────


class _StubDataService:
    """既能给预热历史、又能推实时 bar 的最小数据服务。"""

    def __init__(self, script: list[Bar], warmup: list[Bar] | None = None) -> None:
        self._feed = _FakeDataService(script)
        self._warmup = warmup or []
        self.warmup_requests: list[str] = []

    async def get_bars(self, symbol, market, frequency, start, end):
        self.warmup_requests.append(symbol)
        return [b for b in self._warmup if b.symbol == symbol]

    def subscribe_bars(self, symbols, market, frequency):
        return self._feed.subscribe_bars(symbols, market, frequency)


@pytest.mark.asyncio
async def test_start_portfolio_strategy_runs_the_live_loop_and_updates_the_instance():
    from app.strategy.engine import StrategyEngine, StrategyState

    strategy = _RecordingStrategy(buy=("AAPL", 3))
    oms = _FakeOms()
    data = _StubDataService(
        [_bar("AAPL", 100.0), _bar("MSFT", 50.0)],
        warmup=[_bar("AAPL", 99.0, minute=-1), _bar("MSFT", 49.0, minute=-1)],
    )

    engine = StrategyEngine()
    with patch("app.strategy.engine.get_order_manager", return_value=oms):
        inst = await engine.start_portfolio_strategy(
            instance_id="pf-1",
            strategy=strategy,
            symbols=SYMBOLS,
            market="US",
            frequency="1m",
            data_service=data,
        )
        await asyncio.wait_for(inst.task, timeout=3.0)

    assert inst.state is StrategyState.RUNNING
    assert inst.symbol == "AAPL,MSFT"
    assert inst.bars_processed == 2
    assert inst.orders_placed == 1
    assert sorted(data.warmup_requests) == SYMBOLS
    # 预热历史必须真的喂进上下文：1 根预热 + 1 根实时
    assert len(strategy.contexts[0].history("AAPL")) == 2


@pytest.mark.asyncio
async def test_start_portfolio_strategy_installs_the_same_controls_on_the_oms():
    """契约 §5.1：一份 controls 配置，回测装 broker、实盘装 OMS。"""
    from app.engine.controls import MaxOrderSize
    from app.strategy.engine import StrategyEngine

    controls = [MaxOrderSize(max_shares=5)]
    manager = OrderManager()
    data = _StubDataService([])

    engine = StrategyEngine()
    with patch("app.strategy.engine.get_order_manager", return_value=manager):
        inst = await engine.start_portfolio_strategy(
            instance_id="pf-controls",
            strategy=_RecordingStrategy(),
            symbols=SYMBOLS,
            market="US",
            frequency="1m",
            data_service=data,
            controls=controls,
        )
        await asyncio.wait_for(inst.task, timeout=3.0)

    assert manager._controls == tuple(controls)


@pytest.mark.asyncio
async def test_start_portfolio_strategy_marks_error_on_repeated_snapshot_failure():
    from app.strategy.engine import StrategyEngine, StrategyState

    oms = _FakeOms(fail_times=99)
    data = _StubDataService([_bar("AAPL", 100.0, m) for m in range(6)])

    engine = StrategyEngine()
    with patch("app.strategy.engine.get_order_manager", return_value=oms):
        inst = await engine.start_portfolio_strategy(
            instance_id="pf-error",
            strategy=_RecordingStrategy(buy=("AAPL", 1)),
            symbols=SYMBOLS,
            market="US",
            frequency="1m",
            data_service=data,
        )
        await asyncio.wait_for(inst.task, timeout=10.0)

    assert inst.state is StrategyState.ERROR
    assert "快照" in (inst.error or "")
    assert oms.submitted == []


@pytest.mark.asyncio
async def test_start_portfolio_strategy_validates_inputs():
    from app.strategy.engine import StrategyEngine

    engine = StrategyEngine()
    data = _StubDataService([])
    with pytest.raises(ValueError, match="至少需要一个标的"):
        await engine.start_portfolio_strategy(
            instance_id="pf-bad", strategy=_RecordingStrategy(), symbols=["  "],
            market="US", frequency="1m", data_service=data,
        )
    with pytest.raises(ValueError, match="MARS"):
        await engine.start_portfolio_strategy(
            instance_id="pf-bad", strategy=_RecordingStrategy(), symbols=SYMBOLS,
            market="MARS", frequency="1m", data_service=data,
        )


@pytest.mark.asyncio
async def test_start_portfolio_strategy_refuses_to_start_twice():
    from app.strategy.engine import StrategyEngine

    oms = _FakeOms()
    data = _StubDataService([], warmup=[])
    engine = StrategyEngine()
    with patch("app.strategy.engine.get_order_manager", return_value=oms):
        inst = await engine.start_portfolio_strategy(
            instance_id="pf-dup", strategy=_RecordingStrategy(), symbols=SYMBOLS,
            market="US", frequency="1m", data_service=data,
        )
        with pytest.raises(ValueError, match="already running"):
            await engine.start_portfolio_strategy(
                instance_id="pf-dup", strategy=_RecordingStrategy(), symbols=SYMBOLS,
                market="US", frequency="1m", data_service=data,
            )
        await asyncio.wait_for(inst.task, timeout=3.0)


@pytest.mark.asyncio
async def test_warmup_failure_does_not_block_startup():
    from app.strategy.engine import StrategyEngine

    class _BrokenWarmup(_StubDataService):
        async def get_bars(self, symbol, market, frequency, start, end):
            raise ConnectionError("data source down")

    oms = _FakeOms()
    data = _BrokenWarmup([_bar("AAPL", 100.0), _bar("MSFT", 50.0)])
    engine = StrategyEngine()
    with patch("app.strategy.engine.get_order_manager", return_value=oms):
        inst = await engine.start_portfolio_strategy(
            instance_id="pf-nowarmup", strategy=_RecordingStrategy(), symbols=SYMBOLS,
            market="US", frequency="1m", data_service=data,
        )
        await asyncio.wait_for(inst.task, timeout=3.0)

    assert inst.bars_processed == 2


# ── 6. 实盘独有的 RiskEngine 闸门 ─────────────────────────────


def _snapshot(portfolio_value: float = 10_000.0, price: float = 100.0) -> AccountSnapshot:
    return AccountSnapshot(
        cash=portfolio_value,
        portfolio_value=portfolio_value,
        positions={},
        prices={"AAPL": price},
        taken_at=BASE_TIME,
    )


@pytest.mark.asyncio
async def test_flush_applies_the_live_only_risk_engine_gate():
    """
    组合策略的冲刷路径不得比单标的路径少一道风控：
    `_submit_live_order` 有 `RiskEngine.pre_trade_check`，这里也必须有。
    """
    oms = _FakeOms()
    # 10 万市值的单打在 1 万净值的账户上 —— 必然触发集中度限额
    huge = _local_order("AAPL", 1_000)
    results = await flush_orders(
        oms, [huge], strategy_id="live-1", snapshot=_snapshot()
    )

    assert oms.submitted == []
    assert results[0].live_order is None
    assert "portfolio" in (results[0].error or "")


@pytest.mark.asyncio
async def test_flush_without_snapshot_skips_the_risk_engine_gate():
    """没有快照就没有净值口径，此时把判断交给 OMS 侧的闸门而不是瞎拒。"""
    oms = _FakeOms()
    results = await flush_orders(oms, [_local_order("AAPL", 1_000)], strategy_id="live-1")
    assert len(oms.submitted) == 1
    assert results[0].error is None


@pytest.mark.asyncio
async def test_risk_engine_lets_a_small_order_through():
    oms = _FakeOms()
    results = await flush_orders(
        oms, [_local_order("AAPL", 1)], strategy_id="live-1",
        snapshot=_snapshot(portfolio_value=1_000_000.0),
    )
    assert len(oms.submitted) == 1
    assert results[0].error is None
