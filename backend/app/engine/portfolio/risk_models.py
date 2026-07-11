"""
风险模型：协方差估计器 (D1)

在样本协方差之外提供三种更稳健的年化协方差估计：
- Ledoit-Wolf 收缩（常方差目标）——降低估计误差、改善病态矩阵
- 指数加权协方差 —— 近期数据权重更高，捕捉体制变化
- 半协方差 —— 只统计低于基准的下行波动

所有估计器返回「年化 + PSD 修复」的协方差 DataFrame（symbol × symbol）。

参考签名（不复制实现）:
- refs/PyPortfolioOpt/pypfopt/risk_models.py
"""

from __future__ import annotations

from enum import Enum

import numpy as np
import pandas as pd
from sklearn.covariance import ledoit_wolf as _sk_ledoit_wolf

TRADING_DAYS = 252  # 与 optimizer 常量保持一致
DAILY_RISK_FREE = 0.000079  # ≈ 年化 2% 的日无风险收益，半协方差默认基准


class RiskModel(str, Enum):
    SAMPLE_COV = "sample_cov"        # 样本协方差（默认，向后兼容）
    LEDOIT_WOLF = "ledoit_wolf"      # Ledoit-Wolf 收缩，常方差目标
    EXP_COV = "exp_cov"              # 指数加权协方差
    SEMICOVARIANCE = "semicovariance"  # 下行半协方差


# ── 内部工具 ──────────────────────────────────────────────────

def _returns_from_prices(prices: pd.DataFrame, log_returns: bool) -> pd.DataFrame:
    """由价格 DataFrame 计算日收益率。"""
    if log_returns:
        return np.log(prices / prices.shift(1)).dropna(how="all")
    return prices.pct_change().dropna(how="all")


def _validate_prices(prices: pd.DataFrame) -> None:
    if not isinstance(prices, pd.DataFrame):
        raise ValueError("prices 必须是 DataFrame（index=日期, columns=symbol）")
    if prices.shape[1] < 2:
        raise ValueError("至少需要 2 个资产才能估计协方差矩阵")


# ── PSD 修复 ──────────────────────────────────────────────────

def fix_nonpositive_semidefinite(
    matrix: pd.DataFrame,
    fix_method: str = "spectral",
) -> pd.DataFrame:
    """
    修复非正半定协方差矩阵。

    - "spectral": 特征分解，将负特征值截断为 0 后重构。
    - "diag": 在对角线上加最小量，使最小特征值 ≥ 0。
    """
    if not isinstance(matrix, pd.DataFrame):
        raise ValueError("matrix 必须是 DataFrame")

    arr = matrix.to_numpy()
    # 已是 PSD 则直接返回（对称化以消除浮点噪声）
    min_eig = float(np.min(np.linalg.eigvalsh(arr)))
    if min_eig >= -1e-12:
        symmetric = (arr + arr.T) / 2
        return pd.DataFrame(symmetric, index=matrix.index, columns=matrix.columns)

    if fix_method == "spectral":
        eigvals, eigvecs = np.linalg.eigh(arr)
        eigvals = np.clip(eigvals, 0.0, None)
        fixed = eigvecs @ np.diag(eigvals) @ eigvecs.T
    elif fix_method == "diag":
        fixed = arr - 1.1 * min_eig * np.eye(arr.shape[0])
    else:
        raise ValueError(f"未知的 fix_method: {fix_method}（可选 'spectral' / 'diag'）")

    fixed = (fixed + fixed.T) / 2  # 强制对称
    return pd.DataFrame(fixed, index=matrix.index, columns=matrix.columns)


# ── 协方差估计器 ────────────────────────────────────────────────

def sample_cov(
    prices: pd.DataFrame,
    *,
    frequency: int = TRADING_DAYS,
    log_returns: bool = False,
) -> pd.DataFrame:
    """样本协方差（年化，未做 PSD 修复——由 risk_matrix 统一处理）。"""
    _validate_prices(prices)
    returns = _returns_from_prices(prices, log_returns)
    return returns.cov() * frequency


