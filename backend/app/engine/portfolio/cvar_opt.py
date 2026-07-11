"""
CVaR / CDaR 尾部风险优化 (D5)

用线性规划（scipy.optimize.linprog, HiGHS）最小化组合的尾部风险，
相比样本方差更贴合「损失厌恶」：只惩罚极端亏损/深度回撤。

- min-CVaR：Rockafellar-Uryasev (2000) 线性化条件风险价值
    CVaR_β = min_α [ α + 1/((1−β)T) · Σ_t max(L_t − α, 0) ]
    其中 L_t = −(收益_t · w) 为组合损失。

- min-CDaR：Chekhlov-Uryasev-Zabarankin (2005) 条件回撤风险
    对累计收益曲线的回撤序列做同样的 CVaR 线性化。

两者均为 LP：决策变量含权重 w、阈值 α、辅助松弛变量。
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import linprog

DEFAULT_BETA = 0.95   # 置信水平（关注最差 5% 情景）
_SOLVER = "highs"


def _validate(returns_matrix: np.ndarray, beta: float) -> tuple[int, int]:
    if returns_matrix.ndim != 2:
        raise ValueError("returns_matrix 必须是二维 (T × N)")
    t, n = returns_matrix.shape
    if n < 2:
        raise ValueError("至少需要 2 个资产")
    if t < 2:
        raise ValueError("样本期太短（需 ≥ 2 期收益）")
    if not 0.0 < beta < 1.0:
        raise ValueError("beta 必须在 (0, 1)")
    return t, n


def _clean_weights(w: np.ndarray) -> np.ndarray:
    """裁剪数值噪声并重新归一化。"""
    w = np.clip(w, 0.0, None)
    total = w.sum()
    if total <= 1e-12:
        return np.ones_like(w) / len(w)
    return w / total


# ── min-CVaR ──────────────────────────────────────────────────

def min_cvar_weights(
    returns_matrix: np.ndarray,
    *,
    beta: float = DEFAULT_BETA,
) -> np.ndarray:
    """
    最小化 β-CVaR。决策向量 x = [w(N), α(1), u(T)]。

    目标: min α + 1/((1−β)T) · Σ u_t
    约束: u_t ≥ L_t − α = −(r_t·w) − α  →  −(r_t·w) − α − u_t ≤ 0
          Σ w = 1, w ∈ [0,1], u ≥ 0, α 自由
    """
    t, n = _validate(returns_matrix, beta)
    coef = 1.0 / ((1.0 - beta) * t)

    c = np.concatenate([np.zeros(n), [1.0], np.full(t, coef)])

    # A_ub x ≤ 0： −r_t·w − α − u_t ≤ 0
    a_w = -returns_matrix                       # T×N
    a_alpha = -np.ones((t, 1))                  # T×1
    a_u = -np.eye(t)                            # T×T
    a_ub = np.hstack([a_w, a_alpha, a_u])
    b_ub = np.zeros(t)

    # Σ w = 1
    a_eq = np.concatenate([np.ones(n), [0.0], np.zeros(t)]).reshape(1, -1)
    b_eq = np.array([1.0])

    bounds = [(0.0, 1.0)] * n + [(None, None)] + [(0.0, None)] * t

    res = linprog(c, A_ub=a_ub, b_ub=b_ub, A_eq=a_eq, b_eq=b_eq,
                  bounds=bounds, method=_SOLVER)
    if not res.success:
        raise ValueError(f"min-CVaR LP 求解失败：{res.message}")
    return _clean_weights(res.x[:n])


# ── min-CDaR ──────────────────────────────────────────────────

def min_cdar_weights(
    returns_matrix: np.ndarray,
    *,
    beta: float = DEFAULT_BETA,
) -> np.ndarray:
    """
    最小化 β-CDaR（条件回撤风险）。

    决策向量 x = [w(N), α(1), z(T), u(T)]，其中：
      y_t = 累计收益 = C_t·w（C 为收益的时间前缀和）
      u_t = 截至 t 的累计收益运行峰值（u_t ≥ y_t, u_t ≥ u_{t−1}）
      回撤 DD_t = u_t − y_t
    目标: min α + 1/((1−β)T) · Σ z_t，  z_t ≥ DD_t − α
    """
    t, n = _validate(returns_matrix, beta)
    coef = 1.0 / ((1.0 - beta) * t)
    cum = np.cumsum(returns_matrix, axis=0)     # C: T×N，累计收益

    # 变量分块索引
    w_sl = slice(0, n)
    a_ix = n
    z_sl = slice(n + 1, n + 1 + t)
    u_sl = slice(n + 1 + t, n + 1 + 2 * t)
    dim = n + 1 + 2 * t

    c = np.zeros(dim)
    c[a_ix] = 1.0
    c[z_sl] = coef

    rows: list[np.ndarray] = []
    # 约束1: z_t ≥ (u_t − y_t) − α  →  −z_t + u_t − C_t·w − α ≤ 0
    for i in range(t):
        row = np.zeros(dim)
        row[w_sl] = -cum[i]
        row[a_ix] = -1.0
        row[n + 1 + i] = -1.0          # −z_t
        row[u_sl.start + i] = 1.0      # +u_t
        rows.append(row)
    # 约束2: u_t ≥ y_t  →  −u_t + C_t·w ≤ 0
    for i in range(t):
        row = np.zeros(dim)
        row[w_sl] = cum[i]
        row[u_sl.start + i] = -1.0
        rows.append(row)
    # 约束3: u_t ≥ u_{t−1}  →  u_{t−1} − u_t ≤ 0
    for i in range(1, t):
        row = np.zeros(dim)
        row[u_sl.start + i - 1] = 1.0
        row[u_sl.start + i] = -1.0
        rows.append(row)

    a_ub = np.vstack(rows)
    b_ub = np.zeros(a_ub.shape[0])

    a_eq = np.zeros((1, dim))
    a_eq[0, w_sl] = 1.0
    b_eq = np.array([1.0])

    bounds = (
        [(0.0, 1.0)] * n            # w
        + [(None, None)]           # α
        + [(0.0, None)] * t        # z ≥ 0
        + [(0.0, None)] * t        # u ≥ 0
    )

    res = linprog(c, A_ub=a_ub, b_ub=b_ub, A_eq=a_eq, b_eq=b_eq,
                  bounds=bounds, method=_SOLVER)
    if not res.success:
        raise ValueError(f"min-CDaR LP 求解失败：{res.message}")
    return _clean_weights(res.x[:n])
