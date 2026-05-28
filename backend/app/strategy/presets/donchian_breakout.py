"""唐奇安通道突破策略（海龟交易法则）

价格突破 N 日高点买入，跌破短期低点出场。
Richard Dennis 海龟实验的核心策略，适合强趋势行情。

参数:
  period       — 入场突破周期（默认 20）；价格创 N 日新高则买入
  exit_period  — 出场周期（默认 10）；价格跌破 M 日新低则卖出
"""

from __future__ import annotations

from app.strategy.base import StrategyBase
from app.strategy.context import StrategyContext
from app.strategy.indicators import donchian_channels


class DonchianBreakoutStrategy(StrategyBase):
    name = "donchian_breakout"
    description = "唐奇安通道突破（海龟交易法则）"

    def on_bar(self, ctx: StrategyContext) -> None:
        period = self.param("period", 20)
        exit_period = self.param("exit_period", 10)

        df = ctx.history
        if len(df) < period + 2:
            return

        upper, _, _  = donchian_channels(df, period)
        _, _, exit_lower = donchian_channels(df, exit_period)

        close      = ctx.bar.close
        prev_close = df["close"].iloc[-2]
        prev_upper = upper.iloc[-2]

        # 价格突破上轨 → 入场做多
        if prev_close < prev_upper and close >= upper.iloc[-1] and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / close)
            if qty > 0:
                ctx.buy(qty)

        # 价格跌破短期下轨 → 出场
        elif ctx.qty > 0 and close < exit_lower.iloc[-1]:
            ctx.sell_all()
