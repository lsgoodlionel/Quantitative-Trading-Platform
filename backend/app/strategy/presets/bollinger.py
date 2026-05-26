"""布林带均值回归策略（Bollinger Bands Mean Reversion）

价格触及下轨买入，触及上轨或中轨平仓。
参数:
  period   — 布林带周期（默认 20）
  std_dev  — 标准差倍数（默认 2.0）
"""

from __future__ import annotations

from app.strategy.base import StrategyBase
from app.strategy.context import StrategyContext
from app.strategy.indicators import bollinger_bands


class BollingerStrategy(StrategyBase):
    name = "bollinger"
    description = "布林带均值回归策略"

    def on_bar(self, ctx: StrategyContext) -> None:
        period = self.param("period", 20)
        std_dev = self.param("std_dev", 2.0)

        df = ctx.history
        if len(df) < period + 1:
            return

        upper, mid, lower = bollinger_bands(df, period, std_dev)
        close = ctx.bar.close

        if close <= lower.iloc[-1] and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / close)
            if qty > 0:
                ctx.buy(qty)

        elif ctx.qty > 0 and (close >= mid.iloc[-1] or close >= upper.iloc[-1]):
            ctx.sell_all()
