"""多标的组合回测引擎测试（Wave K-c / K1）

对应契约 docs/contracts/waveKc-portfolio-engine.md §七 验收清单。
"""

from __future__ import annotations

import math
import time
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.data.models import Bar, Frequency, Market
from app.engine.backtest.engine import BacktestConfig, BacktestEngine
from app.engine.backtest.portfolio_engine import (
    PortfolioBacktestConfig,
    PortfolioBacktestEngine,
)
from app.engine.backtest.slippage import NoSlippage
from app.strategy.base import PortfolioStrategyBase, StrategyBase
from app.strategy.context import PortfolioContext, StrategyContext

START = datetime(2024, 1, 2, tzinfo=UTC)


def _make_bars(
    symbol: str,
    n: int = 60,
    base_price: float = 100.0,
    trend: float = 0.0,
    skip: set[int] | None = None,
    start: datetime = START,
) -> list[Bar]:
    """生成确定性日线序列；skip 中的下标不产生 bar（模拟停牌）。"""
    skip = skip or set()
    bars: list[Bar] = []
    price = base_price
    for i in range(n):
        price = price * (1 + trend)
        if i in skip:
            continue
        bars.append(
            Bar(
                time=start + timedelta(days=i),
                symbol=symbol,
                market=Market.US,
                frequency=Frequency.DAY_1,
                open=price,
                high=price * 1.01,
                low=price * 0.99,
                close=price,
                volume=1_000_000,
            )
        )
    return bars


def _config(**kwargs) -> PortfolioBacktestConfig:
    defaults = {
        "initial_cash": 100_000.0,
        "market": Market.US,
        "slippage_model": NoSlippage(),
        "commission_model": _ZeroCommission(),
    }
    defaults.update(kwargs)
    return PortfolioBacktestConfig(**defaults)


class _ZeroCommission:
    """零费率佣金模型（隔离费用对断言的干扰）。"""

    def calculate(self, price: float, qty: int, side: str):
        from app.engine.backtest.commission import CommissionResult

        return CommissionResult(commission=0.0, fees=0.0, total=0.0)


# ── 测试策略 ──────────────────────────────────────────────────────


class EqualWeightBuyAndHold(PortfolioStrategyBase):
    """首个可交易时点等权买入并持有。"""

    name = "equal_weight_buy_hold"

    def on_start(self, ctx: PortfolioContext) -> None:
        self._done = False

    def on_bars(self, ctx: PortfolioContext) -> None:
        if self._done or not ctx.bars:
            return
        budget = ctx.cash / len(ctx.symbols)
        for symbol in ctx.symbols:
            bar = ctx.bars.get(symbol)
            if bar is None:
                return   # 等所有标的都有价再统一建仓
            ctx.buy(symbol, int(budget / bar.close))
        self._done = True


class NoopPortfolioStrategy(PortfolioStrategyBase):
    name = "noop"

    def on_bars(self, ctx: PortfolioContext) -> None:
        return


class RecordingStrategy(PortfolioStrategyBase):
    """记录每个时点看到的标的集合。"""

    name = "recording"

    def on_start(self, ctx: PortfolioContext) -> None:
        self.seen: list[tuple[datetime, tuple[str, ...]]] = []

    def on_bars(self, ctx: PortfolioContext) -> None:
        self.seen.append((ctx.time, tuple(sorted(ctx.bars))))


class TargetWeightOnce(PortfolioStrategyBase):
    """在指定时点下达一次目标权重，记录产出的 diff 订单。"""

    name = "target_weight_once"

    def __init__(self, weights: dict[str, float], at_index: int = 1) -> None:
        super().__init__()
        self._weights = weights
        self._at = at_index
        self.orders: list = []
        self._i = -1

    def on_bars(self, ctx: PortfolioContext) -> None:
        self._i += 1
        if self._i == self._at:
            self.orders = ctx.target_weight(self._weights)


class BuyEverythingEveryBar(PortfolioStrategyBase):
    """每根 bar 对所有标的各下 100 股买单 —— 用于压现金与持仓上限。"""

    name = "buy_everything"

    def on_bars(self, ctx: PortfolioContext) -> None:
        for symbol in ctx.symbols:
            if symbol in ctx.bars:
                ctx.buy(symbol, 100)


