"""策略级止损 / ROI / 追踪止损测试（Wave K-d / K4）

对应契约 docs/contracts/waveKd-strategy-hooks-insight.md §一 与 §三.2。

设计参考自 freqtrade 的 IStrategy 风险闸门语义（GPL-3.0），此处为独立实现，
未复制其任何代码。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.data.models import Bar, Frequency, Market
from app.engine.backtest.broker import OrderSide
from app.engine.backtest.commission import CommissionResult
from app.engine.backtest.portfolio_engine import (
    PortfolioBacktestConfig,
    PortfolioBacktestEngine,
)
from app.engine.backtest.slippage import NoSlippage
from app.engine.backtest.trade import (
    EXIT_ROI,
    EXIT_STOP_LOSS,
    EXIT_TRAILING_STOP,
    ExitRules,
    Trade,
    evaluate_exit,
    roi_threshold,
)
from app.strategy.base import StrategyBase
from app.strategy.context import StrategyContext

START = datetime(2024, 1, 2, tzinfo=UTC)


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


# ── 测试策略 ──────────────────────────────────────────────────────


class BuyOnce(StrategyBase):
    """首根 bar 买入一次后不再动作，把平仓完全交给风险闸门。"""

    name = "buy_once"

    def on_start(self, ctx: StrategyContext) -> None:
        self._done = False

    def on_bar(self, ctx: StrategyContext) -> None:
        if self._done:
            return
        qty = int(ctx.cash * 0.5 / ctx.bar.close)
        if qty > 0:
            ctx.buy(qty)
            self._done = True


class ShortOnce(BuyOnce):
    name = "short_once"

    def on_bar(self, ctx: StrategyContext) -> None:
        if self._done:
            return
        qty = int(ctx.cash * 0.5 / ctx.bar.close)
        if qty > 0:
            ctx.short(qty)
            self._done = True


def _run(strategy: StrategyBase, closes: list[float], **cfg_kwargs) -> list[dict]:
    engine = PortfolioBacktestEngine(_config(**cfg_kwargs))
    result = engine.run_single(strategy, _bars(closes))
    return result.fills


def _exits(fills: list[dict]) -> list[dict]:
    return [f for f in fills if f["exit_reason"] not in (None, "")]


# ── 1. 默认不配置时必须是 no-op ─────────────────────────────────


def test_default_config_produces_no_exit_orders():
    fills = _run(BuyOnce(), [100.0, 100.0, 80.0, 60.0, 60.0])
    assert len(fills) == 1                      # 只有开仓那一笔
    assert _exits(fills) == []


def test_exit_rules_disabled_by_default():
    rules = BuyOnce().exit_rules()
    assert rules == ExitRules()
    assert rules.is_enabled is False


# ── 2. 固定止损 ────────────────────────────────────────────────


class StoplossStrategy(BuyOnce):
    name = "stoploss"
    stoploss = -0.10


def test_fixed_stoploss_exits_on_next_bar():
    closes = [100.0, 100.0, 100.0, 88.0, 88.0, 88.0]
    bars = _bars(closes)
    fills = _run(StoplossStrategy(), closes)

    exits = _exits(fills)
    assert len(exits) == 1
    assert exits[0]["exit_reason"] == EXIT_STOP_LOSS
    # 第 3 根 bar（下标 3）跌破阈值 → 第 4 根 bar 开盘成交
    assert exits[0]["filled_at"] == bars[4].time.isoformat()
    assert exits[0]["side"] == OrderSide.SELL.value


def test_stoploss_not_triggered_above_threshold():
    fills = _run(StoplossStrategy(), [100.0, 100.0, 95.0, 95.0, 95.0])
    assert _exits(fills) == []


def test_short_trade_stoploss_uses_inverted_profit():
    closes = [100.0, 100.0, 112.0, 112.0, 112.0]
    bars = _bars(closes)
    fills = _run(ShortOnce(), closes, allow_short=True)

    exits = _exits(fills)
    assert len(exits) == 0   # ShortOnce 本身没有配置止损

    class ShortStoploss(ShortOnce):
        name = "short_stoploss"
        stoploss = -0.10

    fills = _run(ShortStoploss(), closes, allow_short=True)
    exits = _exits(fills)
    assert len(exits) == 1
    assert exits[0]["exit_reason"] == EXIT_STOP_LOSS
    assert exits[0]["side"] == OrderSide.BUY.value        # 平空是买回
    assert exits[0]["filled_at"] == bars[3].time.isoformat()


def test_risk_gate_runs_before_on_bar():
    """契约 §1.3：风险闸门必须先于策略意图执行，否则策略会在该止损的仓位上继续加仓。"""

    class Pyramiding(StrategyBase):
        name = "pyramiding"
        stoploss = -0.10

        def on_start(self, ctx: StrategyContext) -> None:
            self.pending_seen: list[list[str | None]] = []

        def on_bar(self, ctx: StrategyContext) -> None:
            self.pending_seen.append([o.exit_reason for o in ctx.broker._pending])
            ctx.buy(1)

    strategy = Pyramiding()
    _run(strategy, [100.0, 100.0, 100.0, 80.0, 80.0])

    # 跌破阈值那根 bar 上，策略被调用时已经能看到在途的止损单
    assert any(EXIT_STOP_LOSS in seen for seen in strategy.pending_seen)


# ── 3. minimal_roi 阶梯 ────────────────────────────────────────


class RoiLadderStrategy(BuyOnce):
    name = "roi_ladder"
    # 0 分钟起要求 50% 收益；2 天（2880 分钟）后只要求 1%
    minimal_roi = {0: 0.50, 2880: 0.01}


def test_minimal_roi_ladder_relaxes_with_holding_time():
    closes = [100.0, 100.0, 102.0, 102.0, 102.0, 102.0]
    bars = _bars(closes)
    fills = _run(RoiLadderStrategy(), closes)

    exits = _exits(fills)
    assert len(exits) == 1
    assert exits[0]["exit_reason"] == EXIT_ROI
    # 持仓自 bars[1] 起：bars[2] 时只有 1 天（阈值 50%，不触发），
    # bars[3] 时满 2 天 → 阈值降到 1% → 触发 → bars[4] 成交
    assert exits[0]["filled_at"] == bars[4].time.isoformat()


def test_roi_threshold_picks_largest_key_not_exceeding_duration():
    ladder = {0: 0.10, 60: 0.05, 120: 0.0}
    assert roi_threshold(ladder, 0) == 0.10
    assert roi_threshold(ladder, 59) == 0.10
    assert roi_threshold(ladder, 60) == 0.05
    assert roi_threshold(ladder, 119) == 0.05
    assert roi_threshold(ladder, 10_000) == 0.0
    assert roi_threshold(None, 10) is None


# ── 4. 追踪止损 ────────────────────────────────────────────────


class TrailingStrategy(BuyOnce):
    name = "trailing"
    stoploss = -0.50                      # 固定止损放得很远，隔离追踪逻辑
    trailing_stop = True
    trailing_stop_positive = 0.03
    trailing_stop_positive_offset = 0.05


def test_trailing_stop_inactive_before_offset_and_follows_peak_after():
    #        0      1      2       3        4       5       6
    closes = [100.0, 100.0, 104.0, 100.4, 110.0, 106.0, 106.0]
    bars = _bars(closes)
    fills = _run(TrailingStrategy(), closes)

    exits = _exits(fills)
    # bars[3] 从 +4% 回落到 +0.4%（回撤 3.6% > 3%），但峰值未达 5% offset → 不触发
    # bars[5] 峰值 +10% 后回落到 +6%（回撤 4% > 3%）→ 触发 → bars[6] 成交
    assert len(exits) == 1
    assert exits[0]["exit_reason"] == EXIT_TRAILING_STOP
    assert exits[0]["filled_at"] == bars[6].time.isoformat()


def test_trailing_without_distance_config_raises():
    class BadTrailing(BuyOnce):
        name = "bad_trailing"
        trailing_stop = True               # 既没有 stoploss 也没有 trailing_stop_positive

    with pytest.raises(ValueError, match="追踪止损"):
        _run(BadTrailing(), [100.0, 100.0, 100.0])


# ── 5. custom 钩子覆盖类级配置 ─────────────────────────────────


class CustomStoplossStrategy(BuyOnce):
    name = "custom_stoploss"
    stoploss = -0.50

    def custom_stoploss(self, ctx, trade, current_profit: float) -> float | None:
        return -0.02


def test_custom_stoploss_overrides_class_level_config():
    closes = [100.0, 100.0, 97.0, 97.0, 97.0]
    bars = _bars(closes)
    fills = _run(CustomStoplossStrategy(), closes)

    exits = _exits(fills)
    assert len(exits) == 1
    assert exits[0]["exit_reason"] == EXIT_STOP_LOSS
    assert exits[0]["filled_at"] == bars[3].time.isoformat()


class CustomRoiStrategy(BuyOnce):
    name = "custom_roi"
    minimal_roi = {0: 0.50}

    def custom_roi(self, ctx, trade, current_profit: float) -> float | None:
        return 0.015


def test_custom_roi_overrides_class_level_config():
    closes = [100.0, 100.0, 102.0, 102.0, 102.0]
    bars = _bars(closes)
    fills = _run(CustomRoiStrategy(), closes)

    exits = _exits(fills)
    assert len(exits) == 1
    assert exits[0]["exit_reason"] == EXIT_ROI
    assert exits[0]["filled_at"] == bars[3].time.isoformat()


def test_custom_hook_receives_live_trade_and_profit():
    seen: list[tuple[str, float]] = []

    class Recording(BuyOnce):
        name = "recording_hook"
        stoploss = -0.50

        def custom_stoploss(self, ctx, trade, current_profit: float) -> float | None:
            seen.append((trade.symbol, round(current_profit, 6)))
            return None

    _run(Recording(), [100.0, 100.0, 110.0, 110.0])
    assert seen[0][0] == "AAPL"
    assert 0.09 < max(p for _, p in seen) < 0.11


# ── 6. Trade 与纯函数单元测试 ──────────────────────────────────


def test_trade_current_profit_is_direction_aware():
    long_trade = Trade(
        symbol="AAPL", direction="long", open_time=START, open_price=100.0, qty=10
    )
    short_trade = Trade(
        symbol="AAPL", direction="short", open_time=START, open_price=100.0, qty=10
    )
    assert long_trade.current_profit(110.0) == pytest.approx(0.10)
    assert short_trade.current_profit(110.0) == pytest.approx(-0.10)
    assert short_trade.current_profit(90.0) == pytest.approx(0.10)


def test_trade_duration_minutes():
    trade = Trade(
        symbol="AAPL", direction="long", open_time=START, open_price=100.0, qty=10
    )
    assert trade.duration_minutes(START) == 0
    assert trade.duration_minutes(START + timedelta(hours=2)) == 120
    assert trade.duration_minutes(START - timedelta(hours=2)) == 0


def test_trade_mark_profit_is_immutable():
    trade = Trade(
        symbol="AAPL", direction="long", open_time=START, open_price=100.0, qty=10
    )
    marked = trade.mark_profit(0.08)
    assert trade.max_profit_seen == 0.0        # 原对象不被就地修改
    assert marked.max_profit_seen == pytest.approx(0.08)
    assert marked.mark_profit(-0.05).min_profit_seen == pytest.approx(-0.05)
    assert marked.mark_profit(-0.05).max_profit_seen == pytest.approx(0.08)


def test_evaluate_exit_priority_is_roi_then_stoploss_then_trailing():
    trade = Trade(
        symbol="AAPL", direction="long", open_time=START, open_price=100.0, qty=10,
        max_profit_seen=0.20,
    )
    rules = ExitRules(
        stoploss=-0.05, minimal_roi={0: 0.10}, trailing_stop=True,
        trailing_stop_positive=0.02,
    )
    # 同时满足 ROI 与追踪止损时，ROI 优先
    assert evaluate_exit(rules, trade, profit=0.15, duration_minutes=0) == EXIT_ROI
    # 只满足止损
    assert evaluate_exit(rules, trade, profit=-0.06, duration_minutes=0) == EXIT_STOP_LOSS
    # 只满足追踪止损
    assert (
        evaluate_exit(rules, trade, profit=0.05, duration_minutes=0)
        == EXIT_TRAILING_STOP
    )
    # 都不满足（去掉 ROI 档位后，+19% 距峰值 20% 只回撤 1% < 2%）
    no_roi = ExitRules(stoploss=-0.05, trailing_stop=True, trailing_stop_positive=0.02)
    assert evaluate_exit(no_roi, trade, profit=0.19, duration_minutes=0) is None


def test_exit_rules_validation_rejects_positive_stoploss():
    with pytest.raises(ValueError, match="stoploss"):
        ExitRules(stoploss=0.10).validate()


def test_custom_hook_return_values_are_validated():
    """运行期覆盖若不校验就绕开了类级配置的自检 —— 正数止损会把仓位无声打光。"""
    trade = Trade(
        symbol="AAPL", direction="long", open_time=START, open_price=100.0, qty=10
    )
    rules = ExitRules(stoploss=-0.10)

    with pytest.raises(ValueError, match="custom_stoploss"):
        evaluate_exit(rules, trade, profit=0.0, duration_minutes=0, custom_stoploss=0.05)
    with pytest.raises(ValueError, match="custom_roi"):
        evaluate_exit(rules, trade, profit=0.0, duration_minutes=0, custom_roi=-0.01)


def test_strategy_returning_positive_custom_stoploss_fails_loudly():
    class BadCustom(BuyOnce):
        name = "bad_custom"
        stoploss = -0.10

        def custom_stoploss(self, ctx, trade, current_profit: float) -> float | None:
            return 0.05

    with pytest.raises(ValueError, match="custom_stoploss"):
        _run(BadCustom(), [100.0, 100.0, 100.0, 100.0])


def test_broker_check_exit_conditions_is_noop_without_rules():
    from app.engine.backtest.portfolio_broker import PortfolioBroker

    broker = PortfolioBroker(initial_cash=10_000.0, market=Market.US)
    assert broker.check_exit_conditions(BuyOnce(), None, {"AAPL": 100.0}, START) == []
    assert broker.open_trades == {}
