"""预设策略「指标预算」改造前后的逐笔对拍（V3 Wave E-a / J2 重定向）

本文件里的 `_Legacy*` 类是改造**前**的 `on_bar` 原样搬运，它们就是这次对拍的
参照物 —— 每根 bar 对全量历史重算指标的写法。测试断言两条路径的成交
**逐笔一致**（时间 / 方向 / 数量 / 价格），不是「笔数一致」，也不是「指标接近」。

> 一个快 10 倍但结果不同的回测没有价值。所以性能数字与一致性断言写在
> **同一个测试里**（`test_precompute_is_faster_and_identical`）——
> 分开写就给了「快了但不对」一个藏身处。

`tests/regression/` 那 146 条基线钉的是默认参数 × 3 市场 × 3 情景；
这里补的是**非默认参数**那一片，两者互不替代。
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from app.data.models import Bar, Frequency, Market
from app.engine.backtest.engine import BacktestConfig, BacktestEngine, BacktestResult
from app.strategy.base import StrategyBase
from app.strategy.context import StrategyContext
from app.strategy.indicators import (
    bollinger_bands,
    crossover,
    crossunder,
    ema,
    macd,
    sma,
)
from app.strategy.presets import BollingerStrategy, DoubleMaStrategy, MacdStrategy

BASE_TIME = datetime(2024, 1, 2, tzinfo=UTC)
INITIAL_CASH = 100_000.0

#: 对拍用的市场（各自走不同的手续费 / 滑点 / T+1 分支）
MARKETS: tuple[tuple[Market, str], ...] = (
    (Market.US, "AAPL"),
    (Market.A, "600519"),
)

#: 性能用例的最低加速比。实测单标的 ~7×、组合 ~10×（见 docs/profiling-backtest-j2.md）；
#: 阈值压到 2.5× 是给共享 CI 机器留的余量 —— 这条断言要抓的是「优化失效了」，
#: 不是「今天这台机器慢了 20%」。真实数字由用例打印出来。
_MIN_SPEEDUP = 2.5


# ── 数据 ──────────────────────────────────────────────────────


def make_bars(symbol: str, market: Market, n: int = 400, seed: int = 3) -> list[Bar]:
    """确定性的震荡行情。种子固定，不碰任何全局随机状态。"""
    rng = np.random.Generator(np.random.PCG64(seed))
    log_returns = 0.0005 + 0.016 * rng.standard_normal(n)
    closes = 100.0 * np.exp(np.cumsum(log_returns))
    opens = np.empty(n)
    opens[0] = 100.0
    opens[1:] = closes[:-1]
    spread = np.abs(rng.standard_normal(n)) * 0.012
    highs = np.maximum(opens, closes) * (1.0 + spread)
    lows = np.minimum(opens, closes) * (1.0 - spread)

    return [
        Bar(
            time=BASE_TIME + timedelta(days=i),
            symbol=symbol,
            market=market,
            frequency=Frequency.DAY_1,
            open=float(opens[i]),
            high=float(highs[i]),
            low=float(lows[i]),
            close=float(closes[i]),
            volume=1_000_000,
        )
        for i in range(n)
    ]


def run(strategy: StrategyBase, bars: list[Bar], market: Market) -> BacktestResult:
    engine = BacktestEngine(BacktestConfig(initial_cash=INITIAL_CASH, market=market))
    return engine.run(strategy, bars, strategy_id="parity")


def fingerprints(result: BacktestResult) -> list[tuple]:
    """逐笔指纹：时间 / 方向 / 数量 / 价格 —— 契约 §3.2 要求的四元组。"""
    return [
        (f["filled_at"], f["side"], f["qty"], round(float(f["price"]), 6))
        for f in result.fills
    ]


# ── 改造前的实现（原样搬运，本文件是它们唯一的去处）─────────


class _LegacyDoubleMa(StrategyBase):
    name = "legacy_double_ma"

    def on_bar(self, ctx: StrategyContext) -> None:
        fast = self.param("fast_period", 10)
        slow = self.param("slow_period", 30)
        ma_type = self.param("ma_type", "sma")

        df = ctx.history
        if len(df) < slow + 1:
            return

        if ma_type == "ema":
            fast_ma = ema(df, fast)
            slow_ma = ema(df, slow)
        else:
            fast_ma = sma(df, fast)
            slow_ma = sma(df, slow)

        if crossover(fast_ma, slow_ma).iloc[-1] and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / ctx.bar.close)
            if qty > 0:
                ctx.buy(qty)

        elif crossunder(fast_ma, slow_ma).iloc[-1] and ctx.qty > 0:
            ctx.sell_all()


class _LegacyMacd(StrategyBase):
    name = "legacy_macd"

    def on_bar(self, ctx: StrategyContext) -> None:
        fast = self.param("fast", 12)
        slow = self.param("slow", 26)
        signal = self.param("signal", 9)

        df = ctx.history
        if len(df) < slow + signal + 1:
            return

        macd_line, signal_line, _ = macd(df, fast, slow, signal)

        if crossover(macd_line, signal_line).iloc[-1] and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / ctx.bar.close)
            if qty > 0:
                ctx.buy(qty)

        elif crossunder(macd_line, signal_line).iloc[-1] and ctx.qty > 0:
            ctx.sell_all()


class _LegacyBollinger(StrategyBase):
    name = "legacy_bollinger"

    def on_bar(self, ctx: StrategyContext) -> None:
        period = self.param("period", 20)
        std_dev = self.param("std_dev", 2.0)

        df = ctx.history
        if len(df) < period + 1:
            return

        upper, mid, lower = bollinger_bands(df, period, std_dev)
        close = ctx.bar.close

        if close <= lower.iloc[-1] and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / close)
            if qty > 0:
                ctx.buy(qty)

        elif ctx.qty > 0 and (close >= mid.iloc[-1] or close >= upper.iloc[-1]):
            ctx.sell_all()


# ── 参数矩阵：每个预设 ≥3 组参数 × 2 个市场 ──────────────────

_PARAM_SETS: tuple[tuple[str, type[StrategyBase], type[StrategyBase], dict], ...] = (
    ("double_ma/default", DoubleMaStrategy, _LegacyDoubleMa, {}),
    ("double_ma/fast", DoubleMaStrategy, _LegacyDoubleMa, {"fast_period": 5, "slow_period": 15}),
    ("double_ma/slow", DoubleMaStrategy, _LegacyDoubleMa, {"fast_period": 20, "slow_period": 60}),
    ("double_ma/ema", DoubleMaStrategy, _LegacyDoubleMa, {"ma_type": "ema", "slow_period": 25}),
    ("macd/default", MacdStrategy, _LegacyMacd, {}),
    ("macd/short", MacdStrategy, _LegacyMacd, {"fast": 6, "slow": 13, "signal": 4}),
    ("macd/long", MacdStrategy, _LegacyMacd, {"fast": 19, "slow": 39, "signal": 12}),
    ("bollinger/default", BollingerStrategy, _LegacyBollinger, {}),
    ("bollinger/tight", BollingerStrategy, _LegacyBollinger, {"period": 10, "std_dev": 1.0}),
    ("bollinger/wide", BollingerStrategy, _LegacyBollinger, {"period": 40, "std_dev": 2.5}),
)

_CASES = [
    (label, new_cls, legacy_cls, params, market, symbol)
    for label, new_cls, legacy_cls, params in _PARAM_SETS
    for market, symbol in MARKETS
]


@pytest.mark.parametrize(
    ("label", "new_cls", "legacy_cls", "params", "market", "symbol"),
    _CASES,
    ids=[f"{label}|{market.value}" for label, _, _, _, market, _ in _CASES],
)
def test_preset_fills_match_the_pre_refactor_implementation(
    label: str,
    new_cls: type[StrategyBase],
    legacy_cls: type[StrategyBase],
    params: dict,
    market: Market,
    symbol: str,
) -> None:
    bars = make_bars(symbol, market)
    legacy = run(legacy_cls(params), bars, market)
    updated = run(new_cls(params), bars, market)

    legacy_fills = fingerprints(legacy)
    updated_fills = fingerprints(updated)

    assert legacy_fills, f"{label} 在这份数据上没有成交，对拍什么也证明不了"
    assert len(updated_fills) == len(legacy_fills), (
        f"{label}|{market.value} 成交笔数漂移：{len(legacy_fills)} → {len(updated_fills)}"
    )
    for i, (expected, actual) in enumerate(zip(legacy_fills, updated_fills, strict=True)):
        assert actual == expected, f"{label}|{market.value} 第 {i} 笔成交漂移：{expected} → {actual}"

    assert updated.final_value == pytest.approx(legacy.final_value, rel=1e-12)


# ── 性能：数字与一致性断言必须在同一个测试里 ─────────────────


def _elapsed(strategy_factory, bars, market) -> tuple[float, BacktestResult]:
    start = time.perf_counter()
    result = run(strategy_factory(), bars, market)
    return time.perf_counter() - start, result


def test_precompute_is_faster_and_identical_single_symbol() -> None:
    """单标的：先断言逐笔一致，再比时间。顺序反过来比的就不是同一件事。"""
    market, symbol = Market.US, "AAPL"
    bars = make_bars(symbol, market, n=2000, seed=17)

    legacy_time, legacy = _elapsed(_LegacyDoubleMa, bars, market)
    new_time, updated = _elapsed(DoubleMaStrategy, bars, market)

    assert fingerprints(updated) == fingerprints(legacy)
    assert len(legacy.fills) > 20, "成交太少，性能对比没有代表性"

    speedup = legacy_time / new_time
    print(
        f"\n[E-a 单标的] bars={len(bars)} 成交={len(legacy.fills)} 笔（两条路径逐笔一致）\n"
        f"            每 bar 重算 {legacy_time * 1000:7.1f} ms\n"
        f"            指标预算    {new_time * 1000:7.1f} ms   加速 {speedup:.2f}×"
    )
    assert speedup >= _MIN_SPEEDUP, f"加速比只有 {speedup:.2f}×，预算路径可能已失效"


def test_precompute_is_faster_and_identical_portfolio() -> None:
    """组合：同一份逻辑跑 20 个标的，同样先断言逐笔一致再比时间。"""
    from app.engine.backtest.portfolio_engine import (
        PortfolioBacktestConfig,
        PortfolioBacktestEngine,
    )
    from app.strategy.base import IndicatorSpec, PortfolioStrategyBase
    from app.strategy.context import PortfolioContext

    symbols = [f"SYM{i:02d}" for i in range(20)]
    bars_by_symbol = {
        sym: make_bars(sym, Market.US, n=500, seed=100 + i) for i, sym in enumerate(symbols)
    }

    class _LegacyBasket(PortfolioStrategyBase):
        name = "legacy_basket"

        def on_bars(self, ctx: PortfolioContext) -> None:
            for symbol in ctx.bars:
                df = ctx.history(symbol)
                if len(df) < 31:
                    continue
                fast_ma, slow_ma = sma(df, 10), sma(df, 30)
                if crossover(fast_ma, slow_ma).iloc[-1] and ctx.qty(symbol) == 0:
                    ctx.buy_value(symbol, 20_000.0)
                elif crossunder(fast_ma, slow_ma).iloc[-1] and ctx.qty(symbol) > 0:
                    ctx.close_all(symbol)

    class _PrecomputedBasket(PortfolioStrategyBase):
        name = "precomputed_basket"

        def declare_indicators(self, spec: IndicatorSpec) -> None:
            spec.add("fast", sma, 10)
            spec.add("slow", sma, 30)

        def on_bars(self, ctx: PortfolioContext) -> None:
            for symbol in ctx.bars:
                ind = ctx.ind(symbol)
                if ind.bars_seen < 31:
                    continue
                if ind.crossed_up("fast", "slow") and ctx.qty(symbol) == 0:
                    ctx.buy_value(symbol, 20_000.0)
                elif ind.crossed_down("fast", "slow") and ctx.qty(symbol) > 0:
                    ctx.close_all(symbol)

    def run_portfolio(strategy_cls) -> tuple[float, list[tuple]]:
        engine = PortfolioBacktestEngine(
            PortfolioBacktestConfig(initial_cash=1_000_000.0, market=Market.US)
        )
        start = time.perf_counter()
        result = engine.run(strategy_cls(), bars_by_symbol)
        elapsed = time.perf_counter() - start
        prints = [
            (f["filled_at"], f["symbol"], f["side"], f["qty"], round(float(f["price"]), 6))
            for f in result.fills
        ]
        return elapsed, prints

    legacy_time, legacy_fills = run_portfolio(_LegacyBasket)
    new_time, new_fills = run_portfolio(_PrecomputedBasket)

    assert new_fills == legacy_fills
    assert len(legacy_fills) > 50, "成交太少，性能对比没有代表性"

    speedup = legacy_time / new_time
    print(
        f"\n[E-a 组合] symbols={len(symbols)} bars=500 成交={len(legacy_fills)} 笔"
        f"（两条路径逐笔一致）\n"
        f"          每 bar 重算 {legacy_time * 1000:7.1f} ms\n"
        f"          指标预算    {new_time * 1000:7.1f} ms   加速 {speedup:.2f}×"
    )
    assert speedup >= _MIN_SPEEDUP, f"加速比只有 {speedup:.2f}×，预算路径可能已失效"
