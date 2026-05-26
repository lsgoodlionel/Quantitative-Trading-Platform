"""
GARCH(p,q) 波动率模型 — 手工 MLE 实现（无需 arch 库）

理论基础:
  σ²_t = ω + Σ αᵢ·r²_{t-i} + Σ βⱼ·σ²_{t-j}

  GARCH(1,1) 是最常用形式:
    σ²_t = ω + α·r²_{t-1} + β·σ²_{t-1}
    条件: ω > 0, α > 0, β > 0, α + β < 1 (平稳性)

  长期波动率: σ̄ = √(ω / (1 - α - β))
  半衰期: t_{½} = ln(0.5) / ln(α + β)

应用场景:
  - 动态风险度量（相比历史波动率更准确）
  - 期权定价（时变波动率输入）
  - 风险价值 (VaR) 计算
  - 波动率预测与交易信号

配置参数:
  returns   — 日收益率序列（小数，如 0.01 表示1%）
  p         — ARCH 滞后阶（α 项数），通常 p=1
  q         — GARCH 滞后阶（β 项数），通常 q=1
  forecast_horizon — 预测未来步数（默认30天）
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize  # type: ignore[import-untyped]


@dataclass
class GARCHResult:
    """GARCH(1,1) 拟合结果。"""
    omega: float     # ω: 常数项
    alpha: float     # α: ARCH 系数（新息冲击）
    beta: float      # β: GARCH 系数（持续性）
    log_likelihood: float
    aic: float       # AIC 信息准则
    bic: float       # BIC 信息准则

    # 长期特性
    long_run_vol_annualized: float   # 年化长期波动率 σ̄·√252
    persistence: float               # α + β (越接近1越持续)
    half_life_days: float            # 冲击半衰期（天）

    # 条件方差序列（历史拟合）
    conditional_vol: list[float]      # 年化条件波动率（历史）

    # 波动率预测
    forecast_vol: list[float]         # 未来 n 步年化波动率预测


def _garch11_log_likelihood(params: np.ndarray, returns: np.ndarray) -> float:
    """GARCH(1,1) 负对数似然函数。"""
    omega, alpha, beta = params
    if omega <= 0 or alpha <= 0 or beta <= 0 or alpha + beta >= 1:
        return 1e10

    n = len(returns)
    sigma2 = np.empty(n)
    sigma2[0] = float(np.var(returns))

    for t in range(1, n):
        sigma2[t] = omega + alpha * returns[t - 1] ** 2 + beta * sigma2[t - 1]

    # Gaussian log-likelihood: -0.5 * Σ [ln(2π) + ln(σ²_t) + r_t²/σ²_t]
    ll = -0.5 * float(np.sum(np.log(2 * np.pi * sigma2) + returns ** 2 / sigma2))
    return -ll  # 返回负数用于最小化


def fit_garch11(
    returns: list[float] | np.ndarray,
    forecast_horizon: int = 30,
) -> GARCHResult:
    """
    拟合 GARCH(1,1) 并生成波动率预测。

    参数说明:
        returns          — 日收益率序列（建议 >= 100 个数据点）
        forecast_horizon — 预测步数，默认 30 天

    示例:
        returns = daily_close.pct_change().dropna().tolist()
        result = fit_garch11(returns, forecast_horizon=30)
    """
    r = np.asarray(returns, dtype=float)

    if len(r) < 30:
        raise ValueError(f"收益率序列长度不足（当前 {len(r)}，至少需要 30 个数据点）")

    # 初始参数: ω, α, β
    var0 = float(np.var(r))
    x0 = np.array([var0 * 0.1, 0.1, 0.8])

    result = minimize(
        _garch11_log_likelihood,
        x0,
        args=(r,),
        method="L-BFGS-B",
        bounds=[(1e-9, None), (1e-9, 0.9999), (1e-9, 0.9999)],
        options={"maxiter": 5000, "ftol": 1e-12},
    )

    if not result.success:
        # fallback: 用 Nelder-Mead
        result = minimize(
            _garch11_log_likelihood,
            x0,
            args=(r,),
            method="Nelder-Mead",
            options={"maxiter": 10000},
        )

    omega, alpha, beta = result.x
    ll = -result.fun
    n = len(r)

    # AIC / BIC
    k = 3  # 参数个数
    aic = 2 * k - 2 * ll
    bic = k * float(np.log(n)) - 2 * ll

    # 历史条件方差序列
    sigma2 = np.empty(n)
    sigma2[0] = float(np.var(r))
    for t in range(1, n):
        sigma2[t] = omega + alpha * r[t - 1] ** 2 + beta * sigma2[t - 1]

    # 年化条件波动率
    cond_vol = (np.sqrt(sigma2) * np.sqrt(252)).tolist()

    # 长期波动率（无条件方差）
    long_run_var = omega / (1 - alpha - beta)
    long_run_vol_ann = float(np.sqrt(long_run_var * 252))

    # 冲击半衰期
    persistence = alpha + beta
    half_life = float(np.log(0.5) / np.log(persistence)) if persistence < 1 else float("inf")

    # 波动率预测（递推）
    last_sigma2 = sigma2[-1]
    last_r2 = float(r[-1] ** 2)
    forecast = []
    s2_prev = last_sigma2
    r2_prev = last_r2
    for h in range(1, forecast_horizon + 1):
        if h == 1:
            s2_next = omega + alpha * r2_prev + beta * s2_prev
        else:
            # h>1 步: 期望方差递推 E[σ²_{t+h}] = ω + (α+β)·E[σ²_{t+h-1}]
            s2_next = omega + persistence * s2_prev
        forecast.append(float(np.sqrt(s2_next * 252)))
        s2_prev = s2_next
        r2_prev = s2_prev  # E[r²] = σ²

    return GARCHResult(
        omega=float(omega),
        alpha=float(alpha),
        beta=float(beta),
        log_likelihood=float(ll),
        aic=float(aic),
        bic=float(bic),
        long_run_vol_annualized=long_run_vol_ann,
        persistence=float(persistence),
        half_life_days=float(half_life),
        conditional_vol=cond_vol,
        forecast_vol=forecast,
    )