def exp_cov(
    prices: pd.DataFrame,
    *,
    span: int = 180,
    frequency: int = TRADING_DAYS,
    log_returns: bool = False,
) -> pd.DataFrame:
    """指数加权协方差（年化）。span 控制半衰速度，越小越偏重近期。"""
    _validate_prices(prices)
    returns = _returns_from_prices(prices, log_returns)
    symbols = list(returns.columns)

    # 显式指数加权（近期样本权重更大），避免 pandas ewm 语义歧义
    demeaned = returns - returns.mean()
    alpha = 2.0 / (span + 1.0)
    m = len(demeaned)
    # w_t ∝ (1-alpha)^(m-1-t)，最近的样本权重最大
    exponents = np.arange(m)[::-1]
    w = (1 - alpha) ** exponents
    w = w / w.sum()

    x = demeaned.to_numpy()
    weighted = x * w[:, None]
    cov = weighted.T @ x  # Σ w_t · x_t x_tᵀ
    cov_df = pd.DataFrame(cov, index=symbols, columns=symbols)
    return cov_df * frequency


def semicovariance(
    prices: pd.DataFrame,
    *,
    benchmark: float = DAILY_RISK_FREE,
    frequency: int = TRADING_DAYS,
    log_returns: bool = False,
) -> pd.DataFrame:
    """
    下行半协方差（年化）：只统计低于 benchmark 的收益共动，上行归零。

    E[min(r_i - B, 0) · min(r_j - B, 0)]
    """
    _validate_prices(prices)
    returns = _returns_from_prices(prices, log_returns)
    symbols = list(returns.columns)

    drops = np.minimum(returns.to_numpy() - benchmark, 0.0)
    m = drops.shape[0]
    semicov = (drops.T @ drops) / m
    cov_df = pd.DataFrame(semicov, index=symbols, columns=symbols)
    return cov_df * frequency


def ledoit_wolf_cov(
    prices: pd.DataFrame,
    *,
    frequency: int = TRADING_DAYS,
    log_returns: bool = False,
) -> pd.DataFrame:
    """
    常方差目标的 Ledoit-Wolf 收缩协方差（年化）。

    收缩强度 δ 由 sklearn.covariance.ledoit_wolf 解析估计。
    """
    _validate_prices(prices)
    returns = _returns_from_prices(prices, log_returns)
    symbols = list(returns.columns)

    x = returns.to_numpy()
    # sklearn 返回按 (1/n) 归一的收缩协方差与收缩系数
    shrunk, _delta = _sk_ledoit_wolf(x)
    cov_df = pd.DataFrame(shrunk, index=symbols, columns=symbols)
    return cov_df * frequency


# ── 主调度器 ──────────────────────────────────────────────────

_ESTIMATORS = {
    RiskModel.SAMPLE_COV: sample_cov,
    RiskModel.EXP_COV: exp_cov,
    RiskModel.SEMICOVARIANCE: semicovariance,
    RiskModel.LEDOIT_WOLF: ledoit_wolf_cov,
}


def risk_matrix(
    prices: pd.DataFrame,
    method: RiskModel | str = RiskModel.SAMPLE_COV,
    *,
    frequency: int = TRADING_DAYS,
    fix_method: str = "spectral",
    **kwargs,
) -> pd.DataFrame:
    """
    返回「年化 + PSD 修复」的协方差矩阵（symbol × symbol）。

    Args:
        prices: 宽格式价格 DataFrame（index=日期, columns=symbol，NaN 由调用方清理）
        method: 风险模型
        frequency: 年化系数（交易日数）
        fix_method: PSD 修复策略（'spectral' / 'diag'）
        **kwargs: 透传给具体估计器（如 exp_cov 的 span、semicovariance 的 benchmark）
    """
    _validate_prices(prices)

    try:
        model = RiskModel(method) if not isinstance(method, RiskModel) else method
    except ValueError:
        raise ValueError(f"未知的风险模型: {method}")

    estimator = _ESTIMATORS[model]
    cov = estimator(prices, frequency=frequency, **kwargs)
    return fix_nonpositive_semidefinite(cov, fix_method=fix_method)
