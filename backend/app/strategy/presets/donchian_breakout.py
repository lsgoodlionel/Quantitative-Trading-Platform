"""唐奇安通道突破策略（海龟交易法则）

价格突破前 N 日最高价买入，跌破短期最低价出场。
Richard Dennis 海龟实验的核心策略，适合强趋势行情。

标准规则：
  - 买入：今日收盘价 > 前 N 日最高价（entry_upper 往回一根，不含今日 high）
  - 出场：今日收盘价 < 前 M 日最低价（exit_lower 往回一根，不含今日 low）

参数:
  period       — 入场突破周期（默认 20）；价格创 N 日新高则买入
  exit_period  — 出场周期（默认 10）；价格跌破 M 日新低则卖出

> **E-a 指标预算**（G-a 改造）：入场通道与出场通道是两条独立声明。
> `donchian_channels` 返回三元组，`add_multi` 按位置绑定，因此两组都要给满
> 三个名字（哪怕只用其中一条）—— 名字全局唯一，用 entry_/exit_ 前缀区分。
"""

from __future__ import annotations

from app.strategy.base import IndicatorSpec, StrategyBase
from app.strategy.context import StrategyContext
from app.strategy.indicators import donchian_channels


class DonchianBreakoutStrategy(StrategyBase):
    name = "donchian_breakout"
    description = "唐奇安通道突破（海龟交易法则）"

    def declare_indicators(self, spec: IndicatorSpec) -> None:
        spec.add_multi(
            ("entry_upper", "entry_mid", "entry_lower"),
            donchian_channels,
            self.param("period", 20),
        )
        spec.add_multi(
            ("exit_upper", "exit_mid", "exit_lower"),
            donchian_channels,
            self.param("exit_period", 10),
        )

    def on_bar(self, ctx: StrategyContext) -> None:
        period = self.param("period", 20)

        ind = ctx.ind
        # 热身守卫保持改造前口径（`len(df) < period + 2`）。注意它只看入场周期，
        # 不看 exit_period —— 改造前如此，照搬。
        if ind.bars_seen < period + 2:
            return

        close = ctx.bar.close

        # 标准海龟规则：今日收盘 > 前 N 日通道上轨（不含当日 high）
        prev_upper = ind.value("entry_upper", 1)
        prev_exit_lower = ind.value("exit_lower", 1)

        if prev_upper is not None and close > prev_upper and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / close)
            if qty > 0:
                ctx.buy(qty)

        elif ctx.qty > 0 and prev_exit_lower is not None and close < prev_exit_lower:
            ctx.sell_all()
