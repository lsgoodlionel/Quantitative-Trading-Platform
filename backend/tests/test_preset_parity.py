"""预设策略「指标预算」改造前后的逐笔对拍（V3 Wave E-a / J2 重定向 · G-a 扩充）

本文件里的 `_Legacy*` 类是改造**前**的 `on_bar` 原样搬运，它们就是这次对拍的
参照物 —— 每根 bar 对全量历史重算指标的写法。测试断言两条路径的成交
**逐笔一致**（时间 / 方向 / 数量 / 价格），不是「笔数一致」，也不是「指标接近」。

> 一个快 10 倍但结果不同的回测没有价值。所以性能数字与一致性断言写在
> **同一个测试里**（`test_precompute_is_faster_and_identical`）——
> 分开写就给了「快了但不对」一个藏身处。

`tests/regression/` 那 146 条基线钉的是默认参数 × 3 市场 × 3 情景；
这里补的是**非默认参数**那一片，两者互不替代。

> **G-a**：把 E-a 的 5 个扩到 15 个。剩下的 `grid_trading` 是纯价格网格，
> 一条指标都不用，没有可预算的东西 —— 未改造，也不在本文件里。
"""

from __future__ import annotations

import math
import time
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from app.data.models import Bar, Frequency, Market
from app.engine.backtest.engine import BacktestConfig, BacktestEngine, BacktestResult
from app.strategy.base import StrategyBase
from app.strategy.context import StrategyContext
from app.strategy.indicators import (
    adx,
    atr,
    bollinger_bands,
    crossover,
    crossunder,
    donchian_channels,
    ema,
    highest,
    keltner_channels,
    lowest,
    macd,
    rsi,
    sma,
    stochastic,
    supertrend,
    vwap,
)
from app.strategy.presets import (
    AdxTrendStrategy,
    AtrBreakoutStrategy,
    BollingerStrategy,
    DonchianBreakoutStrategy,
    DoubleMaStrategy,
    KeltnerBreakoutStrategy,
    MacdStrategy,
    MomentumStrategy,
    MultiFactorStrategy,
    PairsTradingStrategy,
    RsiMeanReversionStrategy,
    StochasticStrategy,
    SupertrendStrategy,
    TripleMaStrategy,
    VwapReversionStrategy,
)

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


class _LegacyRsiMeanReversion(StrategyBase):
    name = "legacy_rsi_mean_reversion"

    def on_bar(self, ctx: StrategyContext) -> None:
        period = self.param("period", 14)
        oversold = self.param("oversold", 30)
        overbought = self.param("overbought", 70)

        df = ctx.history
        if len(df) < period + 2:
            return

        rsi_val = rsi(df, period).iloc[-1]
        if rsi_val is None or rsi_val != rsi_val:  # NaN check
            return

        if rsi_val <= oversold and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / ctx.bar.close)
            if qty > 0:
                ctx.buy(qty)

        elif rsi_val >= overbought and ctx.qty > 0:
            ctx.sell_all()


class _LegacyMomentum(StrategyBase):
    name = "legacy_momentum"

    def on_bar(self, ctx: StrategyContext) -> None:
        lookback = self.param("lookback", 20)
        threshold = self.param("threshold", 0.03)

        df = ctx.history
        if len(df) < lookback + 1:
            return

        past_close = df["close"].iloc[-(lookback + 1)]
        current_close = ctx.bar.close
        momentum = (current_close - past_close) / past_close

        if momentum > threshold and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / current_close)
            if qty > 0:
                ctx.buy(qty)

        elif momentum < 0 and ctx.qty > 0:
            ctx.sell_all()


# ── 改造前的实现（G-a 的 10 个，同样原样搬运）───────────────


class _LegacyAdxTrend(StrategyBase):
    name = "legacy_adx_trend"

    def on_bar(self, ctx: StrategyContext) -> None:
        fast_period   = self.param("fast_period",   10)
        slow_period   = self.param("slow_period",   30)
        adx_period    = self.param("adx_period",    14)
        adx_threshold = self.param("adx_threshold", 25)

        df = ctx.history
        min_bars = max(slow_period, adx_period * 2) + 2
        if len(df) < min_bars:
            return

        fast_ma = sma(df, fast_period)
        slow_ma = sma(df, slow_period)
        adx_val = adx(df, adx_period).iloc[-1]

        if adx_val != adx_val:   # NaN guard
            return

        trend_strong = adx_val > adx_threshold

        if trend_strong and crossover(fast_ma, slow_ma).iloc[-1] and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / ctx.bar.close)
            if qty > 0:
                ctx.buy(qty)

        elif ctx.qty > 0 and (crossunder(fast_ma, slow_ma).iloc[-1] or not trend_strong):
            ctx.sell_all()


