"""OptimizerPCM — 把现有组合优化器接进三段式（Wave K-d / K5）

桥接 `app/engine/portfolio/optimizer.py` 已有的 8 种方法（max_sharpe / min_vol /
risk_parity / min_cvar / equal_weight / HRP / Black-Litterman / min_cdar）。

这是 V3 记录的断链「组合优化算出权重后只能手抄下单」的接口：
优化器给权重 → `PortfolioTarget` 给目标股数 → `ExecutionModel` 给增量订单。

优化器本身有硬门槛（≥2 个资产、≥60 个交易日）。达不到时**退化为等权**并告警，
而不是抛错中断整场回测 —— 回测早期历史不足是必然会发生的正常状态。
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

import pandas as pd

from app.engine.framework.insight import Insight, InsightDirection
from app.engine.framework.portfolio_construction import PortfolioConstructionModel
from app.engine.portfolio.optimizer import OptimizeMethod, optimize_portfolio

if TYPE_CHECKING:
    from app.strategy.context import PortfolioContext

logger = logging.getLogger(__name__)

#: 优化器要求的最少历史长度（与 optimize_portfolio 的校验一致）
MIN_HISTORY = 60
#: 优化器要求的最少资产数
MIN_ASSETS = 2
#: 默认只取最近这么多根 bar 参与估计，避免历史越长越慢
DEFAULT_LOOKBACK = 252


class OptimizerPCM(PortfolioConstructionModel):
    """用组合优化器求权重的 PCM。"""

    def __init__(
        self,
        method: OptimizeMethod = OptimizeMethod.HRP,
        *,
        rebalance_period: timedelta | None = None,
        lookback: int = DEFAULT_LOOKBACK,
        **optimizer_kwargs,
    ) -> None:
        super().__init__(rebalance_period=rebalance_period)
        if lookback < MIN_HISTORY:
            raise ValueError(f"lookback 至少 {MIN_HISTORY}，收到 {lookback}")
        self._method = method
        self._lookback = lookback
        self._kwargs = optimizer_kwargs

    def compute_weights(
        self, ctx: PortfolioContext, insights: list[Insight]
    ) -> dict[str, float]:
        directions = {
            i.symbol: int(i.direction)
            for i in insights
            if i.direction is not InsightDirection.FLAT
        }
        flats = {i.symbol: 0.0 for i in insights if i.direction is InsightDirection.FLAT}
        if not directions:
            return flats

        prices = self._price_frame(ctx, sorted(directions))
        weights = self._optimize(prices) if prices is not None else None
        if weights is None:
            weights = dict.fromkeys(directions, 1.0 / len(directions))

        return flats | {s: directions[s] * w for s, w in weights.items()}

    # ── 内部 ─────────────────────────────────────────────────

    def _price_frame(
        self, ctx: PortfolioContext, symbols: list[str]
    ) -> pd.DataFrame | None:
        """各标的收盘价对齐成一张表；数据不足以优化时返回 None。"""
        if len(symbols) < MIN_ASSETS:
            return None
        closes = {}
        for symbol in symbols:
            history = ctx.histories[symbol]
            if len(history) < MIN_HISTORY:
                return None
            closes[symbol] = history["close"].tail(self._lookback)
        frame = pd.DataFrame(closes).dropna()
        return frame if len(frame) >= MIN_HISTORY else None

    def _optimize(self, prices: pd.DataFrame) -> dict[str, float] | None:
        try:
            result = optimize_portfolio(
                prices, method=self._method, include_frontier=False, **self._kwargs
            )
        except (ValueError, KeyError, ZeroDivisionError) as exc:
            logger.warning("组合优化失败（%s），本轮退化为等权: %s", self._method, exc)
            return None
        return _renormalized(result.weights)


def _renormalized(weights: dict[str, float]) -> dict[str, float] | None:
    """
    把优化器权重的绝对值之和归一到 1。

    `optimize_portfolio` 为了给 UI 展示会对权重做四舍五入，几个标的加起来常见
    1.0001 这类零头。做交易时这点漂移会直接变成超配/欠配，必须在这里抹平。
    """
    total = sum(abs(w) for w in weights.values())
    if total <= 0:
        return None
    return {symbol: w / total for symbol, w in weights.items()}


__all__ = ["OptimizerPCM"]