# ── 1. 组合净值 = 各标的净值加权和 ────────────────────────────────


class TestEqualWeightBuyAndHold:
    def test_portfolio_value_equals_sum_of_legs(self) -> None:
        # Arrange
        bars = {
            "AAA": _make_bars("AAA", n=40, base_price=100.0, trend=0.002),
            "BBB": _make_bars("BBB", n=40, base_price=50.0, trend=-0.001),
            "CCC": _make_bars("CCC", n=40, base_price=200.0, trend=0.0),
        }
        engine = PortfolioBacktestEngine(_config())

        # Act
        result = engine.run(EqualWeightBuyAndHold(), bars)

        # Assert
        assert result.symbols == ["AAA", "BBB", "CCC"]
        assert len(result.fills) == 3

        # 组合净值 == 剩余现金 + Σ(各腿持仓 × 各自末收盘价)
        last_close = {s: b[-1].close for s, b in bars.items()}
        held = {p["symbol"]: p["qty"] for p in result.report["positions"]}
        assert set(held) == {"AAA", "BBB", "CCC"}
        legs = sum(held[s] * last_close[s] for s in held)

        # report 里的现金是 2 位取整值，故用 abs 容差而非 rel
        assert result.final_value - legs == pytest.approx(
            result.report["final_cash"], abs=0.01
        )
        assert result.equity_curve.iloc[-1] == pytest.approx(result.final_value, rel=1e-12)

    def test_per_symbol_metrics_cover_every_symbol(self) -> None:
        bars = {
            "AAA": _make_bars("AAA", n=30, trend=0.002),
            "BBB": _make_bars("BBB", n=30, base_price=50.0, trend=0.001),
        }
        result = PortfolioBacktestEngine(_config()).run(EqualWeightBuyAndHold(), bars)

        assert set(result.per_symbol_metrics) == {"AAA", "BBB"}


# ── 2. 停牌标的用最后已知价估值 ───────────────────────────────────


class TestHaltedSymbol:
    def test_missing_bars_do_not_create_fake_drawdown(self) -> None:
        # Arrange: BBB 在第 10~19 个交易日停牌
        halted = set(range(10, 20))
        bars = {
            "AAA": _make_bars("AAA", n=30, base_price=100.0, trend=0.0),
            "BBB": _make_bars("BBB", n=30, base_price=100.0, trend=0.0, skip=halted),
        }
        engine = PortfolioBacktestEngine(_config())

        # Act
        result = engine.run(EqualWeightBuyAndHold(), bars)

        # Assert：全平价格 + 零佣金零滑点 → 净值应恒定，绝不出现假回撤
        assert result.metrics.max_drawdown == pytest.approx(0.0, abs=1e-9)
        assert result.equity_curve.min() == pytest.approx(
            result.equity_curve.max(), rel=1e-9
        )

    def test_timeline_is_union_of_all_symbol_timestamps(self) -> None:
        bars = {
            "AAA": _make_bars("AAA", n=10),
            "BBB": _make_bars("BBB", n=10, skip={3, 4}),
        }
        strategy = RecordingStrategy()

        PortfolioBacktestEngine(_config()).run(strategy, bars)

        assert len(strategy.seen) == 10
        assert strategy.seen[3][1] == ("AAA",)
        assert strategy.seen[5][1] == ("AAA", "BBB")

    def test_halted_symbol_keeps_position_and_last_price(self) -> None:
        bars = {
            "AAA": _make_bars("AAA", n=20, base_price=100.0),
            "BBB": _make_bars("BBB", n=20, base_price=100.0, skip={15, 16, 17, 18, 19}),
        }
        result = PortfolioBacktestEngine(_config()).run(EqualWeightBuyAndHold(), bars)

        # 末尾 BBB 已停牌，但仓位仍按最后已知价计入净值
        bbb_qty = next(f["qty"] for f in result.fills if f["symbol"] == "BBB")
        assert bbb_qty > 0
        assert result.final_value > 0
        assert not math.isnan(result.final_value)


