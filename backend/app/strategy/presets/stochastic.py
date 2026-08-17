"""随机指标策略（Stochastic Oscillator）

%K 从超卖区上穿 %D 时买入，从超买区下穿时卖出。
比 RSI 更敏感，适合短周期震荡行情。

参数:
  k_period    — %K 计算周期（默认 14）
  d_period    — %D 平滑周期（默认 3）
  oversold    — 超卖阈值（默认 20）；%K 低于此值视为超卖
  overbought  — 超买阈值（默认 80）；%K 高于此值视为超买

> **E-a 指标预算**（G-a 改造）：`stochastic()` 一次返回 %K/%D，用 `add_multi`
> 按位置绑定；金叉死叉走 `crossed_up` / `crossed_down`，与
> `crossover(...).iloc[-1]` 逐位等价（任一端为 NaN 时两者同样给 False）。
"""

from __future__ import annotations

from app.strategy.base import IndicatorSpec, StrategyBase
from app.strategy.context import StrategyContext
from app.strategy.indicators import stochastic


class StochasticStrategy(StrategyBase):
    name = "stochastic"
    description = "随机指标 %K/%D 超买超卖策略"

    def declare_indicators(self, spec: IndicatorSpec) -> None:
        spec.add_multi(
            ("k", "d"),
            stochastic,
            self.param("k_period", 14),
            self.param("d_period", 3),
        )

    def on_bar(self, ctx: StrategyContext) -> None:
        k_period   = self.param("k_period",   14)
        d_period   = self.param("d_period",   3)
        oversold   = self.param("oversold",   20)
        overbought = self.param("overbought", 80)

        ind = ctx.ind
        # 热身守卫保持改造前口径（`len(df) < k_period + d_period + 2`）。
        if ind.bars_seen < k_period + d_period + 2:
            return

        # %K 的分母是 `(high_max - low_min).replace(0, nan)`，横盘段中途也会出
        # NaN —— 改造前 `k.iloc[-1] < oversold` 在那里静默得 False，这里判 None 同义。
        k_val = ind.value("k")

        # %K 上穿 %D 且处于超卖区 → 买入信号
        if ind.crossed_up("k", "d") and k_val is not None and k_val < oversold and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / ctx.bar.close)
            if qty > 0:
                ctx.buy(qty)

        # %K 下穿 %D 且处于超买区 → 卖出信号
        elif (
            ind.crossed_down("k", "d")
            and k_val is not None
            and k_val > overbought
            and ctx.qty > 0
        ):
            ctx.sell_all()
