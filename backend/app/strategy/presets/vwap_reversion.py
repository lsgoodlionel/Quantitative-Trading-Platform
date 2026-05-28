"""VWAP 均值回归策略

价格偏离成交量加权均价（VWAP）超过阈值时逆向入场，
价格回归 VWAP 附近时平仓。
适合日内交易及震荡行情，VWAP 是机构重要参考价格。

参数:
  period          — 滚动 VWAP 计算周期（默认 20）
  dev_threshold   — 偏离触发阈值（默认 0.02 = 2%）；低于此比例不入场
"""

from __future__ import annotations

from app.strategy.base import StrategyBase
from app.strategy.context import StrategyContext
from app.strategy.indicators import vwap


class VwapReversionStrategy(StrategyBase):
    name = "vwap_reversion"
    description = "VWAP 均值回归策略（价格偏离均价后反向交易）"

    def on_bar(self, ctx: StrategyContext) -> None:
        period        = self.param("period",        20)
        dev_threshold = self.param("dev_threshold", 0.02)

        df = ctx.history
        if len(df) < period + 1:
            return

        vwap_val = vwap(df, period).iloc[-1]
        if vwap_val is None or vwap_val != vwap_val or vwap_val == 0:
            return

        close     = ctx.bar.close
        deviation = (close - vwap_val) / vwap_val

        # 价格低于 VWAP 超过阈值 → 买入（预期价格回升）
        if deviation < -dev_threshold and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / close)
            if qty > 0:
                ctx.buy(qty)

        # 价格回到 VWAP 上方 → 目标达到，平仓
        elif ctx.qty > 0 and deviation >= 0:
            ctx.sell_all()
