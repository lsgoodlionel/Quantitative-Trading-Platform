"""RSI 均值回归策略

RSI 超卖买入，超买卖出。
参数:
  period      — RSI 周期（默认 14）
  oversold    — 超卖阈值（默认 30）
  overbought  — 超买阈值（默认 70）

> **E-a 指标预算**：RSI 在 `declare_indicators` 里声明，框架一次算完，
> `on_bar` 只按游标取值。成交与改造前**逐笔一致**（见 `tests/test_preset_parity.py`）。
"""

from __future__ import annotations

from app.strategy.base import IndicatorSpec, StrategyBase
from app.strategy.context import StrategyContext
from app.strategy.indicators import rsi


class RsiMeanReversionStrategy(StrategyBase):
    name = "rsi_mean_reversion"
    description = "RSI 超卖买入、超买卖出均值回归"

    def declare_indicators(self, spec: IndicatorSpec) -> None:
        spec.add("rsi", rsi, self.param("period", 14))

    def on_bar(self, ctx: StrategyContext) -> None:
        period = self.param("period", 14)
        oversold = self.param("oversold", 30)
        overbought = self.param("overbought", 70)

        ind = ctx.ind
        # 热身守卫保持改造前口径（`len(df) < period + 2`）。
        if ind.bars_seen < period + 2:
            return

        # value() 在热身期返回 None；改造前是 NaN 检查，两者在这里等价 ——
        # 但 None 比 NaN 好：NaN 参与比较会静默得 False，
        # 把「还没数据」和「条件不成立」混为一谈。
        rsi_val = ind.value("rsi")
        if rsi_val is None:
            return

        if rsi_val <= oversold and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / ctx.bar.close)
            if qty > 0:
                ctx.buy(qty)

        elif rsi_val >= overbought and ctx.qty > 0:
            ctx.sell_all()
