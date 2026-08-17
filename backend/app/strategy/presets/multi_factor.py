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

> **E-a 指标预算**（G-a 改造）：三个因子的原料（动量 / RSI / MACD）都进预算。
> 本策略只依赖 `app/strategy/indicators.py`，与 `app/quant` 的因子库无关。
> 动量复用 `presets/momentum.py` 的 `momentum_series`，两处口径必须同一份代码 ——
> 那里的 `shift(lookback)` 正是改造前 `iloc[-(lookback + 1)]` 在序列上的写法。
"""

from __future__ import annotations

from app.strategy.base import IndicatorSpec, StrategyBase
from app.strategy.context import StrategyContext
from app.strategy.indicators import macd, rsi
from app.strategy.precompute import IndicatorView
from app.strategy.presets.momentum import momentum_series


class MultiFactorStrategy(StrategyBase):
    name = "multi_factor"
    description = "动量+RSI+MACD 多因子加权策略"

    def declare_indicators(self, spec: IndicatorSpec) -> None:
        spec.add("momentum", momentum_series, self.param("momentum_lookback", 20))
        spec.add("rsi", rsi, self.param("rsi_period", 14))
        spec.add_multi(
            ("macd_line", "signal_line", "histogram"),
            macd,
            self.param("macd_fast", 12),
            self.param("macd_slow", 26),
            self.param("macd_signal", 9),
        )

    def on_bar(self, ctx: StrategyContext) -> None:
        momentum_lookback = self.param("momentum_lookback", 20)
        rsi_period = self.param("rsi_period", 14)
        rsi_low = self.param("rsi_low", 35)
        rsi_high = self.param("rsi_high", 65)
        macd_slow = self.param("macd_slow", 26)
        macd_signal = self.param("macd_signal", 9)
        threshold = self.param("threshold", 1.0)
        w_momentum = self.param("w_momentum", 1.0)
        w_rsi = self.param("w_rsi", 1.0)
        w_macd = self.param("w_macd", 1.0)

        ind = ctx.ind
        # 热身守卫保持改造前口径（`len(df) < min_bars + 1`）。
        min_bars = max(momentum_lookback, rsi_period + 1, macd_slow + macd_signal + 1)
        if ind.bars_seen < min_bars + 1:
            return

        close = ctx.bar.close

        # 因子 1: 动量。改造前动量为 NaN 时两个比较都是 False，落到 0.0。
        momentum_ret = ind.value("momentum")
        if momentum_ret is None:
            signal_momentum = 0.0
        elif momentum_ret > 0.02:
            signal_momentum = 1.0
        elif momentum_ret < -0.02:
            signal_momentum = -1.0
        else:
            signal_momentum = 0.0

        # 因子 2: RSI
        rsi_val = ind.value("rsi")
        if rsi_val is None:
            signal_rsi = 0.0
        elif rsi_val < rsi_low:
            signal_rsi = 1.0
        elif rsi_val > rsi_high:
            signal_rsi = -1.0
        else:
            signal_rsi = 0.0

        # 因子 3: MACD 快慢差的符号翻转
        signal_macd = self._macd_signal(ind)

        score = w_momentum * signal_momentum + w_rsi * signal_rsi + w_macd * signal_macd

        if score >= threshold and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / close)
            if qty > 0:
                ctx.buy(qty)

        elif score <= -threshold and ctx.qty > 0:
            ctx.sell_all()

    @staticmethod
    def _macd_signal(ind: IndicatorView) -> float:
        """MACD 因子：diff 由负转正记 +1，由正转负记 -1，其余 0。"""
        macd_now = ind.value("macd_line")
        signal_now = ind.value("signal_line")
        macd_prev = ind.value("macd_line", 1)
        signal_prev = ind.value("signal_line", 1)
        # EMA 从第 0 根起就有值，这里实际取不到 None；保留是因为改造前 NaN
        # 参与比较同样落到 0.0，不想让等价性依赖「恰好取不到」这个巧合。
        if None in (macd_now, signal_now, macd_prev, signal_prev):
            return 0.0

        diff = macd_now - signal_now
        prev_diff = macd_prev - signal_prev
        if diff > 0 and prev_diff <= 0:
            return 1.0
        if diff < 0 and prev_diff >= 0:
            return -1.0
        return 0.0
