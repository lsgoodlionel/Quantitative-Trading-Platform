"""三均线趋势策略（Triple Moving Average）

快线、中线、慢线三线确认趋势方向：
- 三线多头排列（快 > 中 > 慢）且快线上穿中线 → 买入
- 快线下穿中线 或价格跌破慢线 → 卖出

斐波那契周期组合（5/13/34）是常见选择，
适合中短周期趋势行情，比双均线过滤了更多噪声。

参数:
  fast_period  — 快线 EMA 周期（默认 5）
  mid_period   — 中线 EMA 周期（默认 13）
  slow_period  — 慢线 EMA 周期（默认 34）
"""

from __future__ import annotations

from app.strategy.base import StrategyBase
from app.strategy.context import StrategyContext
from app.strategy.indicators import ema, crossover, crossunder


class TripleMaStrategy(StrategyBase):
    name = "triple_ma"
    description = "三均线顺势策略（快/中/慢三线排列确认趋势）"

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

        # 三线多头排列 + 快线上穿中线 → 买入
        if crossover(fast_ma, mid_ma).iloc[-1] and above_slow and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / close)
            if qty > 0:
                ctx.buy(qty)

        # 快线下穿中线 或价格跌破慢线 → 卖出
        elif ctx.qty > 0 and (crossunder(fast_ma, mid_ma).iloc[-1] or not above_slow):
            ctx.sell_all()
