"""
隐马尔可夫模型 (HMM) 市场状态识别

理论基础:
  观测: r_t ~ N(μ_k, σ_k²) 当状态 s_t = k
  转移: P(s_t = j | s_{t-1} = i) = A_{ij}

  参数集: θ = {π (初始), A (转移矩阵), μ (均值), σ (标准差)}

  Baum-Welch (EM) 算法:
    E步: 前向-后向计算 γ_{tk} = P(s_t=k|r, θ)
    M步: 更新 π, A, μ, σ

  Viterbi 解码: 最可能的状态序列

应用场景:
  - 牛市/熊市/震荡市 三状态识别
  - 动态仓位管理（牛市加仓，熊市减仓）
  - 策略切换（趋势策略 vs 均值回归策略）
  - 风险预算动态调整

配置参数:
  returns        — 收益率序列（日/周收益率）
  n_states       — 状态数（默认2: 低波动/高波动）
  n_iterations   — EM 最大迭代次数（默认100）
  tol            — 收敛阈值（默认1e-4）
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class HMMResult:
    """HMM 市场状态识别结果。"""
    n_states: int
    n_observations: int

    # 拟合参数
    initial_probs: list[float]          # π (初始状态概率)
    transition_matrix: list[list[float]] # A_{ij}
    state_means: list[float]            # μ_k (每个状态的收益率均值)
    state_vols: list[float]             # σ_k (每个状态的波动率)

    # 解码结果
    state_sequence: list[int]           # Viterbi 解码的状态序列
    state_probs: list[list[float]]      # γ_{tk}: 每时刻各状态概率

    # 当前状态
    current_state: int
    current_state_prob: float

    # 状态描述（按均值排序）
    state_labels: list[str]             # 如 ["熊市", "牛市"] 或 ["低波动", "中波动", "高波动"]

    # 统计
    log_likelihood: float
    n_iterations: int


def _gaussian_pdf(x: np.ndarray, mu: float, sigma: float) -> np.ndarray:
    """高斯概率密度函数（向量化）。"""
    return np.exp(-0.5 * ((x - mu) / sigma) ** 2) / (sigma * np.sqrt(2 * np.pi)) + 1e-300


def fit_hmm(
    returns: list[float],
    n_states: int = 2,
    n_iterations: int = 100,
    tol: float = 1e-4,
    seed: int = 42,
) -> HMMResult:
    """
    用 Baum-Welch EM 算法拟合高斯 HMM，再用 Viterbi 解码状态序列。

    参数说明:
        returns      — 收益率序列（日收益率），如 pct_change().dropna()
        n_states     — 状态数，2（牛/熊）或 3（牛/震荡/熊）
        n_iterations — EM 最大迭代次数
        tol          — 对数似然变化收敛阈值
        seed         — 初始化随机种子

    抛出:
        ValueError — 序列过短或状态数不合理
    """
    r = np.asarray(returns, dtype=float)
    T = len(r)

    if T < 20:
        raise ValueError(f"收益率序列长度不足（当前 {T}，建议 >= 20）")
    if n_states < 2 or n_states > 5:
        raise ValueError(f"状态数 n_states 应在 [2, 5]，当前: {n_states}")

    rng = np.random.default_rng(seed)

    # 初始化参数（K-means 风格：按分位数划分）
    sorted_r = np.sort(r)
    quantiles = np.linspace(0, 1, n_states + 2)[1:-1]
    mu = np.array([float(np.percentile(r, q * 100)) for q in quantiles])
    sigma = np.full(n_states, float(np.std(r)))
    sigma = np.maximum(sigma, 1e-6)

    # 均匀转移矩阵
    A = np.ones((n_states, n_states)) / n_states + rng.uniform(-0.1, 0.1, (n_states, n_states))
    A = np.abs(A)
    A /= A.sum(axis=1, keepdims=True)

    pi = np.ones(n_states) / n_states

    prev_ll = -np.inf
    n_iter = 0

    for iteration in range(n_iterations):
        n_iter = iteration + 1

        # ── E 步: 前向-后向 ──────────────────────────────────────
        B = np.column_stack([_gaussian_pdf(r, mu[k], sigma[k]) for k in range(n_states)])

        # 前向变量 α (scaled)
        alpha = np.zeros((T, n_states))
        c = np.zeros(T)
        alpha[0] = pi * B[0]
        c[0] = alpha[0].sum()
        alpha[0] /= c[0] + 1e-300

        for t in range(1, T):
            alpha[t] = (alpha[t - 1] @ A) * B[t]
            c[t] = alpha[t].sum()
            alpha[t] /= c[t] + 1e-300

        ll = float(np.sum(np.log(c + 1e-300)))

        # 后向变量 β (scaled)
        beta = np.zeros((T, n_states))
        beta[T - 1] = 1.0
        for t in range(T - 2, -1, -1):
            beta[t] = (A * B[t + 1] * beta[t + 1]).sum(axis=1)
            beta[t] /= beta[t].sum() + 1e-300

        # γ: 状态后验概率
        gamma = alpha * beta
        gamma /= gamma.sum(axis=1, keepdims=True) + 1e-300

        # ξ: 转移后验概率
        xi = np.zeros((T - 1, n_states, n_states))
        for t in range(T - 1):
            xi[t] = alpha[t].reshape(-1, 1) * A * B[t + 1] * beta[t + 1]
            xi[t] /= xi[t].sum() + 1e-300

        # ── M 步: 更新参数 ──────────────────────────────────────
        pi = gamma[0] / gamma[0].sum()
        A = xi.sum(axis=0)
        A /= A.sum(axis=1, keepdims=True) + 1e-300
        for k in range(n_states):
            w = gamma[:, k]
            mu[k] = float((w * r).sum() / (w.sum() + 1e-300))
            sigma[k] = float(np.sqrt((w * (r - mu[k]) ** 2).sum() / (w.sum() + 1e-300)) + 1e-6)

        if abs(ll - prev_ll) < tol:
            break
        prev_ll = ll

    # ── Viterbi 解码 ─────────────────────────────────────────
    B = np.column_stack([_gaussian_pdf(r, mu[k], sigma[k]) for k in range(n_states)])
    viterbi = np.zeros((T, n_states))
    psi = np.zeros((T, n_states), dtype=int)

    viterbi[0] = np.log(pi + 1e-300) + np.log(B[0] + 1e-300)
    for t in range(1, T):
        trans_prob = viterbi[t - 1].reshape(-1, 1) + np.log(A + 1e-300)
        psi[t] = np.argmax(trans_prob, axis=0)
        viterbi[t] = np.max(trans_prob, axis=0) + np.log(B[t] + 1e-300)

    states = np.zeros(T, dtype=int)
    states[T - 1] = int(np.argmax(viterbi[T - 1]))
    for t in range(T - 2, -1, -1):
        states[t] = psi[t + 1, states[t + 1]]

    # 按状态均值排序（方便解读：低→高收益率）
    order = np.argsort(mu)
    mu_sorted = mu[order]
    sigma_sorted = sigma[order]
    A_sorted = A[order][:, order]
    pi_sorted = pi[order]

    # 重映射状态序列
    inv_order = np.argsort(order)
    states_mapped = inv_order[states]

    # 状态标签
    if n_states == 2:
        labels = ["熊市/震荡", "牛市"]
    elif n_states == 3:
        labels = ["熊市", "震荡市", "牛市"]
    else:
        labels = [f"状态{i+1}" for i in range(n_states)]

    # 重新计算 gamma 用于概率输出
    B2 = np.column_stack([_gaussian_pdf(r, mu_sorted[k], sigma_sorted[k]) for k in range(n_states)])
    alpha2 = np.zeros((T, n_states))
    c2 = np.zeros(T)
    alpha2[0] = pi_sorted * B2[0]
    c2[0] = alpha2[0].sum()
    alpha2[0] /= c2[0] + 1e-300
    for t in range(1, T):
        alpha2[t] = (alpha2[t - 1] @ A_sorted) * B2[t]
        c2[t] = alpha2[t].sum()
        alpha2[t] /= c2[t] + 1e-300
    beta2 = np.zeros((T, n_states))
    beta2[T - 1] = 1.0
    for t in range(T - 2, -1, -1):
        beta2[t] = (A_sorted * B2[t + 1] * beta2[t + 1]).sum(axis=1)
        s = beta2[t].sum()
        beta2[t] /= s + 1e-300
    gamma2 = alpha2 * beta2
    gamma2 /= gamma2.sum(axis=1, keepdims=True) + 1e-300

    current_state = int(states_mapped[-1])
    current_prob = float(gamma2[-1, current_state])

    return HMMResult(
        n_states=n_states,
        n_observations=T,
        initial_probs=pi_sorted.tolist(),
        transition_matrix=A_sorted.tolist(),
        state_means=(mu_sorted * 252).tolist(),       # 年化均值
        state_vols=(sigma_sorted * np.sqrt(252)).tolist(),  # 年化波动率
        state_sequence=states_mapped.tolist(),
        state_probs=gamma2.tolist(),
        current_state=current_state,
        current_state_prob=current_prob,
        state_labels=labels,
        log_likelihood=float(prev_ll),
        n_iterations=n_iter,
    )
