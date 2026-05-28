"""Supertrend 趋势跟踪策略

基于 ATR 的动态支撑阻力线，方向翻转时触发买卖信号。
适合趋势明显的市场，对震荡市有较强抗噪能力。

参数:
  period      — ATR 计算周期（默认 10）
  multiplier  — ATR 通道倍数（默认 3.0）；越大越不敏感
"""

from __future__ import annotations

from app.strategy.base import StrategyBase
from app.strategy.context import StrategyContext
from app.strategy.indicators import supertrend


class SupertrendStrategy(StrategyBase):
    name = "supertrend"
    description = "Supertrend ATR 趋势跟踪策略"

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

        # 方向从 -1 → 1：趋势翻多 → 买入
        if prev_dir == -1 and cur_dir == 1 and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / ctx.bar.close)
            if qty > 0:
                ctx.buy(qty)

        # 方向从 1 → -1：趋势翻空 → 全平
        elif prev_dir == 1 and cur_dir == -1 and ctx.qty > 0:
            ctx.sell_all()
