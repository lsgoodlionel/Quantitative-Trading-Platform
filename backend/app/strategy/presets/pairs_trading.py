"""配对交易策略（Pairs Trading / Statistical Arbitrage）

利用两个相关标的价差均值回归特性：
- 价差 = log(price_A) - hedge_ratio * log(price_B)
- 价差偏离均值 > entry_z 个标准差时做空价差（卖 A 买 B）
- 价差偏离均值 < -entry_z 个标准差时做多价差（买 A 卖 B）
- 价差回归均值时平仓

注意：单标的回测引擎中，配对交易通过 symbol / symbol_b 两个字段
在同一个 StrategyContext 中管理（仅适合同时包含两只股票 bar 的回测）。
本实现简化为：以主标的和参考标的的相对强弱为信号。

参数:
  symbol_b      — 配对标的 B 的代码（必须）
  entry_z       — 入场 Z-score 阈值（默认 2.0）
  exit_z        — 出场 Z-score 阈值（默认 0.5）
  lookback      — 价差统计窗口（默认 60）
  hedge_ratio   — 对冲比例（默认 1.0）
"""

from __future__ import annotations

import math

import pandas as pd

from app.strategy.base import StrategyBase
from app.strategy.context import StrategyContext


class PairsTradingStrategy(StrategyBase):
    name = "pairs_trading"
    description = "配对交易统计套利策略"

    def on_start(self, ctx: StrategyContext) -> None:
        self._price_b_history: list[float] = []

    def on_bar(self, ctx: StrategyContext) -> None:
        entry_z = self.param("entry_z", 2.0)
        exit_z = self.param("exit_z", 0.5)
        lookback = self.param("lookback", 60)
        hedge_ratio = self.param("hedge_ratio", 1.0)

        # 标的 B 价格需从 bar 的 vwap 或外部注入；
        # 简化实现：直接从 params 读取当日价格列表（适合测试）
        price_b = self.param("price_b_today", None)
        if price_b is None:
            return

        self._price_b_history.append(float(price_b))
        df = ctx.history
        if len(df) < lookback or len(self._price_b_history) < lookback:
            return

        log_a = df["close"].iloc[-lookback:].apply(math.log)
        log_b = pd.Series(self._price_b_history[-lookback:]).apply(math.log)
        spread = log_a.values - hedge_ratio * log_b.values
        spread_series = pd.Series(spread)

        mean = spread_series.mean()
        std = spread_series.std()
        if std < 1e-10:
            return

        z_score = (spread_series.iloc[-1] - mean) / std

        if z_score > entry_z and ctx.qty == 0:
            # 价差过高 → 做空价差，即卖主标的（简化为持有现金等待）
            # 真实配对交易需双腿同时操作；此处仅示意
            ctx.sell_all()  # 卖出已有多头

        elif z_score < -entry_z and ctx.qty == 0:
            qty = int(ctx.cash * 0.95 / ctx.bar.close)
            if qty > 0:
                ctx.buy(qty)

        elif abs(z_score) < exit_z and ctx.qty > 0:
            ctx.sell_all()
