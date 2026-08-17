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

> **E-a 指标预算**（G-a 改造）：三条 EMA 一次算完。EMA 是从 0 起的前向递推，
> 前缀重算与全帧重算逐位一致 —— 成交与改造前**逐笔一致**。
"""

from __future__ import annotations

from app.strategy.base import IndicatorSpec, StrategyBase
from app.strategy.context import StrategyContext
from app.strategy.indicators import ema


class TripleMaStrategy(StrategyBase):
    name = "triple_ma"
    description = "三均线顺势策略（快/中/慢三线排列确认趋势）"

    def declare_indicators(self, spec: IndicatorSpec) -> None:
        spec.add("fast", ema, self.param("fast_period", 5))
        spec.add("mid", ema, self.param("mid_period", 13))
        spec.add("slow", ema, self.param("slow_period", 34))

    def on_bar(self, ctx: StrategyContext) -> None:
        slow_period = self.param("slow_period", 34)

        ind = ctx.ind
        # 热身守卫保持改造前口径（`len(df) < slow_period + 2`）。
        if ind.bars_seen < slow_period + 2:
            return

        close    = ctx.bar.close
        slow_val = ind.value("slow")
        # 改造前是 `close > slow_ma.iloc[-1]`，NaN 时静默得 False。
        above_slow = slow_val is not None and close > slow_val

        # 三线多头排列 + 快线上穿中线 → 买入
        if ind.crossed_up("fast", "mid") and above_slow and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / close)
            if qty > 0:
                ctx.buy(qty)

        # 快线下穿中线 或价格跌破慢线 → 卖出
        elif ctx.qty > 0 and (ind.crossed_down("fast", "mid") or not above_slow):
            ctx.sell_all()