class _LegacyAtrBreakout(StrategyBase):
    name = "legacy_atr_breakout"

    def on_bar(self, ctx: StrategyContext) -> None:
        channel_period = self.param("channel_period", 20)
        atr_period     = self.param("atr_period",     14)
        multiplier     = self.param("multiplier",     0.5)

        df = ctx.history
        min_bars = max(channel_period, atr_period) + 2
        if len(df) < min_bars:
            return

        close  = ctx.bar.close
        atr_v  = atr(df, atr_period).iloc[-1]
        high_n = highest(df, channel_period).iloc[-2]   # 前 N 日最高（不含当前）
        low_n  = lowest(df,  channel_period).iloc[-2]

        breakout_up   = high_n + multiplier * atr_v
        breakout_down = low_n  - multiplier * atr_v

        if close > breakout_up and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / close)
            if qty > 0:
                ctx.buy(qty)

        elif ctx.qty > 0 and close < breakout_down:
            ctx.sell_all()


class _LegacyDonchianBreakout(StrategyBase):
    name = "legacy_donchian_breakout"

    def on_bar(self, ctx: StrategyContext) -> None:
        period = self.param("period", 20)
        exit_period = self.param("exit_period", 10)

        df = ctx.history
        if len(df) < period + 2:
            return

        upper, _, _      = donchian_channels(df, period)
        _, _, exit_lower = donchian_channels(df, exit_period)

        close = ctx.bar.close

        prev_upper = upper.iloc[-2]
        prev_exit_lower = exit_lower.iloc[-2]

        if close > prev_upper and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / close)
            if qty > 0:
                ctx.buy(qty)

        elif ctx.qty > 0 and close < prev_exit_lower:
            ctx.sell_all()


class _LegacyKeltnerBreakout(StrategyBase):
    name = "legacy_keltner_breakout"

    def on_bar(self, ctx: StrategyContext) -> None:
        ema_period = self.param("ema_period", 20)
        atr_period = self.param("atr_period", 10)
        multiplier = self.param("multiplier", 2.0)

        df = ctx.history
        if len(df) < ema_period + atr_period + 2:
            return

        upper, mid, _ = keltner_channels(df, ema_period, atr_period, multiplier)
        close      = ctx.bar.close
        prev_close = df["close"].iloc[-2]

        if prev_close < upper.iloc[-2] and close >= upper.iloc[-1] and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / close)
            if qty > 0:
                ctx.buy(qty)

        elif ctx.qty > 0 and close < mid.iloc[-1]:
            ctx.sell_all()


class _LegacyMultiFactor(StrategyBase):
    name = "legacy_multi_factor"

    def on_bar(self, ctx: StrategyContext) -> None:
        momentum_lookback = self.param("momentum_lookback", 20)
        rsi_period = self.param("rsi_period", 14)
        rsi_low = self.param("rsi_low", 35)
        rsi_high = self.param("rsi_high", 65)
        macd_fast = self.param("macd_fast", 12)
        macd_slow = self.param("macd_slow", 26)
        macd_signal = self.param("macd_signal", 9)
        threshold = self.param("threshold", 1.0)
        w_momentum = self.param("w_momentum", 1.0)
        w_rsi = self.param("w_rsi", 1.0)
        w_macd = self.param("w_macd", 1.0)

        min_bars = max(momentum_lookback, rsi_period + 1, macd_slow + macd_signal + 1)
        df = ctx.history
        if len(df) < min_bars + 1:
            return

        close = ctx.bar.close

        past_close = df["close"].iloc[-(momentum_lookback + 1)]
        momentum_ret = (close - past_close) / past_close
        signal_momentum = 1.0 if momentum_ret > 0.02 else (-1.0 if momentum_ret < -0.02 else 0.0)

        rsi_val = rsi(df, rsi_period).iloc[-1]
        if rsi_val != rsi_val:  # NaN
            signal_rsi = 0.0
        elif rsi_val < rsi_low:
            signal_rsi = 1.0
        elif rsi_val > rsi_high:
            signal_rsi = -1.0
        else:
            signal_rsi = 0.0

        macd_line, signal_line, _ = macd(df, macd_fast, macd_slow, macd_signal)
        diff = macd_line.iloc[-1] - signal_line.iloc[-1]
        prev_diff = macd_line.iloc[-2] - signal_line.iloc[-2]
        if diff > 0 and prev_diff <= 0:
            signal_macd = 1.0
        elif diff < 0 and prev_diff >= 0:
            signal_macd = -1.0
        else:
            signal_macd = 0.0

        score = w_momentum * signal_momentum + w_rsi * signal_rsi + w_macd * signal_macd

        if score >= threshold and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / close)
            if qty > 0:
                ctx.buy(qty)

        elif score <= -threshold and ctx.qty > 0:
            ctx.sell_all()


