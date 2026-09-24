"""凯尔特纳通道突破策略（Keltner Channel Breakout）

价格突破上轨买入，回落至中轨平仓。
凯尔特纳通道用 ATR 替代标准差，通道更平滑稳定，
假突破率低于布林带，适合追踪中期趋势。

参数:
  ema_period   — 中轨 EMA 周期（默认 20）
  atr_period   — ATR 计算周期（默认 10）
  multiplier   — ATR 倍数（默认 2.0）；越大通道越宽

> **E-a 指标预算**（G-a 改造）：三条轨道一次算完。入场判定还要用到**上一根**
> 收盘价，所以收盘价本身也进了预算 —— 这样 `on_bar` 里一次 DataFrame 取列 +
> 定位都不需要，取值全部走数组下标。
"""

from __future__ import annotations

import pandas as pd

from app.strategy.base import IndicatorSpec, StrategyBase
from app.strategy.context import StrategyContext
from app.strategy.indicators import keltner_channels


def close_series(frame: pd.DataFrame) -> pd.Series:
    """收盘价原样进预算。恒等映射，天然因果。"""
    return frame["close"]


class KeltnerBreakoutStrategy(StrategyBase):
    name = "keltner_breakout"
    description = "凯尔特纳通道突破策略（ATR 自适应通道）"

    def declare_indicators(self, spec: IndicatorSpec) -> None:
        spec.add_multi(
            ("upper", "mid", "lower"),
            keltner_channels,
            self.param("ema_period", 20),
            self.param("atr_period", 10),
            self.param("multiplier", 2.0),
        )
        spec.add("close", close_series)

    def on_bar(self, ctx: StrategyContext) -> None:
        ema_period = self.param("ema_period", 20)
        atr_period = self.param("atr_period", 10)

        ind = ctx.ind
        # 热身守卫保持改造前口径（`len(df) < ema_period + atr_period + 2`）。
        if ind.bars_seen < ema_period + atr_period + 2:
            return

        close      = ctx.bar.close
        prev_close = ind.value("close", 1)
        prev_upper = ind.value("upper", 1)
        upper      = ind.value("upper")
        mid        = ind.value("mid")

        # 价格突破上轨 → 趋势确认，买入
        # 改造前任一端为 NaN 时比较静默得 False，落到 elif；显式判 None 同义。
        if (
            prev_close is not None
            and prev_upper is not None
            and upper is not None
            and prev_close < prev_upper
            and close >= upper
            and ctx.qty == 0
        ):
            qty = int(ctx.cash * 0.95 / close)
            if qty > 0:
                ctx.buy(qty)

        # 价格回落到中轨以下 → 趋势减弱，卖出
        elif ctx.qty > 0 and mid is not None and close < mid:
            ctx.sell_all()
