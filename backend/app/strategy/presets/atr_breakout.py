"""ATR 波动率突破策略

以 N 日价格区间为基础，叠加 k 倍 ATR 作为动态突破阈值。
价格突破上方阈值时买入；跌破下方阈值时止损。
对高波动标的更有效（ATR 越大，突破门槛越高，质量越高）。

参数:
  channel_period  — 价格区间参考周期（默认 20）
  atr_period      — ATR 计算周期（默认 14）
  multiplier      — ATR 突破加成倍数（默认 0.5）
"""

from __future__ import annotations

from app.strategy.base import StrategyBase
from app.strategy.context import StrategyContext
from app.strategy.indicators import atr, highest, lowest


class AtrBreakoutStrategy(StrategyBase):
    name = "atr_breakout"
    description = "ATR 动态波动率突破策略"

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

        # 突破上方动态阈值 → 买入
        if close > breakout_up and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / close)
            if qty > 0:
                ctx.buy(qty)

        # 跌破下方动态阈值 → 止损
        elif ctx.qty > 0 and close < breakout_down:
            ctx.sell_all()
