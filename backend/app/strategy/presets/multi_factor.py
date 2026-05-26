"""多因子策略（Multi-Factor）

综合动量、RSI、MACD 三个因子加权打分：
- 每个因子独立产生 -1/0/+1 信号
- 加权得分 > threshold 时买入，< -threshold 时卖出

参数:
  momentum_lookback  — 动量回望（默认 20）
  rsi_period         — RSI 周期（默认 14）
  rsi_low            — RSI 超卖线（默认 35）
  rsi_high           — RSI 超买线（默认 65）
  macd_fast          — MACD 快线（默认 12）
  macd_slow          — MACD 慢线（默认 26）
  macd_signal        — MACD 信号线（默认 9）
  threshold          — 进场综合得分阈值（默认 1，即至少 2 个因子一致）
  w_momentum / w_rsi / w_macd — 各因子权重（默认均为 1.0）
"""

from __future__ import annotations

from app.strategy.base import StrategyBase
from app.strategy.context import StrategyContext
from app.strategy.indicators import rsi, macd


class MultiFactorStrategy(StrategyBase):
    name = "multi_factor"
    description = "动量+RSI+MACD 多因子加权策略"

    def on_bar(self, ctx: StrategyContext) -> None:
        momentum_lookback = self.param("momentum_lookback", 20)
        rsi_period = self.param("rsi_period", 14)
        rsi_low = self.param("rsi_low", 35)
        rsi_high = self.param("rsi_high", 65)
        macd_fast = self.param("macd_fast", 12)
        macd_slow = self.param("macd_slow", 26)
        macd_signal = self.param("macd_signal", 9)
        threshold = self.param("threshold", 1.0)
        w_momentum = self.param("w_momentum", 1.0)
        w_rsi = self.param("w_rsi", 1.0)
        w_macd = self.param("w_macd", 1.0)

        min_bars = max(momentum_lookback, rsi_period + 1, macd_slow + macd_signal + 1)
        df = ctx.history
        if len(df) < min_bars + 1:
            return

        close = ctx.bar.close

        # 因子 1: 动量
        past_close = df["close"].iloc[-(momentum_lookback + 1)]
        momentum_ret = (close - past_close) / past_close
        signal_momentum = 1.0 if momentum_ret > 0.02 else (-1.0 if momentum_ret < -0.02 else 0.0)

        # 因子 2: RSI
        rsi_val = rsi(df, rsi_period).iloc[-1]
        if rsi_val != rsi_val:  # NaN
            signal_rsi = 0.0
        elif rsi_val < rsi_low:
            signal_rsi = 1.0
        elif rsi_val > rsi_high:
            signal_rsi = -1.0
        else:
            signal_rsi = 0.0

        # 因子 3: MACD
        macd_line, signal_line, _ = macd(df, macd_fast, macd_slow, macd_signal)
        diff = macd_line.iloc[-1] - signal_line.iloc[-1]
        prev_diff = macd_line.iloc[-2] - signal_line.iloc[-2]
        if diff > 0 and prev_diff <= 0:
            signal_macd = 1.0
        elif diff < 0 and prev_diff >= 0:
            signal_macd = -1.0
        else:
            signal_macd = 0.0

        score = w_momentum * signal_momentum + w_rsi * signal_rsi + w_macd * signal_macd

        if score >= threshold and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / close)
            if qty > 0:
                ctx.buy(qty)

        elif score <= -threshold and ctx.qty > 0:
            ctx.sell_all()