class _LegacyPairsTrading(StrategyBase):
    """
    改造前的 `on_bar`，**两种模式都在**。

    模式 B（注入配对价格）这次没走预算 —— 但它被重构成了独立方法，
    所以同样要对拍：证明「没改造」是真的没改行为，而不是「大概没改」。
    """

    name = "legacy_pairs_trading"

    def on_start(self, ctx: StrategyContext) -> None:
        self._price_b_history: list[float] = []

    def on_bar(self, ctx: StrategyContext) -> None:
        entry_z    = self.param("entry_z", 2.0)
        exit_z     = self.param("exit_z", 0.5)
        lookback   = self.param("lookback", 60)
        hedge_ratio = self.param("hedge_ratio", 1.0)

        df = ctx.history
        if len(df) < lookback:
            return

        price_b = self.param("price_b_today", None)
        if price_b is not None:
            self._price_b_history.append(float(price_b))
            if len(self._price_b_history) < lookback:
                return

            log_a = df["close"].iloc[-lookback:].apply(math.log)
            log_b = pd.Series(self._price_b_history[-lookback:]).apply(math.log)
            spread = log_a.values - hedge_ratio * log_b.values
            spread_s = pd.Series(spread)

            mean = spread_s.mean()
            std  = spread_s.std()
            if std < 1e-10:
                return

            z = (spread_s.iloc[-1] - mean) / std

            if z < -entry_z and ctx.qty == 0:
                qty = int(ctx.cash * 0.95 / ctx.bar.close)
                if qty > 0:
                    ctx.buy(qty)
            elif z > entry_z and ctx.qty > 0:
                ctx.sell_all()
            elif abs(z) < exit_z and ctx.qty > 0:
                ctx.sell_all()
            return

        close_series = df["close"].iloc[-lookback:]
        mean = close_series.mean()
        std  = close_series.std()
        if std < 1e-10:
            return

        z = (ctx.bar.close - mean) / std

        if z < -entry_z and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / ctx.bar.close)
            if qty > 0:
                ctx.buy(qty)

        elif z > entry_z and ctx.qty > 0:
            ctx.sell_all()

        elif abs(z) < exit_z and ctx.qty > 0:
            ctx.sell_all()


class _LegacyStochastic(StrategyBase):
    name = "legacy_stochastic"

    def on_bar(self, ctx: StrategyContext) -> None:
        k_period   = self.param("k_period",   14)
        d_period   = self.param("d_period",   3)
        oversold   = self.param("oversold",   20)
        overbought = self.param("overbought", 80)

        df = ctx.history
        if len(df) < k_period + d_period + 2:
            return

        k, d = stochastic(df, k_period, d_period)

        if crossover(k, d).iloc[-1] and k.iloc[-1] < oversold and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / ctx.bar.close)
            if qty > 0:
                ctx.buy(qty)

        elif crossunder(k, d).iloc[-1] and k.iloc[-1] > overbought and ctx.qty > 0:
            ctx.sell_all()


class _LegacySupertrend(StrategyBase):
    name = "legacy_supertrend"

    def on_bar(self, ctx: StrategyContext) -> None:
        period = self.param("period", 10)
        multiplier = self.param("multiplier", 3.0)

        df = ctx.history
        if len(df) < period * 2 + 2:
            return

        _, direction = supertrend(df, period, multiplier)
        if len(direction) < 2:
            return

        cur_dir  = direction.iloc[-1]
        prev_dir = direction.iloc[-2]

        if prev_dir == -1 and cur_dir == 1 and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / ctx.bar.close)
            if qty > 0:
                ctx.buy(qty)

        elif prev_dir == 1 and cur_dir == -1 and ctx.qty > 0:
            ctx.sell_all()


