"""MACD 趋势策略

MACD 线上穿信号线买入，下穿信号线卖出。
参数:
  fast    — 快线 EMA 周期（默认 12）
  slow    — 慢线 EMA 周期（默认 26）
  signal  — 信号线 EMA 周期（默认 9）

> **E-a 指标预算**：`macd()` 一次返回三条线，用 `spec.add_multi` 按位置绑定。
> EMA 是从 0 起的前向递推，用前缀算与用全帧算逐位一致 —— 这正是预算能与
> 改造前逐笔一致的原因（`tests/test_preset_parity.py`）。
"""

from __future__ import annotations

from app.strategy.base import IndicatorSpec, StrategyBase
from app.strategy.context import StrategyContext
from app.strategy.indicators import macd


class MacdStrategy(StrategyBase):
    name = "macd"
    description = "MACD 金叉死叉趋势策略"

    def declare_indicators(self, spec: IndicatorSpec) -> None:
        spec.add_multi(
            ("macd_line", "signal_line", "histogram"),
            macd,
            self.param("fast", 12),
            self.param("slow", 26),
            self.param("signal", 9),
        )

    def on_bar(self, ctx: StrategyContext) -> None:
        slow = self.param("slow", 26)
        signal = self.param("signal", 9)

        ind = ctx.ind
        if ind.bars_seen < slow + signal + 1:
            return

        if ind.crossed_up("macd_line", "signal_line") and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / ctx.bar.close)
            if qty > 0:
                ctx.buy(qty)

        elif ind.crossed_down("macd_line", "signal_line") and ctx.qty > 0:
            ctx.sell_all()
