"""ADX 趋势过滤双均线策略

在标准双均线基础上叠加 ADX 趋势强度过滤：
只有当 ADX 超过阈值（趋势足够强）时才跟随均线金叉信号，
避免在震荡市中产生大量亏损交易。

参数:
  fast_period    — 快线 SMA 周期（默认 10）
  slow_period    — 慢线 SMA 周期（默认 30）
  adx_period     — ADX 计算周期（默认 14）
  adx_threshold  — ADX 进场门槛（默认 25）；ADX 越高趋势越强
"""

from __future__ import annotations

from app.strategy.base import StrategyBase
from app.strategy.context import StrategyContext
from app.strategy.indicators import sma, adx, crossover, crossunder


class AdxTrendStrategy(StrategyBase):
    name = "adx_trend"
    description = "ADX 趋势强度过滤双均线策略（仅强趋势交易）"

    def on_bar(self, ctx: StrategyContext) -> None:
        fast_period   = self.param("fast_period",   10)
        slow_period   = self.param("slow_period",   30)
        adx_period    = self.param("adx_period",    14)
        adx_threshold = self.param("adx_threshold", 25)

        df = ctx.history
        min_bars = max(slow_period, adx_period * 2) + 2
        if len(df) < min_bars:
            return

        fast_ma = sma(df, fast_period)
        slow_ma = sma(df, slow_period)
        adx_val = adx(df, adx_period).iloc[-1]

        if adx_val != adx_val:   # NaN guard
            return

        trend_strong = adx_val > adx_threshold

        # ADX 确认趋势 + 金叉 → 买入
        if trend_strong and crossover(fast_ma, slow_ma).iloc[-1] and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / ctx.bar.close)
            if qty > 0:
                ctx.buy(qty)

        # 死叉 或趋势减弱 → 卖出
        elif ctx.qty > 0 and (crossunder(fast_ma, slow_ma).iloc[-1] or not trend_strong):
            ctx.sell_all()