class _LegacyTripleMa(StrategyBase):
    name = "legacy_triple_ma"

    def on_bar(self, ctx: StrategyContext) -> None:
        fast_period = self.param("fast_period", 5)
        mid_period  = self.param("mid_period",  13)
        slow_period = self.param("slow_period", 34)

        df = ctx.history
        if len(df) < slow_period + 2:
            return

        fast_ma = ema(df, fast_period)
        mid_ma  = ema(df, mid_period)
        slow_ma = ema(df, slow_period)

        close      = ctx.bar.close
        above_slow = close > slow_ma.iloc[-1]

        if crossover(fast_ma, mid_ma).iloc[-1] and above_slow and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / close)
            if qty > 0:
                ctx.buy(qty)

        elif ctx.qty > 0 and (crossunder(fast_ma, mid_ma).iloc[-1] or not above_slow):
            ctx.sell_all()


class _LegacyVwapReversion(StrategyBase):
    name = "legacy_vwap_reversion"

    def on_bar(self, ctx: StrategyContext) -> None:
        period        = self.param("period",        20)
        dev_threshold = self.param("dev_threshold", 0.02)

        df = ctx.history
        if len(df) < period + 1:
            return

        vwap_val = vwap(df, period).iloc[-1]
        if vwap_val is None or vwap_val != vwap_val or vwap_val == 0:
            return

        close     = ctx.bar.close
        deviation = (close - vwap_val) / vwap_val

        if deviation < -dev_threshold and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / close)
            if qty > 0:
                ctx.buy(qty)

        elif ctx.qty > 0 and deviation >= 0:
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
    ("rsi/default", RsiMeanReversionStrategy, _LegacyRsiMeanReversion, {}),
    ("rsi/short", RsiMeanReversionStrategy, _LegacyRsiMeanReversion,
     {"period": 7, "oversold": 25, "overbought": 75}),
    ("rsi/long", RsiMeanReversionStrategy, _LegacyRsiMeanReversion,
     {"period": 28, "oversold": 40, "overbought": 60}),
    ("momentum/default", MomentumStrategy, _LegacyMomentum, {}),
    ("momentum/short", MomentumStrategy, _LegacyMomentum, {"lookback": 5, "threshold": 0.01}),
    ("momentum/long", MomentumStrategy, _LegacyMomentum, {"lookback": 60, "threshold": 0.08}),
    # ── G-a 改造的 10 个 ─────────────────────────────────────
    ("adx_trend/default", AdxTrendStrategy, _LegacyAdxTrend, {}),
    ("adx_trend/loose", AdxTrendStrategy, _LegacyAdxTrend,
     {"fast_period": 5, "slow_period": 20, "adx_period": 10, "adx_threshold": 15}),
    ("adx_trend/strict", AdxTrendStrategy, _LegacyAdxTrend,
     {"fast_period": 15, "slow_period": 45, "adx_period": 20, "adx_threshold": 30}),
    ("atr_breakout/default", AtrBreakoutStrategy, _LegacyAtrBreakout, {}),
    ("atr_breakout/tight", AtrBreakoutStrategy, _LegacyAtrBreakout,
     {"channel_period": 10, "atr_period": 7, "multiplier": 0.2}),
    ("atr_breakout/wide", AtrBreakoutStrategy, _LegacyAtrBreakout,
     {"channel_period": 40, "atr_period": 20, "multiplier": 0.3}),
    ("donchian/default", DonchianBreakoutStrategy, _LegacyDonchianBreakout, {}),
    ("donchian/short", DonchianBreakoutStrategy, _LegacyDonchianBreakout,
     {"period": 10, "exit_period": 5}),
    ("donchian/long", DonchianBreakoutStrategy, _LegacyDonchianBreakout,
     {"period": 55, "exit_period": 20}),
    ("keltner/default", KeltnerBreakoutStrategy, _LegacyKeltnerBreakout, {}),
    ("keltner/tight", KeltnerBreakoutStrategy, _LegacyKeltnerBreakout,
     {"ema_period": 10, "atr_period": 5, "multiplier": 1.0}),
    ("keltner/wide", KeltnerBreakoutStrategy, _LegacyKeltnerBreakout,
     {"ema_period": 30, "atr_period": 20, "multiplier": 3.0}),
    ("multi_factor/default", MultiFactorStrategy, _LegacyMultiFactor, {}),
    ("multi_factor/short", MultiFactorStrategy, _LegacyMultiFactor,
     {"momentum_lookback": 10, "rsi_period": 7, "macd_fast": 6, "macd_slow": 13,
      "macd_signal": 4, "threshold": 1.0}),
    ("multi_factor/weighted", MultiFactorStrategy, _LegacyMultiFactor,
     {"rsi_low": 40, "rsi_high": 60, "threshold": 0.5,
      "w_momentum": 0.5, "w_rsi": 1.5, "w_macd": 2.0}),
    ("pairs/default", PairsTradingStrategy, _LegacyPairsTrading, {}),
    ("pairs/short", PairsTradingStrategy, _LegacyPairsTrading,
     {"lookback": 20, "entry_z": 1.0, "exit_z": 0.25}),
    ("pairs/long", PairsTradingStrategy, _LegacyPairsTrading,
     {"lookback": 90, "entry_z": 1.5, "exit_z": 0.75}),
    # 模式 B（注入配对价格）没走预算，但被重构成了独立方法 —— 同样要对拍
    ("pairs/paired", PairsTradingStrategy, _LegacyPairsTrading, {"price_b_today": 100.0}),
    ("pairs/paired-hedged", PairsTradingStrategy, _LegacyPairsTrading,
     {"price_b_today": 50.0, "lookback": 30, "entry_z": 1.0, "exit_z": 0.4,
      "hedge_ratio": 1.5}),
    ("stochastic/default", StochasticStrategy, _LegacyStochastic, {}),
    ("stochastic/fast", StochasticStrategy, _LegacyStochastic,
     {"k_period": 7, "d_period": 2, "oversold": 30, "overbought": 70}),
    ("stochastic/slow", StochasticStrategy, _LegacyStochastic,
     {"k_period": 21, "d_period": 5, "oversold": 15, "overbought": 85}),
    ("supertrend/default", SupertrendStrategy, _LegacySupertrend, {}),
    ("supertrend/sensitive", SupertrendStrategy, _LegacySupertrend,
     {"period": 7, "multiplier": 1.5}),
    ("supertrend/calm", SupertrendStrategy, _LegacySupertrend,
     {"period": 20, "multiplier": 4.0}),
    ("triple_ma/default", TripleMaStrategy, _LegacyTripleMa, {}),
    ("triple_ma/fast", TripleMaStrategy, _LegacyTripleMa,
     {"fast_period": 3, "mid_period": 8, "slow_period": 21}),
    ("triple_ma/slow", TripleMaStrategy, _LegacyTripleMa,
     {"fast_period": 10, "mid_period": 25, "slow_period": 60}),
    ("vwap/default", VwapReversionStrategy, _LegacyVwapReversion, {}),
    ("vwap/short", VwapReversionStrategy, _LegacyVwapReversion,
     {"period": 10, "dev_threshold": 0.01}),
    ("vwap/long", VwapReversionStrategy, _LegacyVwapReversion,
     {"period": 50, "dev_threshold": 0.04}),
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


