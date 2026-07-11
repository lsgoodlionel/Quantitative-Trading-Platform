"""
滚动算子原语（Rolling Operator Primitives）

参照 qlib `data/ops.py` 中 Rolling / PairRolling 算子的**算法定义**（非复制代码），
用 pandas/numpy 重新实现为纯函数。这些原语被两处复用（DRY）：

  1. formula_factor.py 的 RPN 引擎 — 注册为带窗口的时序算子（B3）
  2. factor_lib/loader.py 的声明式因子库 — Alpha158 式表达式的构建块（B2）

全部为无副作用纯函数：输入 pd.Series，输出等长 pd.Series（前 N-1 个为 NaN），
绝不原地修改输入。窗口不足或常数窗口按 NaN/兜底处理，避免除零。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# 数值常量（禁止内联魔数）
EPS: float = 1e-12
# 与 qlib 一致：滚动标准差接近 0 时判定相关性/回归无意义
STD_ATOL: float = 2e-5


def _require_window(n: int, minimum: int) -> None:
    if not isinstance(n, int) or n < minimum:
        raise ValueError(f"窗口大小必须为 ≥ {minimum} 的整数，实得 {n}")


# ── 回归族（Slope / Rsquare / Resi）────────────────────────────────
# 对滚动窗口内序列 y 与固定自变量 x = [0, 1, ..., N-1] 做一元线性回归。


def _regression_prep(n: int) -> tuple[np.ndarray, np.ndarray, float]:
    """预计算固定自变量 x、其去均值 xc 与 Σxc²（分母，n≥2 时恒正）。"""
    x = np.arange(n, dtype=float)
    xc = x - x.mean()
    denom = float(np.dot(xc, xc))
    return x, xc, denom


def rolling_slope(s: pd.Series, n: int) -> pd.Series:
    """滚动一元回归斜率 β：cov(x, y) / var(x)。价格每期涨 k 元则 β≈k。"""
    _require_window(n, 2)
    _, xc, denom = _regression_prep(n)

    def _slope(y: np.ndarray) -> float:
        return float(np.dot(xc, y) / denom)

    return s.rolling(n, min_periods=n).apply(_slope, raw=True)


def rolling_rsquare(s: pd.Series, n: int) -> pd.Series:
    """滚动回归判定系数 R²（拟合优度，[0,1]）。常数窗口 → NaN。"""
    _require_window(n, 2)
    _, xc, denom = _regression_prep(n)

    def _r2(y: np.ndarray) -> float:
        yc = y - y.mean()
        ss_tot = float(np.dot(yc, yc))
        if ss_tot < EPS:
            return np.nan
        num = float(np.dot(xc, y))
        return (num * num) / (denom * ss_tot)

    return s.rolling(n, min_periods=n).apply(_r2, raw=True)


def rolling_resi(s: pd.Series, n: int) -> pd.Series:
    """滚动回归在窗口末端点的残差：y[-1] − ŷ[-1]。"""
    _require_window(n, 2)
    x, xc, denom = _regression_prep(n)
    x_mean = float(x.mean())
    last_x = float(n - 1)

    def _resi(y: np.ndarray) -> float:
        beta = float(np.dot(xc, y) / denom)
        intercept = float(y.mean()) - beta * x_mean
        pred_last = intercept + beta * last_x
        return float(y[-1] - pred_last)

    return s.rolling(n, min_periods=n).apply(_resi, raw=True)


# ── 配对滚动族（Corr / Cov）────────────────────────────────────────


def rolling_corr(a: pd.Series, b: pd.Series, n: int) -> pd.Series:
    """滚动 Pearson 相关系数；任一序列窗口标准差≈0 → NaN（与 qlib 一致）。"""
    _require_window(n, 2)
    aligned_b = b.reindex(a.index)
    corr = a.rolling(n, min_periods=n).corr(aligned_b)
    std_a = a.rolling(n, min_periods=n).std()
    std_b = aligned_b.rolling(n, min_periods=n).std()
    degenerate = (std_a.abs() < STD_ATOL) | (std_b.abs() < STD_ATOL)
    return corr.mask(degenerate).replace([np.inf, -np.inf], np.nan)


def rolling_cov(a: pd.Series, b: pd.Series, n: int) -> pd.Series:
    """滚动样本协方差。"""
    _require_window(n, 2)
    aligned_b = b.reindex(a.index)
    return a.rolling(n, min_periods=n).cov(aligned_b).replace([np.inf, -np.inf], np.nan)


# ── 加权/指数均值族（WMA / EMA）────────────────────────────────────


def wma(s: pd.Series, n: int) -> pd.Series:
    """线性加权移动平均：权重 (1, 2, ..., N) 归一化，越近权重越大。"""
    _require_window(n, 1)
    weights = np.arange(1, n + 1, dtype=float)
    weights /= weights.sum()

    def _wmean(y: np.ndarray) -> float:
        return float(np.dot(weights, y))

    return s.rolling(n, min_periods=n).apply(_wmean, raw=True)


def ema(s: pd.Series, n: int) -> pd.Series:
    """指数移动平均（span=N，adjust=False，前 N-1 期 NaN）。"""
    _require_window(n, 1)
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


# ── 分布/位置族（Quantile / Mad / IdxMax / IdxMin / Rank）──────────


def rolling_quantile(s: pd.Series, n: int, q: float) -> pd.Series:
    """滚动分位数（q∈[0,1]）。"""
    _require_window(n, 1)
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"分位数 q 必须在 [0,1]，实得 {q}")
    return s.rolling(n, min_periods=n).quantile(q)


def rolling_mad(s: pd.Series, n: int) -> pd.Series:
    """滚动平均绝对偏差 mean(|x − mean(x)|)（离散度度量）。"""
    _require_window(n, 1)

    def _mad(y: np.ndarray) -> float:
        return float(np.mean(np.abs(y - y.mean())))

    return s.rolling(n, min_periods=n).apply(_mad, raw=True)


def rolling_idxmax(s: pd.Series, n: int) -> pd.Series:
    """滚动窗口内最大值的位置（1..N，越大表示极值越靠近当前）。"""
    _require_window(n, 1)
    return s.rolling(n, min_periods=n).apply(lambda y: float(np.argmax(y) + 1), raw=True)


def rolling_idxmin(s: pd.Series, n: int) -> pd.Series:
    """滚动窗口内最小值的位置（1..N）。"""
    _require_window(n, 1)
    return s.rolling(n, min_periods=n).apply(lambda y: float(np.argmin(y) + 1), raw=True)


def rolling_rank(s: pd.Series, n: int) -> pd.Series:
    """滚动分位排名（当前值在过去 N 期中的百分位，(0,1]）。"""
    _require_window(n, 1)
    roller = s.rolling(n, min_periods=n)
    if hasattr(roller, "rank"):  # pandas ≥ 1.4
        return roller.rank(pct=True)

    def _rank(y: np.ndarray) -> float:
        return float((y.argsort().argsort()[-1] + 1) / len(y))

    return roller.apply(_rank, raw=True)
