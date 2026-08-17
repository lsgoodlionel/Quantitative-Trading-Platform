"""动量策略（Price Momentum）

基于过去 N 天收益率的动量效应：收益率为正且超过阈值时买入，转负时卖出。
参数:
  lookback     — 动量回望周期（默认 20）
  threshold    — 入场动量阈值，如 0.05 = 5%（默认 0.03）
"""

from __future__ import annotations

import pandas as pd

from app.strategy.base import IndicatorSpec, StrategyBase
from app.strategy.context import StrategyContext


def momentum_series(frame: pd.DataFrame, lookback: int) -> pd.Series:
    """过去 `lookback` 根的收益率。`shift(lookback)` 是向**后**看，因果。

    与改造前 `df["close"].iloc[-(lookback + 1)]` 等价：history 含当前 bar，
    所以 `iloc[-1]` 是当前、`iloc[-(lookback+1)]` 正是 `shift(lookback)` 在末位的值。
    """
    past = frame["close"].shift(lookback)
    return (frame["close"] - past) / past


class MomentumStrategy(StrategyBase):
    name = "momentum"
    description = "价格动量策略（过去N日收益率）"

    def declare_indicators(self, spec: IndicatorSpec) -> None:
        spec.add("momentum", momentum_series, self.param("lookback", 20))

    def on_bar(self, ctx: StrategyContext) -> None:
        lookback = self.param("lookback", 20)
        threshold = self.param("threshold", 0.03)

        ind = ctx.ind
        if ind.bars_seen < lookback + 1:
            return

        momentum = ind.value("momentum")
        if momentum is None:
            return
        current_close = ctx.bar.close

        if momentum > threshold and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / current_close)
            if qty > 0:
                ctx.buy(qty)

        elif momentum < 0 and ctx.qty > 0:
            ctx.sell_all()
