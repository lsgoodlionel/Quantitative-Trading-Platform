"""唐奇安通道突破策略（海龟交易法则）

价格突破前 N 日最高价买入，跌破短期最低价出场。
Richard Dennis 海龟实验的核心策略，适合强趋势行情。

标准规则：
  - 买入：今日收盘价 > 前 N 日最高价（upper.iloc[-2]，不含今日 high）
  - 出场：今日收盘价 < 前 M 日最低价（exit_lower.iloc[-2]，不含今日 low）

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

        upper, _, _      = donchian_channels(df, period)
        _, _, exit_lower = donchian_channels(df, exit_period)

        close = ctx.bar.close

        # 标准海龟规则：今日收盘 > 前 N 日通道上轨（不含当日 high）
        prev_upper = upper.iloc[-2]
        prev_exit_lower = exit_lower.iloc[-2]

        if close > prev_upper and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / close)
            if qty > 0:
                ctx.buy(qty)

        elif ctx.qty > 0 and close < prev_exit_lower:
            ctx.sell_all()
