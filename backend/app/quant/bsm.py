"""
Black-Scholes-Merton (BSM) 期权定价模型 + Greeks

理论基础:
  欧式看涨: C = S·N(d1) - K·e^{-rT}·N(d2)
  欧式看跌: P = K·e^{-rT}·N(-d2) - S·N(-d1)

  d1 = [ln(S/K) + (r + σ²/2)·T] / (σ·√T)
  d2 = d1 - σ·√T

Greeks:
  Δ (Delta) — 对标的价格的一阶导数，期权价格对 S 的敏感性
  Γ (Gamma) — Delta 对 S 的变化率（凸性）
  Θ (Theta) — 时间衰减（每天损失的时间价值）
  ν (Vega)  — 对波动率的敏感性（波动率上升1%，期权价格变化）
  ρ (Rho)   — 对无风险利率的敏感性

配置参数:
  S     — 标的当前价格
  K     — 执行价格
  r     — 无风险年化利率（如 0.05=5%）
  sigma — 年化隐含波动率（如 0.20=20%）
  T     — 到期时间（年，如 0.25=3个月后到期）
  q     — 股息率（默认0，有分红时需填入）
  option_type — "call" 或 "put"
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from scipy.stats import norm  # type: ignore[import-untyped]


@dataclass
class BSMResult:
    """BSM 期权定价结果。"""
    option_type: str
    S: float
    K: float
    r: float
    sigma: float
    T: float
    q: float

    # 定价
    price: float
    intrinsic_value: float    # 内在价值
    time_value: float         # 时间价值 = price - intrinsic_value

    # Greeks
    delta: float   # Δ
    gamma: float   # Γ
    theta: float   # Θ（日衰减，已除以365）
    vega: float    # ν（波动率变化1%对应的期权价格变化）
    rho: float     # ρ（利率变化1%对应的期权价格变化）

    # 辅助信息
    d1: float
    d2: float
    nd1: float   # N(d1)
    nd2: float   # N(d2)


def price_option(
    S: float,
    K: float,
    r: float,
    sigma: float,
    T: float,
    q: float = 0.0,
    option_type: str = "call",
) -> BSMResult:
    """
    BSM 欧式期权定价 + Greeks。

    参数说明:
        S           — 标的现价，如 150.0
        K           — 行权价，如 155.0
        r           — 无风险利率（年化），如 0.05
        sigma       — 隐含波动率（年化），如 0.25
        T           — 到期年数，如 0.25 (3个月)
        q           — 连续股息率，默认 0.0
        option_type — "call" (认购) 或 "put" (认沽)

    抛出:
        ValueError — T <= 0 或 sigma <= 0 或 S <= 0 或 K <= 0
    """
    if T <= 0:
        raise ValueError(f"到期时间 T 必须 > 0，当前: {T}")
    if sigma <= 0:
        raise ValueError(f"波动率 sigma 必须 > 0，当前: {sigma}")
    if S <= 0 or K <= 0:
        raise ValueError("标的价格和行权价必须 > 0")

    opt = option_type.lower()
    if opt not in ("call", "put"):
        raise ValueError(f"option_type 必须为 'call' 或 'put'，当前: {option_type}")

    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T

    nd1 = float(norm.cdf(d1))
    nd2 = float(norm.cdf(d2))
    n_neg_d1 = float(norm.cdf(-d1))
    n_neg_d2 = float(norm.cdf(-d2))
    n_pdf_d1 = float(norm.pdf(d1))  # N'(d1)

    disc = math.exp(-r * T)
    div_disc = math.exp(-q * T)

    if opt == "call":
        price = S * div_disc * nd1 - K * disc * nd2
        intrinsic = max(0.0, S - K)
        delta = div_disc * nd1
        rho = K * T * disc * nd2 / 100
    else:
        price = K * disc * n_neg_d2 - S * div_disc * n_neg_d1
        intrinsic = max(0.0, K - S)
        delta = -div_disc * n_neg_d1
        rho = -K * T * disc * n_neg_d2 / 100

    gamma = (div_disc * n_pdf_d1) / (S * sigma * sqrt_T)
    vega = S * div_disc * n_pdf_d1 * sqrt_T / 100  # per 1% vol change

    # Theta（每日衰减）
    base_theta = -(S * div_disc * n_pdf_d1 * sigma) / (2 * sqrt_T)
    if opt == "call":
        theta = (base_theta - r * K * disc * nd2 + q * S * div_disc * nd1) / 365
    else:
        theta = (base_theta + r * K * disc * n_neg_d2 - q * S * div_disc * n_neg_d1) / 365

    return BSMResult(
        option_type=opt,
        S=S, K=K, r=r, sigma=sigma, T=T, q=q,
        price=price,
        intrinsic_value=intrinsic,
        time_value=max(0.0, price - intrinsic),
        delta=delta,
        gamma=gamma,
        theta=theta,
        vega=vega,
        rho=rho,
        d1=d1,
        d2=d2,
        nd1=nd1,
        nd2=nd2,
    )
