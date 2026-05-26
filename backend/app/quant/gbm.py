"""
几何布朗运动 (Geometric Brownian Motion) Monte Carlo 模拟

理论基础:
  dS = μS dt + σS dW_t
  离散化: S_{t+1} = S_t · exp[(μ - σ²/2)·Δt + σ·√Δt·Z]
  其中 Z ~ N(0,1)

应用场景:
  - 期权 Monte Carlo 定价
  - VaR / CVaR 计算
  - 压力测试与情景分析
  - 期末价格分布估计

配置参数:
  S0     — 当前价格（起始点）
  mu     — 年化漂移率（预期收益）
  sigma  — 年化波动率（隐含或历史）
  T      — 时间跨度（年，如 0.25=3个月）
  n_paths — 模拟路径数（越多越精确，典型1000-10000）
  n_steps — 每条路径的时间步数
  seed   — 随机种子（-1表示不固定）
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class GBMResult:
    """GBM Monte Carlo 模拟结果。"""
    S0: float
    mu: float
    sigma: float
    T: float
    n_paths: int
    n_steps: int

    # 路径矩阵 shape=(n_paths, n_steps)，用于绘图（最多取100条）
    sample_paths: list[list[float]]

    # 期末价格分布统计
    final_mean: float
    final_std: float
    final_median: float
    final_p5: float       # 5th percentile（下行风险）
    final_p95: float      # 95th percentile

    # 风险指标
    var_95: float         # 95% Value-at-Risk（绝对损失）
    cvar_95: float        # 95% Conditional VaR（期望缺口）
    prob_loss: float      # 亏损概率 P(S_T < S_0)
    expected_return: float  # 期望收益率 (S_T - S_0)/S_0

    # 时间轴（归一化 0→T）
    time_axis: list[float]


def simulate_gbm(
    S0: float,
    mu: float,
    sigma: float,
    T: float = 1.0,
    n_paths: int = 1000,
    n_steps: int = 252,
    seed: int = -1,
) -> GBMResult:
    """
    执行 GBM Monte Carlo 模拟。

    参数说明:
        S0      — 起始价格，如 100.0
        mu      — 年化漂移率，如 0.10 (10%)
        sigma   — 年化波动率，如 0.20 (20%)
        T       — 时间跨度（年），如 1.0 (1年)
        n_paths — 模拟路径数，如 1000
        n_steps — 步数（日线用252，月线用12）
        seed    — 随机种子（-1=不固定，可复现用整数）

    返回:
        GBMResult 含路径样本、统计量、VaR、CVaR
    """
    if seed >= 0:
        rng = np.random.default_rng(seed)
    else:
        rng = np.random.default_rng()

    dt = T / n_steps
    Z = rng.standard_normal((n_paths, n_steps))

    # 对数收益步长: (μ - σ²/2)·Δt + σ·√Δt·Z
    log_returns = (mu - 0.5 * sigma ** 2) * dt + sigma * np.sqrt(dt) * Z

    # 累计对数收益 → 价格路径 (n_paths, n_steps+1)
    log_cum = np.concatenate(
        [np.zeros((n_paths, 1)), np.cumsum(log_returns, axis=1)],
        axis=1,
    )
    paths = S0 * np.exp(log_cum)

    final_prices = paths[:, -1]

    var_95 = float(S0 - np.percentile(final_prices, 5))
    cvar_95 = float(S0 - np.mean(final_prices[final_prices < np.percentile(final_prices, 5)]))

    # 取最多 100 条路径用于前端绘图
    sample_count = min(100, n_paths)
    idx = rng.choice(n_paths, sample_count, replace=False)
    sample_paths = paths[idx].tolist()

    time_axis = np.linspace(0, T, n_steps + 1).tolist()

    return GBMResult(
        S0=S0,
        mu=mu,
        sigma=sigma,
        T=T,
        n_paths=n_paths,
        n_steps=n_steps,
        sample_paths=sample_paths,
        final_mean=float(np.mean(final_prices)),
        final_std=float(np.std(final_prices)),
        final_median=float(np.median(final_prices)),
        final_p5=float(np.percentile(final_prices, 5)),
        final_p95=float(np.percentile(final_prices, 95)),
        var_95=var_95,
        cvar_95=cvar_95,
        prob_loss=float(np.mean(final_prices < S0)),
        expected_return=float(np.mean(final_prices) / S0 - 1),
        time_axis=time_axis,
    )
