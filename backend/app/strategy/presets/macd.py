"""MACD 趋势策略

MACD 线上穿信号线买入，下穿信号线卖出。
参数:
  fast    — 快线 EMA 周期（默认 12）
  slow    — 慢线 EMA 周期（默认 26）
  signal  — 信号线 EMA 周期（默认 9）
"""

from __future__ import annotations

from app.strategy.base import StrategyBase
from app.strategy.context import StrategyContext
from app.strategy.indicators import macd, crossover, crossunder


class MacdStrategy(StrategyBase):
    name = "macd"
    description = "MACD 金叉死叉趋势策略"

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
