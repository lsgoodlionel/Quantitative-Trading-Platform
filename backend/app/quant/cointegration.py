"""
协整检验与统计套利 (Cointegration & Statistical Arbitrage)

理论基础:
  Engle-Granger 两步法:
    1. 对 y_t = α + β·x_t + ε_t 做 OLS
    2. 对残差 ε_t 做 ADF 检验（若平稳则两序列协整）

  Johansen 多变量协整检验（迹统计量 / 最大特征值）

  价差 (spread): z_t = y_t - β*x_t - α
  Z-score: Z_t = (z_t - μ_z) / σ_z

  交易信号（配对交易）:
    Z_t > +threshold → 做空价差（卖 y 买 x）
    Z_t < -threshold → 做多价差（买 y 卖 x）
    |Z_t| < exit    → 平仓

应用场景:
  - 股票配对交易（如 AAPL vs MSFT）
  - ETF 套利（如 SPY vs IVV）
  - 跨市场套利（同一资产不同市场）
  - 期货-现货基差交易

配置参数:
  y           — 因变量价格序列
  x           — 自变量价格序列
  lookback    — 计算 Z-score 的滚动窗口（默认60天）
  entry_z     — 开仓 Z-score 阈值（默认2.0）
  exit_z      — 平仓 Z-score 阈值（默认0.5）
  use_log     — 是否对数处理（对数价格协整更稳定）
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class CointegrationResult:
    """协整检验与配对交易分析结果。"""
    # Engle-Granger 检验
    hedge_ratio: float       # OLS 估计的对冲比例 β
    intercept: float         # OLS 截距 α
    adf_stat: float          # ADF 检验统计量
    adf_pvalue: float        # ADF p-value（< 0.05 则协整）
    is_cointegrated: bool    # 是否通过协整检验

    # 价差统计
    spread_mean: float
    spread_std: float
    spread_last: float

    # 当前 Z-score 及信号
    z_score_last: float
    signal: str              # "BUY_SPREAD" / "SELL_SPREAD" / "EXIT" / "HOLD"

    # 时间序列（用于绘图）
    spread_series: list[float]
    z_score_series: list[float]

    # 统计信息
    n_observations: int
    correlation: float
    half_life_days: float    # 均值回归半衰期（AR(1) 估计）


def _compute_half_life(spread: np.ndarray) -> float:
    """通过 AR(1) 估计价差均值回归半衰期。"""
    lagged = spread[:-1]
    delta = np.diff(spread)
    # OLS: Δz_t = φ·z_{t-1} + ε
    beta = float(np.cov(lagged, delta)[0, 1] / np.var(lagged))
    if beta >= 0:
        return float("inf")  # 不均值回归
    return float(-np.log(2) / beta)


def analyze_cointegration(
    y: list[float],
    x: list[float],
    lookback: int = 60,
    entry_z: float = 2.0,
    exit_z: float = 0.5,
    use_log: bool = True,
) -> CointegrationResult:
    """
    对两个价格序列做协整检验并生成配对交易信号。

    参数说明:
        y        — 价格序列1（被解释变量），如 AAPL 收盘价列表
        x        — 价格序列2（解释变量），如 MSFT 收盘价列表
        lookback — 滚动窗口天数，用于计算当前 Z-score，默认60
        entry_z  — 开仓阈值，Z-score 绝对值超过此值触发信号，默认2.0
        exit_z   — 平仓阈值，Z-score 绝对值低于此值触发平仓，默认0.5
        use_log  — 使用对数价格，降低量纲影响，默认True

    抛出:
        ValueError — 序列长度不足或不等长
    """
    try:
        from statsmodels.tsa.stattools import adfuller  # type: ignore[import-untyped]
    except ImportError as e:
        raise RuntimeError("需要安装 statsmodels: pip install statsmodels") from e

    if len(y) != len(x):
        raise ValueError(f"序列长度不一致: y={len(y)}, x={len(x)}")
    if len(y) < lookback + 10:
        raise ValueError(f"序列长度不足（当前 {len(y)}，建议 >= {lookback + 10}）")

    ya = np.asarray(y, dtype=float)
    xa = np.asarray(x, dtype=float)

    if use_log:
        if np.any(ya <= 0) or np.any(xa <= 0):
            raise ValueError("use_log=True 要求价格序列所有元素 > 0。负数价格请设 use_log=False")
        ya = np.log(ya)
        xa = np.log(xa)

    n = len(ya)

    # Step 1: OLS 估计协整向量（全样本）
    X_mat = np.column_stack([xa, np.ones(n)])
    coeffs, _, _, _ = np.linalg.lstsq(X_mat, ya, rcond=None)
    beta, alpha = float(coeffs[0]), float(coeffs[1])

    # Step 2: 计算价差序列
    spread = ya - beta * xa - alpha

    # Step 3: ADF 检验
    adf_res = adfuller(spread, maxlag=1, autolag=None)
    adf_stat = float(adf_res[0])
    adf_pvalue = float(adf_res[1])
    is_cointegrated = adf_pvalue < 0.05

    # Step 4: 滚动 Z-score（使用近 lookback 天）
    roll_mean = pd.Series(spread).rolling(lookback).mean().to_numpy()
    roll_std = pd.Series(spread).rolling(lookback).std().to_numpy()
    z_scores = np.where(roll_std > 0, (spread - roll_mean) / roll_std, 0.0)

    # 信号判断（基于最新 Z-score）
    z_last = float(z_scores[-1])
    if abs(z_last) > entry_z:
        signal = "SELL_SPREAD" if z_last > 0 else "BUY_SPREAD"
    elif abs(z_last) < exit_z:
        signal = "EXIT"
    else:
        signal = "HOLD"

    # 半衰期
    half_life = _compute_half_life(spread)

    return CointegrationResult(
        hedge_ratio=beta,
        intercept=alpha,
        adf_stat=adf_stat,
        adf_pvalue=adf_pvalue,
        is_cointegrated=is_cointegrated,
        spread_mean=float(np.mean(spread)),
        spread_std=float(np.std(spread)),
        spread_last=float(spread[-1]),
        z_score_last=z_last,
        signal=signal,
        spread_series=spread.tolist(),
        z_score_series=z_scores.tolist(),
        n_observations=n,
        correlation=float(np.corrcoef(ya, xa)[0, 1]),
        half_life_days=half_life,
    )