# ── 3. target_weight() 产出 diff 订单 ─────────────────────────────


class TestTargetWeight:
    def test_diff_orders_from_flat(self) -> None:
        bars = {
            "AAA": _make_bars("AAA", n=10, base_price=100.0),
            "BBB": _make_bars("BBB", n=10, base_price=100.0),
        }
        strategy = TargetWeightOnce({"AAA": 0.5, "BBB": 0.25}, at_index=0)

        PortfolioBacktestEngine(_config()).run(strategy, bars)

        by_symbol = {o.symbol: o for o in strategy.orders}
        assert by_symbol["AAA"].qty == 500   # 100_000 × 0.5 / 100
        assert by_symbol["BBB"].qty == 250
        assert all(o.side.value == "BUY" for o in strategy.orders)

    def test_diff_orders_account_for_existing_position(self) -> None:
        """已有 100 股、目标 150 股 → 买 50 股（而不是买 150 股）。"""
        bars = {"AAA": _make_bars("AAA", n=10, base_price=100.0)}

        class Seeded(PortfolioStrategyBase):
            name = "seeded"

            def __init__(self) -> None:
                super().__init__()
                self.orders: list = []
                self._i = -1

            def on_bars(self, ctx: PortfolioContext) -> None:
                self._i += 1
                if self._i == 0:
                    ctx.buy("AAA", 100)
                elif self._i == 3:
                    assert ctx.position("AAA").qty == 100
                    # 组合净值 ≈ 100_000 → 权重 0.15 ⇒ 目标 150 股
                    self.orders = ctx.target_weight({"AAA": 0.15})

        strategy = Seeded()
        PortfolioBacktestEngine(_config()).run(strategy, bars)

        assert len(strategy.orders) == 1
        assert strategy.orders[0].qty == 50
        assert strategy.orders[0].side.value == "BUY"

    def test_reducing_weight_produces_sell(self) -> None:
        bars = {"AAA": _make_bars("AAA", n=10, base_price=100.0)}

        class Seeded(PortfolioStrategyBase):
            name = "seeded_sell"

            def __init__(self) -> None:
                super().__init__()
                self.orders: list = []
                self._i = -1

            def on_bars(self, ctx: PortfolioContext) -> None:
                self._i += 1
                if self._i == 0:
                    ctx.buy("AAA", 200)
                elif self._i == 3:
                    self.orders = ctx.target_weight({"AAA": 0.05})

        strategy = Seeded()
        PortfolioBacktestEngine(_config()).run(strategy, bars)

        assert len(strategy.orders) == 1
        assert strategy.orders[0].side.value == "SELL"
        assert strategy.orders[0].qty == 150

    def test_zero_diff_emits_no_order(self) -> None:
        bars = {"AAA": _make_bars("AAA", n=10, base_price=100.0)}
        strategy = TargetWeightOnce({"AAA": 0.0}, at_index=2)

        PortfolioBacktestEngine(_config()).run(strategy, bars)

        assert strategy.orders == []

    def test_unknown_symbol_raises(self) -> None:
        bars = {"AAA": _make_bars("AAA", n=6, base_price=100.0)}

        class Bad(PortfolioStrategyBase):
            name = "bad"
            error: Exception | None = None

            def on_bars(self, ctx: PortfolioContext) -> None:
                if Bad.error is None:
                    try:
                        ctx.target_weight({"ZZZ": 0.5})
                    except ValueError as exc:
                        Bad.error = exc

        Bad.error = None
        PortfolioBacktestEngine(_config()).run(Bad(), bars)
        assert isinstance(Bad.error, ValueError)

    def test_negative_weight_rejected_without_short(self) -> None:
        bars = {"AAA": _make_bars("AAA", n=6, base_price=100.0)}

        class Bad(PortfolioStrategyBase):
            name = "bad_short"
            error: Exception | None = None

            def on_bars(self, ctx: PortfolioContext) -> None:
                if Bad.error is None:
                    try:
                        ctx.target_weight({"AAA": -0.5})
                    except ValueError as exc:
                        Bad.error = exc

        Bad.error = None
        PortfolioBacktestEngine(_config()).run(Bad(), bars)
        assert isinstance(Bad.error, ValueError)


