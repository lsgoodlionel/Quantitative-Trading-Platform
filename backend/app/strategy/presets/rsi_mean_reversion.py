"""RSI 均值回归策略

RSI 超卖买入，超买卖出。
参数:
  period      — RSI 周期（默认 14）
  oversold    — 超卖阈值（默认 30）
  overbought  — 超买阈值（默认 70）
"""

from __future__ import annotations

from app.strategy.base import StrategyBase
from app.strategy.context import StrategyContext
from app.strategy.indicators import rsi


class RsiMeanReversionStrategy(StrategyBase):
    name = "rsi_mean_reversion"
    description = "RSI 超卖买入、超买卖出均值回归"

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
