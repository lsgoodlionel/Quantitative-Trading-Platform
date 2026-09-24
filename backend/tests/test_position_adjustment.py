"""L4 仓位调整钩子测试（Wave L-c）

对应契约 docs/contracts/waveLc-position-order-hooks.md §二 与 §五.2。

设计参考自 freqtrade `IStrategy.adjust_trade_position` 的语义（GPL-3.0），
此处为独立实现，未复制其任何代码。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest

from app.data.models import Bar, Frequency, Market
from app.engine.backtest.broker_adjust import EXIT_PARTIAL
from app.engine.backtest.commission import CommissionResult
from app.engine.backtest.portfolio_broker import PortfolioBroker
from app.engine.backtest.portfolio_engine import (
    PortfolioBacktestConfig,
    PortfolioBacktestEngine,
)
from app.engine.backtest.roundtrips import build_round_trips
from app.engine.backtest.slippage import NoSlippage
from app.engine.backtest.trade import EXIT_STOP_LOSS, EXIT_TRAILING_STOP
from app.strategy.base import StrategyBase
from app.strategy.context import StrategyContext

START = datetime(2024, 1, 2, tzinfo=UTC)
ADJUST_LOGGER = "app.engine.backtest.broker_adjust"


class _ZeroCommission:
    """零费率佣金模型（隔离费用对断言的干扰）。"""

    def calculate(self, price: float, qty: int, side: str) -> CommissionResult:
        return CommissionResult(commission=0.0, fees=0.0, total=0.0)


def _bars(closes: list[float], symbol: str = "AAPL") -> list[Bar]:
    """每根 bar 的开盘价 == 收盘价，使「下一 bar 开盘成交」的价格可预测。"""
    return [
        Bar(
            time=START + timedelta(days=i),
            symbol=symbol,
            market=Market.US,
            frequency=Frequency.DAY_1,
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


def _run(strategy: StrategyBase, closes: list[float], **cfg_kwargs) -> list[dict]:
    engine = PortfolioBacktestEngine(_config(**cfg_kwargs))
    return engine.run_single(strategy, _bars(closes)).fills


def _exits(fills: list[dict]) -> list[dict]:
    return [f for f in fills if f["exit_reason"] not in (None, "")]


# ── 测试策略 ──────────────────────────────────────────────────────


class BuyOnce(StrategyBase):
    """首根 bar 买入一次后不再动作，把后续全部交给风险闸门与仓位调整。"""

    name = "buy_once"

    def on_start(self, ctx: StrategyContext) -> None:
        self._done = False
        self.trades_seen: list = []

    def on_bar(self, ctx: StrategyContext) -> None:
        self.trades_seen.append(ctx.broker.open_trades.get(ctx.bar.symbol))
        if self._done:
            return
        qty = int(ctx.cash * 0.5 / ctx.bar.close)
        if qty > 0:
            ctx.buy(qty)
            self._done = True


class ShortOnce(BuyOnce):
    name = "short_once"

    def on_bar(self, ctx: StrategyContext) -> None:
        self.trades_seen.append(ctx.broker.open_trades.get(ctx.bar.symbol))
        if self._done:
            return
        qty = int(ctx.cash * 0.5 / ctx.bar.close)
        if qty > 0:
            ctx.short(qty)
            self._done = True


class DcaOnce(BuyOnce):
    """浮盈超过 10% 时加仓一次（金额 12000）。"""

    name = "dca_once"
    position_adjustment_enable = True
    stoploss = -0.50
    trailing_stop = True
    trailing_stop_positive = 0.03

    def on_start(self, ctx: StrategyContext) -> None:
        super().on_start(ctx)
        self._adjusted = False

    def adjust_position(self, ctx, trade, current_profit: float) -> float | None:
        if self._adjusted or current_profit <= 0.10:
            return None
        self._adjusted = True
        return 12_000.0


# ── 1. 关闭时必须是 no-op ───────────────────────────────────────


def test_adjustment_disabled_by_default_is_noop():
    class WouldAdjust(BuyOnce):
        name = "would_adjust"
        # 刻意不开 position_adjustment_enable

        def adjust_position(self, ctx, trade, current_profit: float) -> float | None:
            return 10_000.0

    fills = _run(WouldAdjust(), [100.0, 100.0, 100.0, 100.0])
    assert len(fills) == 1                       # 只有最初那一笔开仓
    assert _exits(fills) == []


def test_base_strategy_adjust_position_returns_none():
    assert StrategyBase.position_adjustment_enable is False
    assert BuyOnce().adjust_position(None, None, 0.0) is None


def test_broker_adjust_positions_is_noop_without_enable():
    broker = PortfolioBroker(initial_cash=10_000.0, market=Market.US)
    assert broker.adjust_positions(BuyOnce(), None, {"AAPL": 100.0}, START) == []


# ── 2. 加仓走 K-d 的持仓更新路径 ────────────────────────────────


def test_dca_increase_reuses_shared_trade_update_path():
    """
    加仓后 `Trade.open_price` 必须对齐到新的平均成本、收益率极值必须重置。

    若另写一份持仓更新逻辑而不走 `next_trade`，峰值会停在加仓前的 +20%，
    加仓后 +16.1% 会被当成 3.9% 的回撤 —— 追踪止损（距离 3%）会在毫无回撤的
    情况下把刚加的仓打掉。本用例断言「没有这笔追踪止损」。
    """
    strategy = DcaOnce()
    fills = _run(strategy, [100.0, 100.0, 120.0, 120.0, 120.0, 120.0])

    assert len(fills) == 2                        # 开仓 500 股 + 加仓 100 股
    assert [f["qty"] for f in fills] == [500, 100]
    assert _exits(fills) == []                    # 追踪止损没有被虚假触发

    final = strategy.trades_seen[-1]
    assert final.qty == 600
    # (500×100 + 100×120) / 600
    assert final.open_price == pytest.approx(103.3333, abs=1e-3)
    # 峰值以新基准重算：120/103.3333 - 1 ≈ 16.1%，而不是加仓前的 20%
    assert final.max_profit_seen == pytest.approx(0.16129, abs=1e-4)


def test_dca_without_reset_would_have_triggered_trailing_stop():
    """反证：把加仓关掉后同一条价格路径也不该有平仓，确保上一用例不是「碰巧没触发」。"""

    class NoDca(DcaOnce):
        name = "no_dca"
        position_adjustment_enable = False

    fills = _run(NoDca(), [100.0, 100.0, 120.0, 120.0, 120.0, 120.0])
    assert len(fills) == 1
    assert not any(f["exit_reason"] == EXIT_TRAILING_STOP for f in fills)


def test_short_position_increase_uses_short_leg():
    class ShortDca(ShortOnce):
        name = "short_dca"
        position_adjustment_enable = True

        def on_start(self, ctx: StrategyContext) -> None:
            super().on_start(ctx)
            self._adjusted = False

        def adjust_position(self, ctx, trade, current_profit: float) -> float | None:
            if self._adjusted or current_profit <= 0.05:
                return None
            self._adjusted = True
            return 9_000.0

    fills = _run(ShortDca(), [100.0, 100.0, 90.0, 90.0, 90.0], allow_short=True)
    assert len(fills) == 2
    assert all(f["side"] == "SELL" for f in fills)     # 加空仓仍是卖出
    assert _exits(fills) == []                        # 加空不该被记成平仓


# ── 3. 部分平仓 ─────────────────────────────────────────────────


class PartialExitOnce(BuyOnce):
    name = "partial_exit_once"
    position_adjustment_enable = True

    def on_start(self, ctx: StrategyContext) -> None:
        super().on_start(ctx)
        self._adjusted = False

    def adjust_position(self, ctx, trade, current_profit: float) -> float | None:
        if self._adjusted or current_profit <= 0.10:
            return None
        self._adjusted = True
        return -6_000.0


def test_partial_exit_produces_tagged_fill_and_round_trip():
    fills = _run(PartialExitOnce(), [100.0, 100.0, 120.0, 120.0, 120.0])

    exits = _exits(fills)
    assert len(exits) == 1
    assert exits[0]["exit_reason"] == EXIT_PARTIAL
    assert exits[0]["qty"] == 50                  # 6000 / 120
    assert exits[0]["side"] == "SELL"

    trips = build_round_trips(fills)
    assert len(trips) == 1
    trip = trips[0]
    assert trip.direction == "long"
    assert trip.exit_reason == EXIT_PARTIAL
    assert trip.qty == pytest.approx(50)
    assert trip.entry_price == pytest.approx(100.0)
    assert trip.exit_price == pytest.approx(120.0)
    assert trip.pnl == pytest.approx(1_000.0)     # 50 × (120 − 100)


def test_reduction_beyond_market_value_closes_whole_position():
    class OverSell(PartialExitOnce):
        name = "over_sell"

        def adjust_position(self, ctx, trade, current_profit: float) -> float | None:
            if self._adjusted or current_profit <= 0.10:
                return None
            self._adjusted = True
            return -1_000_000.0        # 远超持仓市值

    strategy = OverSell()
    fills = _run(strategy, [100.0, 100.0, 120.0, 120.0, 120.0])

    exits = _exits(fills)
    assert len(exits) == 1
    assert exits[0]["qty"] == 500                 # 全平，不多不少
    # 全平之后本笔交易被关闭，绝不留下反向持仓
    assert strategy.trades_seen[-1] is None


# ── 4. 调整次数上限 ─────────────────────────────────────────────


class GreedyDca(BuyOnce):
    """每根 bar 都想加仓 1000，用来撞 max_position_adjustments 的上限。"""

    name = "greedy_dca"
    position_adjustment_enable = True
    max_position_adjustments = 1

    def adjust_position(self, ctx, trade, current_profit: float) -> float | None:
        return 1_000.0


def test_max_position_adjustments_is_enforced_and_warned(caplog):
    caplog.set_level(logging.WARNING, logger=ADJUST_LOGGER)
    fills = _run(GreedyDca(), [100.0] * 8)

    # 开仓 1 笔 + 唯一一次被允许的加仓
    assert len(fills) == 2
    assert fills[1]["qty"] == 10                  # 1000 / 100

    warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "max_position_adjustments" in r.getMessage()
    ]
    assert len(warnings) == 1                     # 首次 WARNING，之后降为 DEBUG 不刷屏
    assert "AAPL" in warnings[0].getMessage()


def test_adjustment_quota_resets_for_a_new_trade():
    """上一笔用满配额不该连累下一笔 —— 清仓重开即是新的一笔交易。"""

    class ReopenDca(StrategyBase):
        name = "reopen_dca"
        position_adjustment_enable = True
        max_position_adjustments = 1

        def on_start(self, ctx: StrategyContext) -> None:
            self.adjust_calls = 0

        def on_bar(self, ctx: StrategyContext) -> None:
            # bar0 开仓、bar3 清仓、bar5 重新开仓
            if ctx.bar.time == START:
                ctx.buy(100)
            elif ctx.bar.time == START + timedelta(days=3):
                ctx.sell_all()
            elif ctx.bar.time == START + timedelta(days=5):
                ctx.buy(100)

        def adjust_position(self, ctx, trade, current_profit: float) -> float | None:
            self.adjust_calls += 1
            return 500.0

    strategy = ReopenDca()
    fills = _run(strategy, [100.0] * 9)

    increases = [f for f in fills if f["side"] == "BUY" and f["qty"] == 5]
    assert len(increases) == 2       # 第一笔一次、重开后的第二笔又有一次


# ── 5. 与风险闸门的先后顺序 ─────────────────────────────────────


def test_position_with_pending_risk_exit_is_not_adjusted():
    """契约 §2.1：已经该止损的仓位不应该再被加仓。"""

    class DipBuyer(BuyOnce):
        name = "dip_buyer"
        position_adjustment_enable = True
        stoploss = -0.10

        def on_start(self, ctx: StrategyContext) -> None:
            super().on_start(ctx)
            self.adjust_profits: list[float] = []

        def adjust_position(self, ctx, trade, current_profit: float) -> float | None:
            self.adjust_profits.append(round(current_profit, 4))
            return 5_000.0 if current_profit < -0.05 else None

    strategy = DipBuyer()
    fills = _run(strategy, [100.0, 100.0, 100.0, 85.0, 85.0, 85.0])

    exits = _exits(fills)
    assert len(exits) == 1
    assert exits[0]["exit_reason"] == EXIT_STOP_LOSS
    assert len(fills) == 2                       # 开仓 + 止损，没有任何补仓
    # 止损那根 bar 上根本没有问过策略要不要加仓
    assert all(p > -0.05 for p in strategy.adjust_profits)
