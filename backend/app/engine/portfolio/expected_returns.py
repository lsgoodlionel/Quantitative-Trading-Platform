"""
预期收益估计器 (D1)

在历史均值之外提供两种可选估计：
- 指数加权历史收益 —— 趋势倾斜，近期表现权重更高
- CAPM 隐含收益 —— rf + β·(市场超额收益)，规避噪声均值

所有估计器返回年化预期收益 Series（index=symbol，分数形式，如 0.18 = 18%）。

参考签名（不复制实现）:
- refs/PyPortfolioOpt/pypfopt/expected_returns.py
"""

from __future__ import annotations

from enum import Enum

import numpy as np
import pandas as pd

TRADING_DAYS = 252
RISK_FREE_RATE = 0.05  # 与 optimizer 常量保持一致


class ReturnsModel(str, Enum):
    MEAN_HISTORICAL = "mean_historical"  # 历史均值 / CAGR（默认，当前行为）
    EMA_HISTORICAL = "ema_historical"    # 指数加权均值
    CAPM = "capm"                        # CAPM 隐含收益


# ── 内部工具 ──────────────────────────────────────────────────

def _returns_from_prices(prices: pd.DataFrame, log_returns: bool) -> pd.DataFrame:
    if log_returns:
        returns = np.log(prices / prices.shift(1)).dropna(how="all")
    else:
        returns = prices.pct_change().dropna(how="all")
    if returns.shape[0] < 2:
        raise ValueError(
            f"价格数据不足以估计预期收益：仅 {returns.shape[0]} 行收益（需 ≥ 2）"
        )
    return returns


def _validate_prices(prices: pd.DataFrame) -> None:
    if not isinstance(prices, pd.DataFrame):
        raise ValueError("prices 必须是 DataFrame（index=日期, columns=symbol）")
    if prices.shape[1] < 1:
        raise ValueError("至少需要 1 个资产才能估计预期收益")


# ── 估计器 ────────────────────────────────────────────────────

def mean_historical_return(
    prices: pd.DataFrame,
    *,
    compounding: bool = True,
    frequency: int = TRADING_DAYS,
    log_returns: bool = False,
) -> pd.Series:
    """历史均值预期收益（年化）。compounding=True 用 CAGR，否则算术均值。"""
    _validate_prices(prices)
    returns = _returns_from_prices(prices, log_returns)
    if compounding:
        # (1 + r).prod() ^ (frequency / n) - 1
        total_growth = (1 + returns).prod()
        return total_growth ** (frequency / returns.shape[0]) - 1
    return returns.mean() * frequency


def ema_historical_return(
    prices: pd.DataFrame,
    *,
    compounding: bool = True,
    span: int = 500,
    frequency: int = TRADING_DAYS,
    log_returns: bool = False,
) -> pd.Series:
    """指数加权历史收益（年化）。近期收益权重更高。"""
    _validate_prices(prices)
    returns = _returns_from_prices(prices, log_returns)
    ema_daily = returns.ewm(span=span).mean().iloc[-1]
    if compounding:
        return (1 + ema_daily) ** frequency - 1
    return ema_daily * frequency


def capm_return(
    prices: pd.DataFrame,
    *,
    market_prices: pd.DataFrame | None = None,
    risk_free_rate: float = RISK_FREE_RATE,
    compounding: bool = True,
    frequency: int = TRADING_DAYS,
    log_returns: bool = False,
) -> pd.Series:
    """
    CAPM 隐含收益：rf + β·(市场超额收益)。

    β = Cov(asset, market) / Var(market)。若未提供 market_prices，
    以输入资产的等权组合作为市场代理。
    """
    _validate_prices(prices)
    returns = _returns_from_prices(prices, log_returns)
    symbols = list(returns.columns)

    if market_prices is not None:
        market_returns = _returns_from_prices(market_prices, log_returns)
        # 取首列作为市场收益
        market_col = market_returns.columns[0]
        market_series = market_returns[market_col]
        # 对齐日期
        combined = returns.join(market_series.rename("__mkt__"), how="inner")
        asset_ret = combined[symbols]
        market_ret = combined["__mkt__"]
    else:
        # 等权组合代理市场
        asset_ret = returns
        market_ret = returns.mean(axis=1)

    market_var = float(market_ret.var())
    if market_var < 1e-18:
        # 市场无波动时退化为无风险利率
        return pd.Series(risk_free_rate, index=symbols)

    # β_i = Cov(asset_i, market) / Var(market)
    betas = asset_ret.apply(lambda col: col.cov(market_ret) / market_var)

    # 年化市场收益
    if compounding:
        market_annual = float((1 + market_ret).prod() ** (frequency / len(market_ret)) - 1)
    else:
        market_annual = float(market_ret.mean() * frequency)

    implied = risk_free_rate + betas * (market_annual - risk_free_rate)
    return implied.reindex(symbols)


# ── 主调度器 ──────────────────────────────────────────────────

_ESTIMATORS = {
    ReturnsModel.MEAN_HISTORICAL: mean_historical_return,
    ReturnsModel.EMA_HISTORICAL: ema_historical_return,
    ReturnsModel.CAPM: capm_return,
}


def expected_returns(
    prices: pd.DataFrame,
    method: ReturnsModel | str = ReturnsModel.MEAN_HISTORICAL,
    *,
    frequency: int = TRADING_DAYS,
    **kwargs,
) -> pd.Series:
    """
    返回年化预期收益 Series（index=symbol）。

    Args:
        prices: 宽格式价格 DataFrame（index=日期, columns=symbol）
        method: 预期收益估计方法
        frequency: 年化系数
        **kwargs: 透传给具体估计器（如 ema 的 span、capm 的 risk_free_rate）
    """
    _validate_prices(prices)

    try:
        model = ReturnsModel(method) if not isinstance(method, ReturnsModel) else method
    except ValueError:
        raise ValueError(f"未知的预期收益方法: {method}")

    estimator = _ESTIMATORS[model]
    return estimator(prices, frequency=frequency, **kwargs)
