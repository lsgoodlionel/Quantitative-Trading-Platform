"""ADX 趋势过滤双均线策略

在标准双均线基础上叠加 ADX 趋势强度过滤：
只有当 ADX 超过阈值（趋势足够强）时才跟随均线金叉信号，
避免在震荡市中产生大量亏损交易。

参数:
  fast_period    — 快线 SMA 周期（默认 10）
  slow_period    — 慢线 SMA 周期（默认 30）
  adx_period     — ADX 计算周期（默认 14）
  adx_threshold  — ADX 进场门槛（默认 25）；ADX 越高趋势越强

> **E-a 指标预算**（G-a 改造）：两条均线 + ADX 在 `declare_indicators` 里声明，
> 框架一次算完，`on_bar` 只按游标取值。三者都是 rolling 前向递推，前缀重算与
> 全帧重算逐位一致 —— 成交与改造前**逐笔一致**（`tests/test_preset_parity.py`）。
"""

from __future__ import annotations

from app.strategy.base import IndicatorSpec, StrategyBase
from app.strategy.context import StrategyContext
from app.strategy.indicators import adx, sma


class AdxTrendStrategy(StrategyBase):
    name = "adx_trend"
    description = "ADX 趋势强度过滤双均线策略（仅强趋势交易）"

    def declare_indicators(self, spec: IndicatorSpec) -> None:
        spec.add("fast", sma, self.param("fast_period", 10))
        spec.add("slow", sma, self.param("slow_period", 30))
        spec.add("adx", adx, self.param("adx_period", 14))

    def on_bar(self, ctx: StrategyContext) -> None:
        slow_period   = self.param("slow_period",   30)
        adx_period    = self.param("adx_period",    14)
        adx_threshold = self.param("adx_threshold", 25)

        ind = ctx.ind
        # 热身守卫保持改造前口径（`len(df) < max(slow, adx_period*2) + 2`）。
        min_bars = max(slow_period, adx_period * 2) + 2
        if ind.bars_seen < min_bars:
            return

        # 改造前是 `if adx_val != adx_val: return` 的 NaN 守卫。ADX 分母上有两次
        # `replace(0, nan)`，横盘段中途也可能给出 NaN，这道守卫不只挡热身期。
        adx_val = ind.value("adx")
        if adx_val is None:
            return

        trend_strong = adx_val > adx_threshold

        # ADX 确认趋势 + 金叉 → 买入
        if trend_strong and ind.crossed_up("fast", "slow") and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / ctx.bar.close)
            if qty > 0:
                ctx.buy(qty)

        # 死叉 或趋势减弱 → 卖出
        elif ctx.qty > 0 and (ind.crossed_down("fast", "slow") or not trend_strong):
            ctx.sell_all()
