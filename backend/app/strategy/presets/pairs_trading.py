"""配对交易策略（Pairs Trading / Statistical Arbitrage）

正式配对交易需要两个协整标的同时运行。
本实现提供两种模式：

模式 A — 单标的 Z-score 模式（默认，无需第二标的）：
  计算价格相对自身长期均线的标准化偏离（Z-score），
  当价格显著低于历史均值时买入，回归后平仓。
  本质上是带统计意义的均值回归策略。

模式 B — 双标的模式（需通过 params 注入 symbol_b 当日价格）：
  以对数价差 log(A/B) 的 Z-score 作为套利信号，
  需同时传入配对标的每日收盘价（适合专业用户）。

参数:
  entry_z    — 开仓 Z-score 阈值（默认 2.0）；偏离越大越保守
  exit_z     — 平仓 Z-score 阈值（默认 0.5）；越小越快平仓
  lookback   — 统计窗口（默认 60）；越长基准越稳定
  hedge_ratio — 对冲比例（双标的模式，默认 1.0）
"""

from __future__ import annotations

import math

import pandas as pd

from app.strategy.base import StrategyBase
from app.strategy.context import StrategyContext


class PairsTradingStrategy(StrategyBase):
    name = "pairs_trading"
    description = "配对套利 — Z-score 均值回归（单/双标的）"

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

        # ── 模式 B：双标的（需 params 注入 price_b_today） ──────────
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

        # ── 模式 A：单标的 — 价格 vs 长期 SMA 的 Z-score ──────────
        close_series = df["close"].iloc[-lookback:]
        mean = close_series.mean()
        std  = close_series.std()
        if std < 1e-10:
            return

        z = (ctx.bar.close - mean) / std

        # 价格显著低于历史均值（统计意义上超卖）→ 买入
        if z < -entry_z and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / ctx.bar.close)
            if qty > 0:
                ctx.buy(qty)

        # 价格显著高于历史均值（统计意义上超买）→ 卖出
        elif z > entry_z and ctx.qty > 0:
            ctx.sell_all()

        # 价格回归均值区间 → 平仓
        elif abs(z) < exit_z and ctx.qty > 0:
            ctx.sell_all()
