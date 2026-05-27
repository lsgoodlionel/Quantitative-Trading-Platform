"""
VaR / CVaR 风险计算引擎

基于历史模拟法计算投资组合的在险价值（Value at Risk）
和条件在险价值（Conditional Value at Risk / Expected Shortfall）。

方法：历史模拟法 + 正态参数法（双结果供比较）

参考:
  RiskMetrics Technical Document (1996)
  Basel III Market Risk Framework
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass


# ── 结果类 ────────────────────────────────────────────────────────

@dataclass
class VaRResult:
    # 历史模拟法
    hist_var_95:   float   # 95% VaR（即 5% 分位数取负）
    hist_var_99:   float   # 99% VaR
    hist_cvar_95:  float   # 95% CVaR（Expected Shortfall）
    hist_cvar_99:  float   # 99% CVaR

    # 参数法（正态分布假设）
    param_var_95:  float
    param_var_99:  float
    param_cvar_95: float
    param_cvar_99: float

    # 收益率统计
    mean_return:   float   # 日均收益率
    std_return:    float   # 日收益率标准差
    skewness:      float   # 偏度
    kurtosis:      float   # 超额峰度（正态=0）
    min_return:    float   # 最大单日亏损
    max_return:    float   # 最大单日收益

    # 组合价值
    portfolio_value: float
    n_days: int

    def as_monetary(self, portfolio_value: float | None = None) -> dict:
        """返回货币金额表示（以组合价值换算）。"""
        pv = portfolio_value or self.portfolio_value
        return {
            "hist_var_95_pct":    round(self.hist_var_95 * 100, 3),
            "hist_var_99_pct":    round(self.hist_var_99 * 100, 3),
            "hist_cvar_95_pct":   round(self.hist_cvar_95 * 100, 3),
            "hist_cvar_99_pct":   round(self.hist_cvar_99 * 100, 3),
            "hist_var_95_value":  round(self.hist_var_95 * pv, 2),
            "hist_var_99_value":  round(self.hist_var_99 * pv, 2),
            "hist_cvar_95_value": round(self.hist_cvar_95 * pv, 2),
            "hist_cvar_99_value": round(self.hist_cvar_99 * pv, 2),
            "param_var_95_pct":   round(self.param_var_95 * 100, 3),
            "param_var_99_pct":   round(self.param_var_99 * 100, 3),
            "param_cvar_95_pct":  round(self.param_cvar_95 * 100, 3),
            "param_cvar_99_pct":  round(self.param_cvar_99 * 100, 3),
            "mean_return_pct":    round(self.mean_return * 100, 4),
            "std_return_pct":     round(self.std_return * 100, 4),
            "skewness":           round(self.skewness, 4),
            "kurtosis":           round(self.kurtosis, 4),
            "min_return_pct":     round(self.min_return * 100, 3),
            "max_return_pct":     round(self.max_return * 100, 3),
            "portfolio_value":    round(pv, 2),
            "n_days":             self.n_days,
        }


# ── 核心计算函数 ──────────────────────────────────────────────────

def compute_portfolio_var(
    returns: list[float],
    portfolio_value: float,
    confidence_levels: tuple[float, float] = (0.95, 0.99),
) -> VaRResult:
    """
    基于历史收益率序列计算投资组合 VaR / CVaR。

    Parameters
    ----------
    returns          : 日收益率序列（如 [0.012, -0.023, ...]）
    portfolio_value  : 当前组合价值（用于换算货币金额）
    confidence_levels: 置信水平元组，默认 (0.95, 0.99)
    """
    arr = np.array(returns, dtype=float)
    arr = arr[~np.isnan(arr)]

    if len(arr) < 20:
        raise ValueError(f"Not enough return observations: {len(arr)} (need ≥ 20)")

    c95, c99 = confidence_levels

    # ── 历史模拟法 ──────────────────────────
    sorted_r = np.sort(arr)  # ascending: worst first

    # VaR at level q = -(q-th percentile of returns)
    hist_var_95  = float(-np.percentile(arr, (1 - c95) * 100))
    hist_var_99  = float(-np.percentile(arr, (1 - c99) * 100))

    # CVaR = mean of losses beyond VaR threshold
    cutoff_95 = np.percentile(arr, (1 - c95) * 100)
    cutoff_99 = np.percentile(arr, (1 - c99) * 100)
    tail_95 = arr[arr <= cutoff_95]
    tail_99 = arr[arr <= cutoff_99]
    hist_cvar_95 = float(-np.mean(tail_95)) if len(tail_95) > 0 else hist_var_95
    hist_cvar_99 = float(-np.mean(tail_99)) if len(tail_99) > 0 else hist_var_99

    # ── 参数法（正态分布） ──────────────────
    from scipy import stats  # type: ignore[import]
    mu    = float(np.mean(arr))
    sigma = float(np.std(arr, ddof=1))

    # z-scores for one-tail
    z95 = float(stats.norm.ppf(1 - c95))  # negative
    z99 = float(stats.norm.ppf(1 - c99))

    param_var_95  = float(-(mu + z95 * sigma))
    param_var_99  = float(-(mu + z99 * sigma))

    # Parametric CVaR = (phi(z) / (1-c)) * sigma - mu
    param_cvar_95 = float(
        sigma * stats.norm.pdf(z95) / (1 - c95) - mu
    )
    param_cvar_99 = float(
        sigma * stats.norm.pdf(z99) / (1 - c99) - mu
    )

    # ── 描述统计 ────────────────────────────
    skew  = float(stats.skew(arr))
    kurt  = float(stats.kurtosis(arr))  # excess kurtosis

    return VaRResult(
        hist_var_95=max(hist_var_95, 0.0),
        hist_var_99=max(hist_var_99, 0.0),
        hist_cvar_95=max(hist_cvar_95, 0.0),
        hist_cvar_99=max(hist_cvar_99, 0.0),
        param_var_95=max(param_var_95, 0.0),
        param_var_99=max(param_var_99, 0.0),
        param_cvar_95=max(param_cvar_95, 0.0),
        param_cvar_99=max(param_cvar_99, 0.0),
        mean_return=mu,
        std_return=sigma,
        skewness=skew,
        kurtosis=kurt,
        min_return=float(np.min(arr)),
        max_return=float(np.max(arr)),
        portfolio_value=portfolio_value,
        n_days=len(arr),
    )


def aggregate_portfolio_returns(
    position_returns: dict[str, np.ndarray],
    weights: dict[str, float],
) -> np.ndarray:
    """
    合并各标的日收益率为组合日收益率。

    Parameters
    ----------
    position_returns: {symbol: daily_returns_array}
    weights         : {symbol: weight (0~1)}，权重之和应≈1
    """
    if not position_returns:
        raise ValueError("No position returns provided")

    # Align lengths
    min_len = min(len(arr) for arr in position_returns.values())
    if min_len < 20:
        raise ValueError(f"Insufficient history: {min_len} days")

    portfolio_ret = np.zeros(min_len)
    for symbol, ret_arr in position_returns.items():
        w = weights.get(symbol, 0.0)
        portfolio_ret += w * ret_arr[-min_len:]

    return portfolio_ret