# ── 4. max_open_positions ─────────────────────────────────────────


class TestMaxOpenPositions:
    def test_limit_caps_number_of_symbols_held(self) -> None:
        bars = {
            f"S{i}": _make_bars(f"S{i}", n=15, base_price=10.0 + i)
            for i in range(6)
        }
        engine = PortfolioBacktestEngine(_config(max_open_positions=2))

        result = engine.run(BuyEverythingEveryBar(), bars)

        traded = {f["symbol"] for f in result.fills}
        assert len(traded) <= 2

    def test_no_limit_by_default(self) -> None:
        bars = {
            f"S{i}": _make_bars(f"S{i}", n=15, base_price=10.0 + i)
            for i in range(6)
        }
        result = PortfolioBacktestEngine(_config()).run(BuyEverythingEveryBar(), bars)

        traded = {f["symbol"] for f in result.fills}
        assert len(traded) == 6

    def test_rotation_at_the_cap_is_not_rejected(self) -> None:
        """满仓时用 target_weight 换股：清仓单已挂出，新标的不应被误判为超限。"""
        bars = {
            "AAA": _make_bars("AAA", n=14, base_price=100.0),
            "BBB": _make_bars("BBB", n=14, base_price=100.0),
        }

        class Rotate(PortfolioStrategyBase):
            name = "rotate"

            def on_start(self, ctx: PortfolioContext) -> None:
                self._i = -1

            def on_bars(self, ctx: PortfolioContext) -> None:
                self._i += 1
                if self._i == 0:
                    ctx.target_weight({"AAA": 0.5, "BBB": 0.0})
                elif self._i == 5:
                    ctx.target_weight({"AAA": 0.0, "BBB": 0.5})

        result = PortfolioBacktestEngine(_config(max_open_positions=1)).run(
            Rotate(), bars
        )

        traded = [f["symbol"] for f in result.fills]
        assert traded.count("BBB") == 1, f"换股未成交，实际成交序列 {traded}"
        held = {p["symbol"] for p in result.report["positions"]}
        assert held == {"BBB"}


# ── 5. 现金不足按序拒单，不透支 ───────────────────────────────────


class TestCashDiscipline:
    def test_cash_never_goes_negative(self) -> None:
        bars = {
            f"S{i}": _make_bars(f"S{i}", n=30, base_price=500.0)
            for i in range(8)
        }
        engine = PortfolioBacktestEngine(_config(initial_cash=20_000.0))

        result = engine.run(BuyEverythingEveryBar(), bars)

        assert result.report["final_cash"] >= 0.0
        assert result.final_value > 0.0

    def test_equity_curve_has_no_nan(self) -> None:
        bars = {
            "AAA": _make_bars("AAA", n=25, base_price=120.0, trend=0.001),
            "BBB": _make_bars("BBB", n=25, base_price=80.0, skip={5, 6}),
        }
        result = PortfolioBacktestEngine(_config()).run(BuyEverythingEveryBar(), bars)

        assert not result.equity_curve.isna().any()


# ── 6. warmup / 生命周期 ──────────────────────────────────────────