#: 单个计时点的重复预算（秒）。快的用例会在预算内多跑几次，慢的跑一次就返回
_TIMING_BUDGET = 0.5


def _best_elapsed(strategy_factory, bars, market) -> tuple[float, BacktestResult]:
    """
    多次运行取**最小值**。

    计时噪声（GC、调度、共享 CI 上的邻居进程）只会让单次结果变大，不会变小，
    所以最小值是比均值稳得多的估计。这在「扣掉引擎地板」的口径下是必需的：
    两个各带 ±50% 噪声的数相减，差值的噪声能盖过被测量本身 —— 实测就见过
    同一个用例的策略侧成本在 0 与 37ms 之间跳。
    """
    elapsed, result = _elapsed(strategy_factory, bars, market)
    spent = elapsed
    while spent < _TIMING_BUDGET:
        again, _ = _elapsed(strategy_factory, bars, market)
        elapsed = min(elapsed, again)
        spent += again
    return elapsed, result


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


#: 逐预设性能用例的行情长度。回测成本是「每 bar 重算」× bar 数，
#: 1500 根足够让 O(n²) 与 O(n) 的差距压过计时噪声，又不至于让整个用例跑成分钟级
_PERF_BARS = 1500

#: 策略侧成本的计时地板（秒）。低于 1ms 的差值在共享 CI 机器上已经全是噪声
_COST_FLOOR = 1e-3


