"""Supertrend 趋势跟踪策略

基于 ATR 的动态支撑阻力线，方向翻转时触发买卖信号。
适合趋势明显的市场，对震荡市有较强抗噪能力。

参数:
  period      — ATR 计算周期（默认 10）
  multiplier  — ATR 通道倍数（默认 3.0）；越大越不敏感

> **E-a 指标预算**（G-a 改造）：`supertrend()` 是**递归**指标 —— 每一根的
> 上下轨与方向都依赖前一根的结果。递归本身不妨碍预算：它的循环严格
> `for i in range(1, n)` 只读 `i-1`，所以「用前缀算」与「用全帧算」在前缀段上
> 逐位相同（已验证到位级相等），因果抽检也据此放行。
> 真正会出问题的是引用未来的递归，这里没有。
"""

from __future__ import annotations

from app.strategy.base import IndicatorSpec, StrategyBase
from app.strategy.context import StrategyContext
from app.strategy.indicators import supertrend


class SupertrendStrategy(StrategyBase):
    name = "supertrend"
    description = "Supertrend ATR 趋势跟踪策略"

    def declare_indicators(self, spec: IndicatorSpec) -> None:
        spec.add_multi(
            ("st_line", "direction"),
            supertrend,
            self.param("period", 10),
            self.param("multiplier", 3.0),
        )

    def on_bar(self, ctx: StrategyContext) -> None:
        period = self.param("period", 10)

        ind = ctx.ind
        # 热身守卫保持改造前口径（`len(df) < period * 2 + 2`）。
        if ind.bars_seen < period * 2 + 2:
            return

        cur_dir  = ind.value("direction")
        prev_dir = ind.value("direction", 1)
        # 改造前的第二道守卫是 `if len(direction) < 2: return`。方向序列与行情帧
        # 等长，取不到往回一根就等价于那个条件 —— 换了写法，口径没换。
        if cur_dir is None or prev_dir is None:
            return

        # direction 在预算路径上是 float64（框架统一转 float），取值仍是 ±1 的整数值
        # 方向从 -1 → 1：趋势翻多 → 买入
        if prev_dir == -1 and cur_dir == 1 and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / ctx.bar.close)
            if qty > 0:
                ctx.buy(qty)

        # 方向从 1 → -1：趋势翻空 → 全平
        elif prev_dir == 1 and cur_dir == -1 and ctx.qty > 0:
            ctx.sell_all()