class TestLifecycle:
    def test_warmup_bars_suppress_strategy_calls(self) -> None:
        bars = {"AAA": _make_bars("AAA", n=10)}
        strategy = RecordingStrategy()

        PortfolioBacktestEngine(_config(warmup_bars=4)).run(strategy, bars)

        assert len(strategy.seen) == 6

    def test_requires_at_least_two_timepoints(self) -> None:
        bars = {"AAA": _make_bars("AAA", n=1)}

        with pytest.raises(ValueError, match="At least 2 bars"):
            PortfolioBacktestEngine(_config()).run(NoopPortfolioStrategy(), bars)

    def test_rejects_empty_input(self) -> None:
        with pytest.raises(ValueError, match="不能为空"):
            PortfolioBacktestEngine(_config()).run(NoopPortfolioStrategy(), {})

    def test_daily_results_reconcile_with_equity_change(self) -> None:
        bars = {
            "AAA": _make_bars("AAA", n=20, base_price=100.0, trend=0.003),
            "BBB": _make_bars("BBB", n=20, base_price=60.0, trend=-0.002),
        }
        result = PortfolioBacktestEngine(_config()).run(EqualWeightBuyAndHold(), bars)

        assert result.daily_results
        total_net = sum(d.net_pnl for d in result.daily_results)
        assert total_net == pytest.approx(
            result.final_value - result.initial_cash, rel=1e-6, abs=1e-6
        )

    def test_daily_results_reconcile_with_dividends(self) -> None:
        """除权分红是非成交现金流：不记进 net_pnl，日结就会与净值对不上。"""
        from app.data.adjustments import CorporateAction

        bars = {"AAA": _make_bars("AAA", n=20, base_price=100.0)}
        action = CorporateAction(
            symbol="AAA",
            ex_date=(START + timedelta(days=10)).date(),
            kind="dividend",
            amount=2.0,
        )
        cfg = _config(adjust_prices=True, corporate_actions=[action])

        result = PortfolioBacktestEngine(cfg).run(EqualWeightBuyAndHold(), bars)

        assert result.report["adjustments"]["dividend_cash"] > 0
        assert sum(d.other_cash_flow for d in result.daily_results) == pytest.approx(
            result.report["adjustments"]["dividend_cash"], abs=1e-3
        )
        total_net = sum(d.net_pnl for d in result.daily_results)
        assert total_net == pytest.approx(
            result.final_value - result.initial_cash, rel=1e-6, abs=1e-6
        )

    def test_daily_results_reconcile_with_short_borrow_fee(self) -> None:
        """融券费同理：按自然日计提，也必须落进 other_cash_flow。"""
        bars = {"AAA": _make_bars("AAA", n=20, base_price=100.0)}

        class ShortOnce(PortfolioStrategyBase):
            name = "short_once"

            def on_start(self, ctx: PortfolioContext) -> None:
                self._done = False

            def on_bars(self, ctx: PortfolioContext) -> None:
                if not self._done:
                    ctx.short("AAA", 100)
                    self._done = True

        cfg = _config(allow_short=True, short_borrow_rate=0.10)
        result = PortfolioBacktestEngine(cfg).run(ShortOnce(), bars)

        assert sum(d.other_cash_flow for d in result.daily_results) < 0
        total_net = sum(d.net_pnl for d in result.daily_results)
        assert total_net == pytest.approx(
            result.final_value - result.initial_cash, rel=1e-6, abs=1e-6
        )


# ── 7. 单标的等价性 ───────────────────────────────────────────────


class _SingleMa(StrategyBase):
    """单标的双均线：用于比对单标的引擎与组合引擎的逐笔一致性。"""

    name = "single_ma"

    def on_bar(self, ctx: StrategyContext) -> None:
        closes = ctx.close_series()
        if len(closes) < 10:
            return
        fast = closes.iloc[-3:].mean()
        slow = closes.iloc[-10:].mean()
        pos = ctx.position()
        held = pos.qty if pos else 0
        if fast > slow and held == 0:
            ctx.buy(int(ctx.cash / ctx.bar.close / 2))
        elif fast < slow and held > 0:
            ctx.sell_all()


