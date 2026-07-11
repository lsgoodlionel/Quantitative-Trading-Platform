"""
Black-Litterman 模型 (D3)

将「市场均衡隐含收益」与「投资者观点」贝叶斯融合，输出后验预期收益与后验协方差，
再喂给下游均值-方差优化器（如 max_sharpe）。

核心步骤：
1. 市场隐含先验 π = δ·Σ·w_mkt  （反向优化：市场组合权重 → 隐含超额收益）
2. 观点矩阵 P（K×N 拾取矩阵）、观点向量 Q（K×1 预期收益/超额收益）
3. 观点不确定性 Ω（Idzorek 置信度法：由 0~1 的置信度反推方差）
4. 后验收益 = π + τΣPᵀ·(PτΣPᵀ + Ω)⁻¹·(Q − Pπ)   （用线性求解规避求逆）
5. 后验协方差 = Σ + M

参考签名（不复制实现）:
- refs/PyPortfolioOpt/pypfopt/black_litterman.py
  market_implied_prior_returns / market_implied_risk_aversion /
  BlackLittermanModel.idzorek_method / bl_returns / bl_cov
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import pandas as pd

TRADING_DAYS = 252
DEFAULT_RISK_AVERSION = 2.5  # δ 默认值（市场价格风险，He-Litterman 常用区间 2~3）
DEFAULT_TAU = 0.05           # τ：先验不确定性缩放（常用 0.01~0.05）
DEFAULT_RISK_FREE = 0.05


class ViewKind(str, Enum):
    ABSOLUTE = "absolute"   # 绝对观点：某标的年化收益 = value
    RELATIVE = "relative"   # 相对观点：long 相对 short 超额 = value


@dataclass(frozen=True)
class InvestorView:
    """
    单条投资者观点。

    - ABSOLUTE：assets=(sym,)，含义「sym 的年化预期收益 = value」
    - RELATIVE：assets=(long_sym, short_sym)，含义「long 相对 short 超额收益 = value」

    confidence ∈ [0, 1]：Idzorek 置信度，0 表示完全无把握（观点被忽略），1 表示绝对确信。
    """
    kind: ViewKind
    assets: tuple[str, ...]
    value: float
    confidence: float = 0.5


@dataclass
class BLResult:
    posterior_returns: pd.Series          # 后验年化预期收益（index=symbol）
    posterior_cov: pd.DataFrame           # 后验年化协方差
    prior_returns: pd.Series              # 市场隐含先验收益
    risk_aversion: float                  # 所用 δ
    tau: float
    market_weights: dict[str, float]      # 市场组合权重（先验来源）
    view_labels: list[str] = field(default_factory=list)


# ── 观点解析 ──────────────────────────────────────────────────

def parse_views(
    views: list[InvestorView],
    symbols: list[str],
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]]:
    """
    将观点列表解析为拾取矩阵 P (K×N)、观点向量 Q (K,)、置信度向量 (K,) 与文本标签。

    未在 symbols 中的标的会触发 ValueError（快速失败）。
    """
    if not views:
        raise ValueError("Black-Litterman 至少需要 1 条投资者观点")

    idx = {s: i for i, s in enumerate(symbols)}
    n = len(symbols)
    rows: list[np.ndarray] = []
    q_vals: list[float] = []
    confidences: list[float] = []
    labels: list[str] = []

    for view in views:
        if not 0.0 <= view.confidence <= 1.0:
            raise ValueError(f"观点置信度必须在 [0, 1]：{view.confidence}")
        row = np.zeros(n)
        if view.kind == ViewKind.ABSOLUTE:
            if len(view.assets) != 1:
                raise ValueError("绝对观点必须且仅含 1 个标的")
            sym = view.assets[0]
            _require_symbol(sym, idx)
            row[idx[sym]] = 1.0
            labels.append(f"{sym} 年化收益 = {view.value:.1%}")
        elif view.kind == ViewKind.RELATIVE:
            if len(view.assets) != 2:
                raise ValueError("相对观点必须含 2 个标的（long, short）")
            long_sym, short_sym = view.assets
            _require_symbol(long_sym, idx)
            _require_symbol(short_sym, idx)
            row[idx[long_sym]] = 1.0
            row[idx[short_sym]] = -1.0
            labels.append(f"{long_sym} 跑赢 {short_sym} {view.value:.1%}")
        else:
            raise ValueError(f"未知观点类型：{view.kind}")
        rows.append(row)
        q_vals.append(float(view.value))
        confidences.append(float(view.confidence))

    return (
        np.array(rows),
        np.array(q_vals).reshape(-1, 1),
        np.array(confidences),
        labels,
    )


def _require_symbol(sym: str, idx: dict[str, int]) -> None:
    if sym not in idx:
        raise ValueError(f"观点标的「{sym}」不在资产池中")


# ── 市场隐含先验 ────────────────────────────────────────────────

def market_implied_prior_returns(
    market_weights: np.ndarray,
    risk_aversion: float,
    cov: np.ndarray,
    risk_free_rate: float = 0.0,
) -> np.ndarray:
    """π = δ·Σ·w_mkt + rf（反向优化：由市场权重推隐含收益）。"""
    return risk_aversion * (cov @ market_weights) + risk_free_rate


def market_implied_risk_aversion(
    market_prices: pd.Series,
    frequency: int = TRADING_DAYS,
    risk_free_rate: float = 0.0,
) -> float:
    """δ = (R − Rf) / σ²，由市场组合价格序列估计市场价格风险。"""
    rets = market_prices.pct_change().dropna()
    r = float(rets.mean() * frequency)
    var = float(rets.var() * frequency)
    if var < 1e-12:
        return DEFAULT_RISK_AVERSION
    return (r - risk_free_rate) / var


# ── Idzorek 置信度 → 观点不确定性 Ω ────────────────────────────

def idzorek_omega(
    view_confidences: np.ndarray,
    cov: np.ndarray,
    P: np.ndarray,
    tau: float,
) -> np.ndarray:
    """
    Idzorek 简化闭式解：Ω_kk = τ·α_k·(P_k Σ P_kᵀ)，α_k = (1−c_k)/c_k。

    置信度越高 → α 越小 → Ω 越小 → 观点权重越大。c=0 时退化为极大不确定性（忽略观点）。
    """
    k = len(view_confidences)
    diag = np.zeros(k)
    for i in range(k):
        conf = float(view_confidences[i])
        if conf <= 0.0:
            diag[i] = 1e6  # 无把握：极大方差 → 观点被忽略
            continue
        p_row = P[i].reshape(1, -1)
        view_var = float((p_row @ cov @ p_row.T).item())
        alpha = (1.0 - conf) / conf
        diag[i] = max(tau * alpha * view_var, 1e-10)
    return np.diag(diag)


# ── 后验收益 & 协方差（线性求解，规避求逆）────────────────────

def _posterior(
    pi: np.ndarray,
    cov: np.ndarray,
    P: np.ndarray,
    Q: np.ndarray,
    omega: np.ndarray,
    tau: float,
) -> tuple[np.ndarray, np.ndarray]:
    """返回 (后验收益向量, 后验协方差矩阵)。"""
    tau_sigma_pt = tau * cov @ P.T           # N×K
    a = P @ tau_sigma_pt + omega             # K×K
    b_ret = Q - P @ pi.reshape(-1, 1)        # K×1
    sol = _solve(a, b_ret)                    # K×1
    post_rets = pi.reshape(-1, 1) + tau_sigma_pt @ sol

    m_sol = _solve(a, tau_sigma_pt.T)         # K×N
    m = tau * cov - tau_sigma_pt @ m_sol      # N×N
    post_cov = cov + m
    return post_rets.flatten(), post_cov


def _solve(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """线性求解 A x = b，奇异时回退最小二乘。"""
    try:
        return np.linalg.solve(a, b)
    except np.linalg.LinAlgError:
        return np.linalg.lstsq(a, b, rcond=None)[0]


# ── 主入口 ────────────────────────────────────────────────────

def black_litterman(
    cov: pd.DataFrame,
    views: list[InvestorView],
    *,
    market_caps: dict[str, float] | None = None,
    market_prices: pd.Series | None = None,
    risk_aversion: float | None = None,
    risk_free_rate: float = DEFAULT_RISK_FREE,
    tau: float = DEFAULT_TAU,
) -> BLResult:
    """
    执行 Black-Litterman 融合，返回后验收益/协方差。

    Args:
        cov: 年化协方差 DataFrame（symbol × symbol，建议来自 risk_models）
        views: 投资者观点列表
        market_caps: symbol → 市值；缺省时以等权组合作为市场代理
        market_prices: 市场组合（如 SPY）价格序列，用于估计 δ
        risk_aversion: 显式 δ；优先级高于 market_prices 推断
        risk_free_rate: 年化无风险利率
        tau: 先验不确定性缩放
    """
    symbols = list(cov.columns)
    n = len(symbols)
    if n < 2:
        raise ValueError("Black-Litterman 至少需要 2 个资产")

    cov_np = cov.to_numpy()

    # 市场权重（先验来源）
    if market_caps:
        caps = np.array([float(market_caps.get(s, 0.0)) for s in symbols])
        if caps.sum() <= 0:
            raise ValueError("market_caps 之和必须为正")
        mkt_w = caps / caps.sum()
    else:
        mkt_w = np.ones(n) / n  # 等权代理

    # 风险厌恶 δ
    if risk_aversion is not None:
        delta = float(risk_aversion)
    elif market_prices is not None and len(market_prices) > 2:
        delta = market_implied_risk_aversion(
            market_prices, risk_free_rate=risk_free_rate
        )
    else:
        delta = DEFAULT_RISK_AVERSION
    delta = max(delta, 1e-6)

    pi = market_implied_prior_returns(mkt_w, delta, cov_np, risk_free_rate)

    p_mat, q_vec, confidences, labels = parse_views(views, symbols)
    omega = idzorek_omega(confidences, cov_np, p_mat, tau)

    post_rets, post_cov = _posterior(pi, cov_np, p_mat, q_vec, omega, tau)

    return BLResult(
        posterior_returns=pd.Series(post_rets, index=symbols),
        posterior_cov=pd.DataFrame(post_cov, index=symbols, columns=symbols),
        prior_returns=pd.Series(pi, index=symbols),
        risk_aversion=round(delta, 4),
        tau=tau,
        market_weights={s: round(float(w), 4) for s, w in zip(symbols, mkt_w)},
        view_labels=labels,
    )
