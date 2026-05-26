"""
组合优化器

基于 PyPortfolioOpt 提供三种优化模式：
1. max_sharpe     — 最大夏普比率（默认）
2. min_volatility — 最小方差组合
3. equal_weight   — 等权重（不依赖收益率估计，最简单）
4. risk_parity    — 风险平价

参考: refs/PyPortfolioOpt/pypfopt/  — efficient_frontier, risk_models, expected_returns
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

OptimizeMode = Literal["max_sharpe", "min_volatility", "equal_weight", "risk_parity"]


@dataclass(frozen=True)
class PortfolioWeights:
    """优化结果：各标的目标权重。"""
    weights: dict[str, float]          # symbol → 权重（0~1，合计≤1）
    mode: str
    expected_return: Optional[float]   # 年化预期收益率
    expected_volatility: Optional[float]
    sharpe_ratio: Optional[float]

    def to_dict(self) -> dict:
        return {
            "weights": {k: round(v, 6) for k, v in self.weights.items()},
            "mode": self.mode,
            "expected_return": round(self.expected_return, 6) if self.expected_return else None,
            "expected_volatility": round(self.expected_volatility, 6) if self.expected_volatility else None,
            "sharpe_ratio": round(self.sharpe_ratio, 4) if self.sharpe_ratio else None,
        }


@dataclass(frozen=True)
class RebalanceOrder:
    """单个标的的再平衡指令。"""
    symbol: str
    current_weight: float
    target_weight: float
    delta_weight: float      # 正=买入，负=卖出
    delta_value: float       # 需要交易的金额（正=买，负=卖）


def optimize_portfolio(
    prices: pd.DataFrame,
    mode: OptimizeMode = "max_sharpe",
    risk_free_rate: float = 0.04,
    weight_bounds: tuple[float, float] = (0.0, 0.4),
) -> PortfolioWeights:
    """
    给定历史价格 DataFrame（列=symbol，行=日期），
    返回最优组合权重。

    prices: pd.DataFrame，index=datetime，columns=symbol，values=收盘价
    weight_bounds: (min_weight, max_weight) 各标的权重区间
    """
    if prices.empty or len(prices.columns) < 2:
        raise ValueError("Need at least 2 symbols with price history")

    if len(prices) < 30:
        raise ValueError("Need at least 30 days of price history for optimization")

    if mode == "equal_weight":
        return _equal_weight(prices)

    if mode == "risk_parity":
        return _risk_parity(prices)

    return _mean_variance(prices, mode, risk_free_rate, weight_bounds)


def _mean_variance(
    prices: pd.DataFrame,
    mode: OptimizeMode,
    risk_free_rate: float,
    weight_bounds: tuple[float, float],
) -> PortfolioWeights:
    """均值-方差优化（最大夏普 / 最小波动）。"""
    try:
        from pypfopt import expected_returns, risk_models, EfficientFrontier
    except ImportError as e:
        raise RuntimeError(
            "PyPortfolioOpt not installed. Run: pip install pyportfolioopt"
        ) from e

    mu = expected_returns.mean_historical_return(prices)
    S = risk_models.sample_cov(prices)

    ef = EfficientFrontier(mu, S, weight_bounds=weight_bounds)

    if mode == "max_sharpe":
        ef.max_sharpe(risk_free_rate=risk_free_rate)
    elif mode == "min_volatility":
        ef.min_volatility()
    else:
        ef.max_sharpe(risk_free_rate=risk_free_rate)

    cleaned_weights = ef.clean_weights()
    perf = ef.portfolio_performance(verbose=False, risk_free_rate=risk_free_rate)

    return PortfolioWeights(
        weights={str(k): float(v) for k, v in cleaned_weights.items()},
        mode=mode,
        expected_return=float(perf[0]),
        expected_volatility=float(perf[1]),
        sharpe_ratio=float(perf[2]),
    )


def _equal_weight(prices: pd.DataFrame) -> PortfolioWeights:
    n = len(prices.columns)
    w = 1.0 / n
    weights = {str(col): w for col in prices.columns}

    returns = prices.pct_change().dropna()
    eq_returns = returns.mean(axis=1)
    ann_return = float((1 + eq_returns.mean()) ** 252 - 1)
    ann_vol = float(eq_returns.std() * np.sqrt(252))
    sharpe = ann_return / ann_vol if ann_vol > 0 else 0.0

    return PortfolioWeights(
        weights=weights,
        mode="equal_weight",
        expected_return=ann_return,
        expected_volatility=ann_vol,
        sharpe_ratio=sharpe,
    )


def _risk_parity(prices: pd.DataFrame) -> PortfolioWeights:
    """
    风险平价：各标的对组合风险的贡献相等。
    使用逆波动率近似（简化实现，不依赖优化器）。
    """
    returns = prices.pct_change().dropna()
    vols = returns.std() * np.sqrt(252)

    inv_vol = 1.0 / vols
    weights_raw = inv_vol / inv_vol.sum()
    weights = {str(k): float(v) for k, v in weights_raw.items()}

    port_returns = (returns * weights_raw).sum(axis=1)
    ann_return = float((1 + port_returns.mean()) ** 252 - 1)
    ann_vol = float(port_returns.std() * np.sqrt(252))
    sharpe = ann_return / ann_vol if ann_vol > 0 else 0.0

    return PortfolioWeights(
        weights=weights,
        mode="risk_parity",
        expected_return=ann_return,
        expected_volatility=ann_vol,
        sharpe_ratio=sharpe,
    )


def compute_rebalance(
    current_positions: dict[str, float],  # symbol → current market value
    target_weights: dict[str, float],      # symbol → target weight (0~1)
    portfolio_value: float,
    min_trade_value: float = 500.0,        # 低于此金额的调整忽略（减少交易摩擦）
) -> list[RebalanceOrder]:
    """
    给定当前持仓市值和目标权重，计算再平衡指令列表。

    返回所有需要交易的标的（按 |delta_value| 降序排列）。
    """
    if portfolio_value <= 0:
        return []

    all_symbols = set(current_positions) | set(target_weights)
    orders: list[RebalanceOrder] = []

    for symbol in all_symbols:
        current_value = current_positions.get(symbol, 0.0)
        current_weight = current_value / portfolio_value
        target_weight = target_weights.get(symbol, 0.0)
        delta_weight = target_weight - current_weight
        delta_value = delta_weight * portfolio_value

        if abs(delta_value) < min_trade_value:
            continue

        orders.append(RebalanceOrder(
            symbol=symbol,
            current_weight=round(current_weight, 6),
            target_weight=round(target_weight, 6),
            delta_weight=round(delta_weight, 6),
            delta_value=round(delta_value, 2),
        ))

    # 先卖后买（确保有足够现金）
    orders.sort(key=lambda o: o.delta_value)
    return orders
