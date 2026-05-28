"""凯尔特纳通道突破策略（Keltner Channel Breakout）

价格突破上轨买入，回落至中轨平仓。
凯尔特纳通道用 ATR 替代标准差，通道更平滑稳定，
假突破率低于布林带，适合追踪中期趋势。

参数:
  ema_period   — 中轨 EMA 周期（默认 20）
  atr_period   — ATR 计算周期（默认 10）
  multiplier   — ATR 倍数（默认 2.0）；越大通道越宽
"""

from __future__ import annotations

from app.strategy.base import StrategyBase
from app.strategy.context import StrategyContext
from app.strategy.indicators import keltner_channels


class KeltnerBreakoutStrategy(StrategyBase):
    name = "keltner_breakout"
    description = "凯尔特纳通道突破策略（ATR 自适应通道）"

    def on_bar(self, ctx: StrategyContext) -> None:
        ema_period = self.param("ema_period", 20)
        atr_period = self.param("atr_period", 10)
        multiplier = self.param("multiplier", 2.0)

        df = ctx.history
        if len(df) < ema_period + atr_period + 2:
            return

        upper, mid, _ = keltner_channels(df, ema_period, atr_period, multiplier)
        close      = ctx.bar.close
        prev_close = df["close"].iloc[-2]

        # 价格突破上轨 → 趋势确认，买入
        if prev_close < upper.iloc[-2] and close >= upper.iloc[-1] and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / close)
            if qty > 0:
                ctx.buy(qty)

        # 价格回落到中轨以下 → 趋势减弱，卖出
        elif ctx.qty > 0 and close < mid.iloc[-1]:
            ctx.sell_all()
