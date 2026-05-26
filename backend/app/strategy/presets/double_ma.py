"""双均线策略（Double Moving Average Crossover）

经典趋势跟踪策略：快线上穿慢线买入，快线下穿慢线卖出。
参数:
  fast_period  — 快线周期（默认 10）
  slow_period  — 慢线周期（默认 30）
  ma_type      — 均线类型：sma / ema（默认 sma）
"""

from __future__ import annotations

from app.strategy.base import StrategyBase
from app.strategy.context import StrategyContext
from app.strategy.indicators import sma, ema, crossover, crossunder


class DoubleMaStrategy(StrategyBase):
    name = "double_ma"
    description = "双均线金叉死叉趋势策略"

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