class _NullStrategy(StrategyBase):
    """
    什么都不做的策略：跑一遍等于只付**引擎自身**的成本。

    它是逐预设加速比的分母基准。端到端时间里始终压着这块引擎地板，
    而这一项优化只动得了策略侧那部分 —— `pairs_trading` 改造前的策略侧成本
    是 O(n·lookback)（尾窗统计量，与引擎同阶），端到端比值天然封顶在 2× 左右。
    拿端到端比值当门禁，等于要求一件这项优化做不到、也不该做到的事。
    """

    name = "null"

    def on_bar(self, ctx: StrategyContext) -> None:
        return

#: G-a 改造的 10 个预设（`_PARAM_SETS` 里的标签前缀）
_GA_PRESETS = frozenset({
    "adx_trend", "atr_breakout", "donchian", "keltner", "multi_factor",
    "pairs", "stochastic", "supertrend", "triple_ma", "vwap",
})

#: 逐预设性能用例：G-a 改造的 10 个（默认参数）
_PERF_CASES = tuple(
    (label, new_cls, legacy_cls, params)
    for label, new_cls, legacy_cls, params in _PARAM_SETS
    if label.endswith("/default") and label.split("/")[0] in _GA_PRESETS
)

# 标签是靠字符串匹配挑出来的：改了 `_PARAM_SETS` 里的标签而忘了改这里，
# 用例会**静默消失**而不是变红。这行让它变红。
assert len(_PERF_CASES) == len(_GA_PRESETS), (
    f"性能用例只挑出 {len(_PERF_CASES)} 个，应为 {len(_GA_PRESETS)} 个 —— "
    f"`_PARAM_SETS` 的标签与 `_GA_PRESETS` 对不上了"
)


@pytest.mark.parametrize(
    ("label", "new_cls", "legacy_cls", "params"),
    _PERF_CASES,
    ids=[label.split("/")[0] for label, _, _, _ in _PERF_CASES],
)
def test_ga_preset_is_faster_and_identical(
    label: str,
    new_cls: type[StrategyBase],
    legacy_cls: type[StrategyBase],
    params: dict,
) -> None:
    """
    G-a 改造的每个预设：先断言逐笔一致，再比时间。

    与上面的参数矩阵用例分工不同 —— 那里覆盖参数面，这里覆盖「优化没失效」。
    两条断言必须在同一个用例里：拆开就给了「快了但不对」一个藏身处。
    """
    market, symbol = Market.US, "AAPL"
    bars = make_bars(symbol, market, n=_PERF_BARS, seed=17)

    engine_floor, _ = _best_elapsed(_NullStrategy, bars, market)
    legacy_time, legacy = _best_elapsed(lambda: legacy_cls(params), bars, market)
    new_time, updated = _best_elapsed(lambda: new_cls(params), bars, market)

    assert fingerprints(updated) == fingerprints(legacy), f"{label} 成交漂移"
    assert legacy.fills, f"{label} 在这份数据上没有成交，性能对比没有代表性"

    # 扣掉引擎地板后比的才是这一项真正动过的那部分。差值小于 _COST_FLOOR
    # 时已经全是计时噪声（甚至为负），按地板计 —— 印出来的是**下界**，不是实测值
    legacy_cost = max(legacy_time - engine_floor, _COST_FLOOR)
    new_cost = max(new_time - engine_floor, _COST_FLOOR)
    speedup = legacy_cost / new_cost
    print(
        f"\n[G-a {label:22s}] bars={len(bars)} 成交={len(legacy.fills):3d} 笔（逐笔一致）"
        f"  端到端 {legacy_time * 1000:7.1f} → {new_time * 1000:6.1f} ms"
        f"（{legacy_time / new_time:5.2f}×）"
        f"  策略侧 {legacy_cost * 1000:7.1f} → {new_cost * 1000:5.1f} ms"
        f"（≥{speedup:7.1f}×）"
    )
    assert speedup >= _MIN_SPEEDUP, f"{label} 策略侧加速比只有 {speedup:.2f}×，预算路径可能已失效"


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
