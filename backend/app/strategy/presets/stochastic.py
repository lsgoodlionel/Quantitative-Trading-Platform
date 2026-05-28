"""随机指标策略（Stochastic Oscillator）

%K 从超卖区上穿 %D 时买入，从超买区下穿时卖出。
比 RSI 更敏感，适合短周期震荡行情。

参数:
  k_period    — %K 计算周期（默认 14）
  d_period    — %D 平滑周期（默认 3）
  oversold    — 超卖阈值（默认 20）；%K 低于此值视为超卖
  overbought  — 超买阈值（默认 80）；%K 高于此值视为超买
"""

from __future__ import annotations

from app.strategy.base import StrategyBase
from app.strategy.context import StrategyContext
from app.strategy.indicators import stochastic, crossover, crossunder


class StochasticStrategy(StrategyBase):
    name = "stochastic"
    description = "随机指标 %K/%D 超买超卖策略"

    def on_bar(self, ctx: StrategyContext) -> None:
        k_period   = self.param("k_period",   14)
        d_period   = self.param("d_period",   3)
        oversold   = self.param("oversold",   20)
        overbought = self.param("overbought", 80)

        df = ctx.history
        if len(df) < k_period + d_period + 2:
            return

        k, d = stochastic(df, k_period, d_period)

        # %K 上穿 %D 且处于超卖区 → 买入信号
        if crossover(k, d).iloc[-1] and k.iloc[-1] < oversold and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / ctx.bar.close)
            if qty > 0:
                ctx.buy(qty)

        # %K 下穿 %D 且处于超买区 → 卖出信号
        elif crossunder(k, d).iloc[-1] and k.iloc[-1] > overbought and ctx.qty > 0:
            ctx.sell_all()
