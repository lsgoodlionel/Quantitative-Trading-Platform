"""
Copula 尾部相关性分析 — Gaussian / t-Copula

理论基础 (Sklar 定理):
  任意联合分布 F(x,y) = C(F_X(x), F_Y(y))
  C 为 Copula 函数，独立捕捉相关结构

  Gaussian Copula: C(u,v;ρ) = Φ_ρ(Φ⁻¹(u), Φ⁻¹(v))
  t-Copula:        C(u,v;ρ,ν) = T_{ρ,ν}(T_ν⁻¹(u), T_ν⁻¹(v))

  尾部相关性系数 (Tail Dependence Coefficient):
    λ_U (上尾) = P(X > F_X⁻¹(q) | Y > F_Y⁻¹(q)) as q→1
    λ_L (下尾) = P(X < F_X⁻¹(q) | Y < F_Y⁻¹(q)) as q→0

    Gaussian Copula: λ_U = λ_L = 0 (无尾部相关性)
    t-Copula: λ_U = λ_L = 2·T_{ν+1}(-√(ν+1)·√(1-ρ)/√(1+ρ))

应用场景:
  - 投资组合压力测试（极端事件下的相关性）
  - VaR 计算（考虑尾部相关性）
  - 信用风险建模（默认率联合分布）
  - 配对交易风险评估

配置参数:
  returns_x  — 资产1收益率序列
  returns_y  — 资产2收益率序列
  copula_type — "gaussian" 或 "t"
  tail_q      — 尾部分位数阈值（默认0.05）
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats  # type: ignore[import-untyped]


@dataclass
class CopulaResult:
    """Copula 尾部相关性分析结果。"""
    copula_type: str
    n_observations: int

    # 线性相关
    pearson_rho: float
    spearman_rho: float
    kendall_tau: float

    # Copula 参数
    copula_rho: float        # Gaussian/t Copula 相关参数
    t_df: float | None       # t-Copula 自由度（仅 t-Copula 有效）

    # 尾部相关性
    lower_tail_dep: float    # 下尾相关系数 λ_L（极端下跌同时出现概率）
    upper_tail_dep: float    # 上尾相关系数 λ_U（极端上涨同时出现概率）

    # 经验尾部相关（实际数据估计）
    empirical_lower_tail: float
    empirical_upper_tail: float

    # 散点数据（用于绘图）
    u_samples: list[float]   # 均匀边际（Copula空间）
    v_samples: list[float]

    # 分位数
    tail_quantile: float
    joint_extreme_prob: float  # 两资产同时极端的概率


def _rank_transform(x: np.ndarray) -> np.ndarray:
    """经验 CDF（排秩法）转换到 [0,1]。"""
    n = len(x)
    ranks = stats.rankdata(x)
    return ranks / (n + 1)  # 除以 n+1 避免 0 和 1


def analyze_copula(
    returns_x: list[float],
    returns_y: list[float],
    copula_type: str = "gaussian",
    tail_q: float = 0.05,
) -> CopulaResult:
    """
    对两个收益率序列做 Copula 尾部相关性分析。

    参数说明:
        returns_x   — 资产1收益率列表（日收益率）
        returns_y   — 资产2收益率列表（日收益率）
        copula_type — 'gaussian' 或 't'
        tail_q      — 尾部阈值分位数，如 0.05=5%

    抛出:
        ValueError — 序列长度不足或不等长
    """
    if len(returns_x) != len(returns_y):
        raise ValueError(f"序列长度不一致: {len(returns_x)} vs {len(returns_y)}")
    if len(returns_x) < 30:
        raise ValueError(f"序列长度不足（当前 {len(returns_x)}，建议 >= 30）")

    x = np.asarray(returns_x, dtype=float)
    y = np.asarray(returns_y, dtype=float)
    n = len(x)

    # Pearson / Spearman / Kendall 相关
    pearson = float(np.corrcoef(x, y)[0, 1])
    spearman, _ = stats.spearmanr(x, y)
    kendall, _ = stats.kendalltau(x, y)

    # 排秩变换到 Copula 空间 [0,1]
    u = _rank_transform(x)
    v = _rank_transform(y)

    # Copula 参数估计（最大似然/矩估计）
    # Gaussian Copula: ρ = sin(π/2 · τ)（Kendall's τ 转换）
    copula_rho = float(np.sin(np.pi / 2 * kendall))
    copula_rho = float(np.clip(copula_rho, -0.9999, 0.9999))

    # 尾部相关系数
    t_df = None
    if copula_type.lower() == "gaussian":
        # Gaussian Copula 无理论尾部相关性
        lower_tail_dep = 0.0
        upper_tail_dep = 0.0
    else:
        # t-Copula: 估计自由度（方法矩）
        # 简化估计: ν 使得 E[|r|^2] 与样本匹配
        excess_kurtosis_x = float(stats.kurtosis(x))
        excess_kurtosis_y = float(stats.kurtosis(y))
        avg_kurtosis = (excess_kurtosis_x + excess_kurtosis_y) / 2
        t_df = max(4.0, 6.0 / avg_kurtosis + 4.0) if avg_kurtosis > 0 else 8.0
        t_df = float(min(t_df, 30.0))

        # t-Copula 尾部相关系数
        nu = t_df
        rho = copula_rho
        arg = -np.sqrt((nu + 1) * (1 - rho) / (1 + rho))
        lambda_tail = float(2 * stats.t.cdf(arg, df=nu + 1))
        lower_tail_dep = lambda_tail
        upper_tail_dep = lambda_tail

    # 经验尾部相关（实际数据）
    q_low = tail_q
    q_high = 1 - tail_q
    mask_lower = (u < q_low) & (v < q_low)
    mask_upper = (u > q_high) & (v > q_high)
    empirical_lower = float(mask_lower.sum() / max((u < q_low).sum(), 1))
    empirical_upper = float(mask_upper.sum() / max((u > q_high).sum(), 1))

    # 两资产同时极端下跌的概率估计
    joint_extreme = float(mask_lower.sum() / n)

    return CopulaResult(
        copula_type=copula_type.lower(),
        n_observations=n,
        pearson_rho=pearson,
        spearman_rho=float(spearman),
        kendall_tau=float(kendall),
        copula_rho=copula_rho,
        t_df=t_df,
        lower_tail_dep=lower_tail_dep,
        upper_tail_dep=upper_tail_dep,
        empirical_lower_tail=empirical_lower,
        empirical_upper_tail=empirical_upper,
        u_samples=u.tolist(),
        v_samples=v.tolist(),
        tail_quantile=tail_q,
        joint_extreme_prob=joint_extreme,
    )
