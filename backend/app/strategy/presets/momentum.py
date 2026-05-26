"""动量策略（Price Momentum）

基于过去 N 天收益率的动量效应：收益率为正且超过阈值时买入，转负时卖出。
参数:
  lookback     — 动量回望周期（默认 20）
  threshold    — 入场动量阈值，如 0.05 = 5%（默认 0.03）
"""

from __future__ import annotations

from app.strategy.base import StrategyBase
from app.strategy.context import StrategyContext


class MomentumStrategy(StrategyBase):
    name = "momentum"
    description = "价格动量策略（过去N日收益率）"

    def on_bar(self, ctx: StrategyContext) -> None:
        lookback = self.param("lookback", 20)
        threshold = self.param("threshold", 0.03)

        df = ctx.history
        if len(df) < lookback + 1:
            return

        past_close = df["close"].iloc[-(lookback + 1)]
        current_close = ctx.bar.close
        momentum = (current_close - past_close) / past_close

        if momentum > threshold and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / current_close)
            if qty > 0:
                ctx.buy(qty)

        elif momentum < 0 and ctx.qty > 0:
            ctx.sell_all()
