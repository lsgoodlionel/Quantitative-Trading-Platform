"""L5 下单确认与价格自定义钩子测试（Wave L-c）

对应契约 docs/contracts/waveLc-position-order-hooks.md §三 与 §五.3。

设计参考自 freqtrade `IStrategy` 的 confirm_trade_entry / confirm_trade_exit /
custom_entry_price 语义（GPL-3.0），此处为独立实现，未复制其任何代码。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest

from app.data.models import Bar, Frequency, Market
from app.engine.backtest.broker import OrderStatus
from app.engine.backtest.commission import CommissionResult
from app.engine.backtest.order_types import OrderType, TimeInForce
from app.engine.backtest.portfolio_broker import PortfolioBroker
from app.engine.backtest.portfolio_engine import (
    PortfolioBacktestConfig,
    PortfolioBacktestEngine,
)
from app.engine.backtest.slippage import NoSlippage
from app.engine.backtest.trade import EXIT_STOP_LOSS
from app.strategy.base import StrategyBase, StrategyContractError
from app.strategy.context import StrategyContext

START = datetime(2024, 1, 2, 9, 30, tzinfo=UTC)
HOOK_LOGGER = "app.engine.backtest.broker_order_hooks"


class _ZeroCommission:
    def calculate(self, price: float, qty: int, side: str) -> CommissionResult:
        return CommissionResult(commission=0.0, fees=0.0, total=0.0)


def _bars(
    closes: list[float],
    symbol: str = "AAPL",
    step: timedelta = timedelta(days=1),
    frequency: Frequency = Frequency.DAY_1,
) -> list[Bar]:
    return [
        Bar(
            time=START + step * i,
            symbol=symbol,
            market=Market.US,
            frequency=frequency,
            open=price,
            high=price * 1.001,
            low=price * 0.999,
            close=price,
            volume=1_000_000,
        )
        for i, price in enumerate(closes)
    ]


def _config(**kwargs) -> PortfolioBacktestConfig:
    defaults = {
        "initial_cash": 100_000.0,
        "market": Market.US,
        "slippage_model": NoSlippage(),
        "commission_model": _ZeroCommission(),
    }
    defaults.update(kwargs)
    return PortfolioBacktestConfig(**defaults)


def _run(strategy: StrategyBase, bars: list[Bar], **cfg_kwargs) -> list[dict]:
    engine = PortfolioBacktestEngine(_config(**cfg_kwargs))
    return engine.run_single(strategy, bars).fills


def _broker_with_position(qty: int = 100, price: float = 100.0) -> PortfolioBroker:
    """建一个已持有多头仓位的券商，用于直接对 `submit_order` 做单元断言。"""
    broker = PortfolioBroker(
        initial_cash=100_000.0,
        market=Market.US,
        slippage_model=NoSlippage(),
        commission_model=_ZeroCommission(),
    )
    bar = _bars([price])[0]
    broker.buy("AAPL", qty)
    broker.process_bars(bar.time, {"AAPL": bar})
    return broker


# ── 测试策略 ──────────────────────────────────────────────────────


class BuyOnce(StrategyBase):
    name = "buy_once"

    def on_start(self, ctx: StrategyContext) -> None:
        self._done = False
        self.orders: list = []
        self.qty_seen: list[int] = []

    def on_bar(self, ctx: StrategyContext) -> None:
        self.qty_seen.append(ctx.qty)
        if self._done:
            return
        qty = int(ctx.cash * 0.5 / ctx.bar.close)
        if qty > 0:
            self.orders.append(ctx.buy(qty))
            self._done = True


# ── 1. 默认全不覆盖 = 零开销路径 ────────────────────────────────


def test_default_strategy_reports_no_order_hooks():
    assert BuyOnce().has_order_hooks() is False
    assert StrategyBase.entry_timeout_minutes is None
    assert StrategyBase.exit_timeout_minutes is None


def test_default_hooks_do_not_change_fills():
    fills = _run(BuyOnce(), _bars([100.0, 100.0, 110.0, 110.0]))
    assert len(fills) == 1
    assert fills[0]["qty"] == 500


def test_overriding_any_hook_flips_the_flag():
    class WithHook(BuyOnce):
        def confirm_entry(self, ctx, symbol, qty, price, entry_tag) -> bool:
            return True

    class WithTimeout(BuyOnce):
        entry_timeout_minutes = 30

    assert WithHook().has_order_hooks() is True
    assert WithTimeout().has_order_hooks() is True


# ── 2. confirm_entry ───────────────────────────────────────────


def test_confirm_entry_veto_produces_no_fill():
    class VetoEntry(BuyOnce):
        name = "veto_entry"

        def confirm_entry(self, ctx, symbol, qty, price, entry_tag) -> bool:
            return False

    strategy = VetoEntry()
    fills = _run(strategy, _bars([100.0, 100.0, 100.0]))

    assert fills == []
    order = strategy.orders[0]
    assert order.status is OrderStatus.CANCELLED
    assert "confirm_entry" in order.reject_reason


def test_confirm_entry_receives_order_details():
    seen: list[tuple] = []

    class Recording(BuyOnce):
        name = "recording_entry"

        def confirm_entry(self, ctx, symbol, qty, price, entry_tag) -> bool:
            seen.append((symbol, qty, price, entry_tag))
            return True

    fills = _run(Recording(), _bars([100.0, 100.0, 100.0]))
    assert len(fills) == 1
    assert seen == [("AAPL", 500, 100.0, None)]


# ── 3. confirm_exit ────────────────────────────────────────────


class VetoStopLoss(BuyOnce):
    name = "veto_stop_loss"
    stoploss = -0.10

    def confirm_exit(self, ctx, symbol, qty, price, exit_reason) -> bool:
        return False


def test_confirm_exit_veto_keeps_position_and_warns(caplog):
    caplog.set_level(logging.WARNING, logger=HOOK_LOGGER)
    strategy = VetoStopLoss()
    fills = _run(strategy, _bars([100.0, 100.0, 100.0, 85.0, 85.0, 85.0]))

    assert len(fills) == 1                                 # 只有开仓，没有止损平仓
    assert strategy.qty_seen[-1] == 500                    # 仓位仍在

    warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "confirm_exit 否决" in r.getMessage()
    ]
    # 风险闸门逐 bar 重试，但同一 (标的, 原因) 只在首次用 WARNING
    assert len(warnings) == 1
    assert EXIT_STOP_LOSS in warnings[0].getMessage()


def test_confirm_exit_veto_marks_order_cancelled():
    class Veto(BuyOnce):
        def confirm_exit(self, ctx, symbol, qty, price, exit_reason) -> bool:
            return False

    broker = _broker_with_position()
    broker.bind_order_hooks(Veto(), None)
    order = broker.sell("AAPL", 100, exit_reason=EXIT_STOP_LOSS)

    assert order.status is OrderStatus.CANCELLED
    assert broker.positions.get("AAPL").qty == 100


def test_confirm_exit_sees_signal_reason_for_untagged_exit():
    seen: list[str] = []

    class Recording(BuyOnce):
        def confirm_exit(self, ctx, symbol, qty, price, exit_reason) -> bool:
            seen.append(exit_reason)
            return True

    broker = _broker_with_position()
    broker.bind_order_hooks(Recording(), None)
    broker.sell("AAPL", 100)
    assert seen == ["signal"]


def test_increase_on_existing_position_is_treated_as_entry():
    """加仓不是平仓：净持仓为正时的 BUY 必须走 confirm_entry。"""
    calls: list[str] = []

    class Recording(BuyOnce):
        def confirm_entry(self, ctx, symbol, qty, price, entry_tag) -> bool:
            calls.append("entry")
            return True

        def confirm_exit(self, ctx, symbol, qty, price, exit_reason) -> bool:
            calls.append("exit")
            return True

    broker = _broker_with_position()
    broker.bind_order_hooks(Recording(), None)
    broker.buy("AAPL", 10)
    assert calls == ["entry"]


# ── 4. custom_*_price 必须产出 LIMIT 单 ────────────────────────


def test_custom_entry_price_produces_limit_order():
    class LimitEntry(BuyOnce):
        name = "limit_entry"

        def custom_entry_price(self, ctx, symbol, proposed) -> float | None:
            return 95.0

    strategy = LimitEntry()
    fills = _run(strategy, _bars([100.0, 100.0, 95.0, 95.0]))

    order = strategy.orders[0]
    assert order.order_type is OrderType.LIMIT      # 不是「按 95 市价成交」
    assert order.limit_price == pytest.approx(95.0)

    # 100 那根 bar 不该成交，跌到 95 才成交，且成交价就是限价
    assert len(fills) == 1
    assert fills[0]["price"] == pytest.approx(95.0)
    assert fills[0]["filled_at"] == (START + timedelta(days=2)).isoformat()


def test_custom_exit_price_produces_limit_order():
    class LimitExit(BuyOnce):
        def custom_exit_price(self, ctx, symbol, proposed, exit_reason) -> float | None:
            return 130.0

    broker = _broker_with_position()
    broker.bind_order_hooks(LimitExit(), None)
    order = broker.sell("AAPL", 100, exit_reason="signal")

    assert order.status is OrderStatus.PENDING
    assert order.order_type is OrderType.LIMIT
    assert order.limit_price == pytest.approx(130.0)


def test_custom_price_replaces_limit_price_on_existing_limit_order():
    class LimitEntry(BuyOnce):
        def custom_entry_price(self, ctx, symbol, proposed) -> float | None:
            return 88.0

    broker = _broker_with_position()
    broker.bind_order_hooks(LimitEntry(), None)
    order = broker.buy("AAPL", 10, order_type=OrderType.LIMIT, limit_price=99.0)

    assert order.order_type is OrderType.LIMIT
    assert order.limit_price == pytest.approx(88.0)


def test_custom_price_is_ignored_for_trailing_stop_order(caplog):
    """把触发类订单转成限价会让止损单可能不成交 —— 宁可忽略并告警。"""
    caplog.set_level(logging.WARNING, logger=HOOK_LOGGER)

    class LimitExit(BuyOnce):
        def custom_exit_price(self, ctx, symbol, proposed, exit_reason) -> float | None:
            return 130.0

    broker = _broker_with_position()
    broker.bind_order_hooks(LimitExit(), None)
    order = broker.sell(
        "AAPL", 100, exit_reason="signal",
        order_type=OrderType.TRAILING_STOP, trailing_pct=0.05,
    )

    assert order.order_type is OrderType.TRAILING_STOP
    assert order.limit_price is None
    assert any("被忽略" in r.getMessage() for r in caplog.records)


def test_non_positive_custom_price_fails_loudly():
    class BadPrice(BuyOnce):
        def custom_entry_price(self, ctx, symbol, proposed) -> float | None:
            return -1.0

    broker = _broker_with_position()
    broker.bind_order_hooks(BadPrice(), None)
    with pytest.raises(StrategyContractError, match="custom_entry_price"):
        broker.buy("AAPL", 10)


# ── 5. 挂单超时与 TimeInForce 取更早者 ─────────────────────────


class NeverFillingEntry(StrategyBase):
    """挂一张永远成交不了的限价买单，用来观察它是怎么被撤掉的。"""

    name = "never_filling"
    time_in_force = TimeInForce.GTC

    def on_start(self, ctx: StrategyContext) -> None:
        self._done = False
        self.orders: list = []

    def on_bar(self, ctx: StrategyContext) -> None:
        if self._done:
            return
        self.orders.append(
            ctx.broker.buy(
                ctx.bar.symbol, 10,
                order_type=OrderType.LIMIT,
                limit_price=1.0,                    # 永远触及不到
                time_in_force=self.time_in_force,
            )
        )
        self._done = True


def test_entry_timeout_wins_over_later_day_tif():
    """日内 bar 上 DAY 尚未过期，30 分钟超时先到 → 按超时撤单。"""

    class Fast(NeverFillingEntry):
        name = "fast_timeout"
        entry_timeout_minutes = 30
        time_in_force = TimeInForce.DAY

    strategy = Fast()
    bars = _bars(
        [100.0] * 4, step=timedelta(hours=1), frequency=Frequency.HOUR_1
    )
    _run(strategy, bars)

    order = strategy.orders[0]
    assert order.status is OrderStatus.CANCELLED
    assert "挂单超时" in order.reject_reason


def test_day_tif_wins_over_later_timeout():
    """日线 bar 上 DAY 隔日即过期，而 7 天的超时还没到 → 按 TIF 撤单。"""

    class Slow(NeverFillingEntry):
        name = "slow_timeout"
        entry_timeout_minutes = 10_000        # ≈ 6.9 天
        time_in_force = TimeInForce.DAY

    strategy = Slow()
    _run(strategy, _bars([100.0] * 4))

    order = strategy.orders[0]
    assert order.status is OrderStatus.CANCELLED
    assert "DAY 订单过期" in order.reject_reason


def test_timeout_alone_cancels_a_gtc_order():
    class TimedGtc(NeverFillingEntry):
        name = "timed_gtc"
        entry_timeout_minutes = 30

    strategy = TimedGtc()
    bars = _bars([100.0] * 4, step=timedelta(hours=1), frequency=Frequency.HOUR_1)
    _run(strategy, bars)

    assert strategy.orders[0].status is OrderStatus.CANCELLED
    assert "挂单超时" in strategy.orders[0].reject_reason


def test_order_survives_until_timeout_elapses():
    class LongTimeout(NeverFillingEntry):
        name = "long_timeout"
        entry_timeout_minutes = 10_000

    strategy = LongTimeout()
    bars = _bars([100.0] * 4, step=timedelta(hours=1), frequency=Frequency.HOUR_1)
    _run(strategy, bars)

    # 4 根小时线跑完还没到 10000 分钟，订单只会被收尾的 cancel_all_pending 撤掉
    assert strategy.orders[0].reject_reason is None


def test_non_positive_timeout_fails_loudly():
    class BadTimeout(BuyOnce):
        entry_timeout_minutes = 0

    broker = _broker_with_position()
    broker.bind_order_hooks(BadTimeout(), None)
    with pytest.raises(StrategyContractError, match="entry_timeout_minutes"):
        broker.buy("AAPL", 10)


# ── 契约违规必须炸出来，不能被引擎吞掉 ──────────────────────────
#
# 回归用例：`_step` 对 `strategy.on_bars` 有 `except Exception: logger.exception(...)`，
# 用来容忍「某根 bar 的运行时意外」。但钩子返回非法值是**契约违规** ——
# 确定性的、每根 bar 都会重复、不改代码永远不会好。
# 吞掉它的后果是一份「跑完了、报告正常、但钩子从未生效」的假结果。

class _BadPriceHook(StrategyBase):
    """`custom_entry_price` 返回负数 —— 典型的配置写错。"""

    name = "bad_price_hook"

    def on_bar(self, ctx: StrategyContext) -> None:
        if ctx.qty == 0:
            ctx.buy(10)

    def custom_entry_price(self, ctx, symbol, proposed):  # noqa: ARG002
        return -1.0


class _RuntimeErrorStrategy(StrategyBase):
    """普通运行时错误 —— 应当被容忍，回测继续跑完。"""

    name = "runtime_error"

    def on_bar(self, ctx: StrategyContext) -> None:
        raise ZeroDivisionError("模拟策略自身的 bug")


def test_contract_violation_aborts_the_backtest() -> None:
    # Arrange / Act / Assert：非法钩子返回值必须一路抛到调用方
    with pytest.raises(StrategyContractError, match="必须返回正数"):
        _run(_BadPriceHook(), _bars([100.0, 101.0, 102.0]))


def test_runtime_error_is_tolerated_and_backtest_completes() -> None:
    # Arrange / Act：策略每根 bar 都抛 ZeroDivisionError
    fills = _run(_RuntimeErrorStrategy(), _bars([100.0, 101.0, 102.0]))

    # Assert：回测跑完（没有成交，但也没有抛出）—— 与契约违规的处理相反
    assert fills == []