class TestSingleSymbolEquivalence:
    def test_both_entry_points_agree_fill_by_fill(self) -> None:
        """`BacktestEngine.run` 与 `PortfolioBacktestEngine.run_single` 逐笔一致。

        ⚠️ 这**不是**对旧单标的引擎的对账 —— 旧实现已在 K-c 迁移中删除，
        `BacktestEngine.run` 如今直接委托给 `run_single`。本用例真正守住的是
        中间那层 `BacktestConfig → PortfolioBacktestConfig` 的字段搬运：
        漏搬或错搬任何一个配置项，两个入口就会给出不同结果。

        迁移本身（新引擎 == 旧引擎逐笔）由 `tests/regression` 的 146 个用例
        永久看守 —— 那份 golden 快照是在旧实现还在时生成的。
        """
        bars = _make_bars("AAA", n=90, base_price=100.0, trend=0.0)
        # 制造均线交叉
        rng = np.random.default_rng(7)
        noisy = []
        price = 100.0
        for i, b in enumerate(bars):
            price *= float(math.exp(rng.normal(0.0, 0.02)))
            noisy.append(
                Bar(
                    time=b.time, symbol=b.symbol, market=b.market, frequency=b.frequency,
                    open=price * 0.999, high=price * 1.01, low=price * 0.99,
                    close=price, volume=1_000_000 + i,
                )
            )

        legacy = BacktestEngine(
            BacktestConfig(initial_cash=100_000.0, market=Market.US)
        ).run(_SingleMa(), noisy)
        portfolio = PortfolioBacktestEngine(
            PortfolioBacktestConfig(initial_cash=100_000.0, market=Market.US)
        ).run_single(_SingleMa(), noisy)

        # order_id 是随机 uuid，逐笔比对时剔除
        def _comparable(fills: list[dict]) -> list[dict]:
            return [{k: v for k, v in f.items() if k != "order_id"} for f in fills]

        assert len(portfolio.fills) == len(legacy.fills)
        assert _comparable(portfolio.fills) == _comparable(legacy.fills)
        assert portfolio.final_value == pytest.approx(legacy.final_value, rel=1e-12)
        assert portfolio.metrics == legacy.metrics


# ── 8. 性能 ───────────────────────────────────────────────────────


class TestPerformance:
    def test_50_symbols_3_years_under_30s(self, capsys) -> None:
        # Arrange: 50 标的 × 750 时点
        rng = np.random.default_rng(20260811)
        bars: dict[str, list[Bar]] = {}
        for s in range(50):
            symbol = f"SYM{s:02d}"
            price = 50.0 + s
            series = []
            for i in range(750):
                price *= float(math.exp(rng.normal(0.0002, 0.015)))
                series.append(
                    Bar(
                        time=START + timedelta(days=i),
                        symbol=symbol,
                        market=Market.US,
                        frequency=Frequency.DAY_1,
                        open=price * 0.999,
                        high=price * 1.012,
                        low=price * 0.988,
                        close=price,
                        volume=2_000_000,
                    )
                )
            bars[symbol] = series

        engine = PortfolioBacktestEngine(_config(initial_cash=1_000_000.0))

        # Act
        started = time.perf_counter()
        result = engine.run(_MomentumRebalance(), bars)
        elapsed = time.perf_counter() - started

        # Assert
        with capsys.disabled():
            print(
                f"\n[perf] 50 标的 × 750 时点：{elapsed:.2f}s，"
                f"成交 {len(result.fills)} 笔，末值 {result.final_value:,.0f}"
            )
        assert elapsed < 30.0, f"组合引擎耗时 {elapsed:.2f}s，超过 30s 预算"


class _MomentumRebalance(PortfolioStrategyBase):
    """
    动量选股：**每个时点**为全部标的算 20 日动量（压 history 访问的最坏情况），
    每 20 个时点按分数取前 5 名等权调仓（压 target_weight 的 diff 与撮合）。
    """

    name = "momentum_rebalance"
    LOOKBACK = 20
    TOP_N = 5
    REBALANCE_EVERY = 20
    GROSS = 0.95

    def on_start(self, ctx: PortfolioContext) -> None:
        self._i = -1

    def on_bars(self, ctx: PortfolioContext) -> None:
        self._i += 1
        scores = self._score(ctx)
        if not scores or self._i % self.REBALANCE_EVERY or self._i < self.LOOKBACK:
            return
        top = sorted(scores, key=lambda s: scores[s], reverse=True)[: self.TOP_N]
        weight = self.GROSS / len(top)
        ctx.target_weight({s: (weight if s in top else 0.0) for s in ctx.symbols})

    def _score(self, ctx: PortfolioContext) -> dict[str, float]:
        scores: dict[str, float] = {}
        for symbol in ctx.bars:
            closes = ctx.close_series(symbol)
            if len(closes) <= self.LOOKBACK:
                continue
            scores[symbol] = float(closes.iloc[-1] / closes.iloc[-self.LOOKBACK] - 1.0)
        return scores
