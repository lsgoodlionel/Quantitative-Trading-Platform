"""ATR 波动率突破策略

以 N 日价格区间为基础，叠加 k 倍 ATR 作为动态突破阈值。
价格突破上方阈值时买入；跌破下方阈值时止损。
对高波动标的更有效（ATR 越大，突破门槛越高，质量越高）。

参数:
  channel_period  — 价格区间参考周期（默认 20）
  atr_period      — ATR 计算周期（默认 14）
  multiplier      — ATR 突破加成倍数（默认 0.5）

> **E-a 指标预算**（G-a 改造）：ATR 与前 N 日高低点都进预算。区间取的是
> `iloc[-2]`（不含当前 bar），在预算路径上就是 `value(name, offset=1)` ——
> 偏移只能往回，视图不提供负偏移。成交与改造前**逐笔一致**。
"""

from __future__ import annotations

from app.strategy.base import IndicatorSpec, StrategyBase
from app.strategy.context import StrategyContext
from app.strategy.indicators import atr, highest, lowest


class AtrBreakoutStrategy(StrategyBase):
    name = "atr_breakout"
    description = "ATR 动态波动率突破策略"

    def declare_indicators(self, spec: IndicatorSpec) -> None:
        channel_period = self.param("channel_period", 20)
        spec.add("atr", atr, self.param("atr_period", 14))
        spec.add("high_n", highest, channel_period)
        spec.add("low_n", lowest, channel_period)

    def on_bar(self, ctx: StrategyContext) -> None:
        channel_period = self.param("channel_period", 20)
        atr_period     = self.param("atr_period",     14)
        multiplier     = self.param("multiplier",     0.5)

        ind = ctx.ind
        # 热身守卫保持改造前口径（`len(df) < max(channel_period, atr_period) + 2`）。
        min_bars = max(channel_period, atr_period) + 2
        if ind.bars_seen < min_bars:
            return

        close  = ctx.bar.close
        atr_v  = ind.value("atr")
        high_n = ind.value("high_n", 1)   # 前 N 日最高（不含当前）
        low_n  = ind.value("low_n",  1)

        # 改造前这三个值取到 NaN 时，两条阈值都是 NaN，两个比较都静默得 False。
        # 显式判 None 是同一个结果，只是把「还没数据」写在了明面上。
        if atr_v is None or high_n is None or low_n is None:
            return

        breakout_up   = high_n + multiplier * atr_v
        breakout_down = low_n  - multiplier * atr_v

        # 突破上方动态阈值 → 买入
        if close > breakout_up and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / close)
            if qty > 0:
                ctx.buy(qty)

        # 跌破下方动态阈值 → 止损
        elif ctx.qty > 0 and close < breakout_down:
            ctx.sell_all()
