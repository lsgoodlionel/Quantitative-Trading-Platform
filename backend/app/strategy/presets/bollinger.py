"""布林带均值回归策略（Bollinger Bands Mean Reversion）

价格触及下轨买入，触及上轨或中轨平仓。
参数:
  period   — 布林带周期（默认 20）
  std_dev  — 标准差倍数（默认 2.0）

> **E-a 指标预算**：三条轨道一次算完。注意本策略的热身期守卫
> （`len(df) < period + 1`）**不是冗余的** —— 第 `period-1` 根 bar 上轨道已经
> 有值，守卫把它跳过了。改造必须原样保留这个口径，否则会多出一笔成交。
"""

from __future__ import annotations

from app.strategy.base import IndicatorSpec, StrategyBase
from app.strategy.context import StrategyContext
from app.strategy.indicators import bollinger_bands


class BollingerStrategy(StrategyBase):
    name = "bollinger"
    description = "布林带均值回归策略"

    def declare_indicators(self, spec: IndicatorSpec) -> None:
        spec.add_multi(
            ("upper", "mid", "lower"),
            bollinger_bands,
            self.param("period", 20),
            self.param("std_dev", 2.0),
        )

    def on_bar(self, ctx: StrategyContext) -> None:
        period = self.param("period", 20)

        ind = ctx.ind
        if ind.bars_seen < period + 1:
            return

        close = ctx.bar.close
        # 轨道未就绪时 `value()` 返回 None。改造前这里是 NaN 参与比较、静默得到
        # False —— 显式判 None 是同一个结果，但把「还没数据」写在了明面上。
        upper = ind.value("upper")
        mid = ind.value("mid")
        lower = ind.value("lower")

        if lower is not None and close <= lower and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / close)
            if qty > 0:
                ctx.buy(qty)

        elif ctx.qty > 0 and (
            (mid is not None and close >= mid) or (upper is not None and close >= upper)
        ):
            ctx.sell_all()
