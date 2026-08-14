"""双均线策略（Double Moving Average Crossover）

经典趋势跟踪策略：快线上穿慢线买入，快线下穿慢线卖出。
参数:
  fast_period  — 快线周期（默认 10）
  slow_period  — 慢线周期（默认 30）
  ma_type      — 均线类型：sma / ema（默认 sma）

> **E-a 指标预算**：均线在 `declare_indicators` 里声明，框架一次算完，
> `on_bar` 只按游标取值。改造前是每根 bar 对全量历史重算两条均线 + 两次
> crossover（实测占整轮回测 86% 的时间）。改造后成交与改造前**逐笔一致** ——
> 见 `tests/test_preset_parity.py`。
"""

from __future__ import annotations

from app.strategy.base import IndicatorSpec, StrategyBase
from app.strategy.context import StrategyContext
from app.strategy.indicators import ema, sma


class DoubleMaStrategy(StrategyBase):
    name = "double_ma"
    description = "双均线金叉死叉趋势策略"

    def declare_indicators(self, spec: IndicatorSpec) -> None:
        moving_average = ema if self.param("ma_type", "sma") == "ema" else sma
        spec.add("fast", moving_average, self.param("fast_period", 10))
        spec.add("slow", moving_average, self.param("slow_period", 30))

    def on_bar(self, ctx: StrategyContext) -> None:
        slow = self.param("slow_period", 30)

        ind = ctx.ind
        # 热身期守卫保持改造前的口径（`len(df) < slow + 1`）。
        # `bars_seen` 与 `len(ctx.history)` 同源，但省掉一次 DataFrame 切片。
        if ind.bars_seen < slow + 1:
            return

        if ind.crossed_up("fast", "slow") and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / ctx.bar.close)
            if qty > 0:
                ctx.buy(qty)

        elif ind.crossed_down("fast", "slow") and ctx.qty > 0:
            ctx.sell_all()
