"""配对交易策略（Pairs Trading / Statistical Arbitrage）

正式配对交易需要两个协整标的同时运行。
本实现提供两种模式：

模式 A — 单标的 Z-score 模式（默认，无需第二标的）：
  计算价格相对自身长期均线的标准化偏离（Z-score），
  当价格显著低于历史均值时买入，回归后平仓。
  本质上是带统计意义的均值回归策略。

模式 B — 双标的模式（需通过 params 注入 symbol_b 当日价格）：
  以对数价差 log(A/B) 的 Z-score 作为套利信号，
  需同时传入配对标的每日收盘价（适合专业用户）。

参数:
  entry_z    — 开仓 Z-score 阈值（默认 2.0）；偏离越大越保守
  exit_z     — 平仓 Z-score 阈值（默认 0.5）；越小越快平仓
  lookback   — 统计窗口（默认 60）；越长基准越稳定
  hedge_ratio — 对冲比例（双标的模式，默认 1.0）

> **E-a 指标预算**（G-a 改造）：**只有模式 A 走预算**。模式 B 的价差依赖
> `on_bar` 里逐根累积的 `_price_b_history`（配对标的价格不在行情帧里），
> 它不是行情帧的函数，无法声明成指标 —— 那条路径一行未动。
>
> 模式 A 的窗口统计量用 `sliding_window_view` 一次算完。**不能用
> `close.rolling(n).mean()/.std()`**：pandas 的滚动实现是增量式的（加新值、
> 减旧值），与改造前「对尾窗切片整段求和」不是同一个浮点运算序列，实测
> 341 个窗口里 std 只有 2 个逐位相等、相对差到 1e-14。这个量级不会改变
> 任何一笔成交的概率极高，但「极高」不是「一致」；滑窗视图按整段
> pairwise 求和，与改造前**逐位相等**，对拍才成立。
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from app.strategy.base import IndicatorSpec, StrategyBase
from app.strategy.context import StrategyContext

#: 标准差的下限。低于它视作价格无波动，Z-score 没有意义
_MIN_STD = 1e-10


def close_window_stats(
    frame: pd.DataFrame, lookback: int
) -> tuple[pd.Series, pd.Series]:
    """
    收盘价尾窗的均值与样本标准差（ddof=1），逐位复刻改造前的
    `df["close"].iloc[-lookback:].mean() / .std()`。

    只回看，天然因果：位置 i 的窗口是 `[i - lookback + 1, i]`。
    前 `lookback - 1` 位没有完整窗口，与 pandas 一样留 NaN。

    与改造前的一点差别：pandas 的 `.mean()/.std()` 默认跳过 NaN，这里不跳。
    行情帧的 close 由 Bar 逐根构造，不含 NaN，两者在本项目的输入上等价；
    真出现缺口时这里给 NaN（=「不动作」）而不是拿残缺窗口硬算，更保守。
    """
    closes = frame["close"].to_numpy(dtype=float)
    n = len(closes)
    mean = np.full(n, np.nan)
    std = np.full(n, np.nan)
    # lookback < 2 时样本标准差无定义（ddof=1 分母为 0），保持全 NaN —— 与
    # pandas 对单元素 `.std()` 的结果一致，且不触发 numpy 的除零告警
    if lookback >= 2 and n >= lookback:
        windows = np.lib.stride_tricks.sliding_window_view(closes, lookback)
        mean[lookback - 1 :] = windows.mean(axis=1)
        std[lookback - 1 :] = windows.std(axis=1, ddof=1)
    return pd.Series(mean, index=frame.index), pd.Series(std, index=frame.index)


class PairsTradingStrategy(StrategyBase):
    name = "pairs_trading"
    description = "配对套利 — Z-score 均值回归（单/双标的）"

    def declare_indicators(self, spec: IndicatorSpec) -> None:
        spec.add_multi(
            ("close_mean", "close_std"),
            close_window_stats,
            self.param("lookback", 60),
        )

    def on_start(self, ctx: StrategyContext) -> None:
        self._price_b_history: list[float] = []

    def on_bar(self, ctx: StrategyContext) -> None:
        entry_z    = self.param("entry_z", 2.0)
        exit_z     = self.param("exit_z", 0.5)
        lookback   = self.param("lookback", 60)

        ind = ctx.ind
        # 热身守卫保持改造前口径（`len(df) < lookback`，注意没有 +1）。
        if ind.bars_seen < lookback:
            return

        # ── 模式 B：双标的（需 params 注入 price_b_today） ──────────
        price_b = self.param("price_b_today", None)
        if price_b is not None:
            self._on_bar_paired(ctx, float(price_b))
            return

        # ── 模式 A：单标的 — 价格 vs 长期窗口的 Z-score ──────────
        mean = ind.value("close_mean")
        std = ind.value("close_std")
        # 改造前 std 为 NaN 时 `std < 1e-10` 静默得 False，随后 z 也是 NaN，
        # 三个比较全 False —— 与这里直接返回同为「不动作」。
        if mean is None or std is None or std < _MIN_STD:
            return

        z = (ctx.bar.close - mean) / std
        self._trade_on_zscore(ctx, z, entry_z, exit_z)

    # ── 模式 B ───────────────────────────────────────────────────

    def _on_bar_paired(self, ctx: StrategyContext, price_b: float) -> None:
        """
        双标的价差 Z-score。配对标的价格由调用方逐根注入，不在行情帧里 ——
        因此这条路径**不走指标预算**，与改造前逐行相同。
        """
        entry_z     = self.param("entry_z", 2.0)
        exit_z      = self.param("exit_z", 0.5)
        lookback    = self.param("lookback", 60)
        hedge_ratio = self.param("hedge_ratio", 1.0)

        self._price_b_history.append(price_b)
        if len(self._price_b_history) < lookback:
            return

        df = ctx.history
        log_a = df["close"].iloc[-lookback:].apply(math.log)
        log_b = pd.Series(self._price_b_history[-lookback:]).apply(math.log)
        spread = log_a.values - hedge_ratio * log_b.values
        spread_s = pd.Series(spread)

        mean = spread_s.mean()
        std  = spread_s.std()
        if std < _MIN_STD:
            return

        z = (spread_s.iloc[-1] - mean) / std
        self._trade_on_zscore(ctx, z, entry_z, exit_z)

    # ── 两种模式共用的下单判定 ───────────────────────────────────

    @staticmethod
    def _trade_on_zscore(
        ctx: StrategyContext, z: float, entry_z: float, exit_z: float
    ) -> None:
        # 价格显著低于历史均值（统计意义上超卖）→ 买入
        if z < -entry_z and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / ctx.bar.close)
            if qty > 0:
                ctx.buy(qty)

        # 价格显著高于历史均值（统计意义上超买）→ 卖出
        elif z > entry_z and ctx.qty > 0:
            ctx.sell_all()

        # 价格回归均值区间 → 平仓
        elif abs(z) < exit_z and ctx.qty > 0:
            ctx.sell_all()
