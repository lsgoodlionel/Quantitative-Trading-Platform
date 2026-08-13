"""
面板算子（Panel Operators）— Alpha101 的计算底座

表示形态是**宽表** `DataFrame(index=datetime, columns=instrument)`：
一列是一个标的的时间序列，一行是一个截面。这样时序算子（沿 axis=0 的 rolling）
与截面算子（沿 axis=1，见 `app/quant/cross_section.py`）都能一次性向量化到
全 universe，避免逐标的 Python 循环。

算子语义移植自 vnpy `alpha/dataset/{ts,math}_function.py`（**MIT，可移植**），
与 WorldQuant Alpha101 原文的算子表一一对应：

  ts_delay/ts_delta/ts_sum/ts_mean/ts_std/ts_min/ts_max/ts_argmax/ts_argmin/
  ts_rank/ts_corr/ts_cov/ts_decay_linear/ts_product/ts_greater/ts_less
  sign/log/abs/pow1/pow2/quesval/quesval2/lt

**与 vnpy 的一处刻意分歧**：`ts_decay_linear` 的权重方向。
vnpy 的实现把最大权重给了窗口内**最老**的一根 bar，而 Alpha101 原文的
`decay_linear(x, d)` 是「权重 d, d-1, …, 1 依次赋给最近到最远」。
这里按原文实现（直接复用 `operators.wma`，权重 1..n 递增到最近）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.quant.factor_lib.operators import STD_ATOL, wma

Wide = pd.DataFrame

#: 除零保护
PANEL_EPS: float = 1e-12


def _require_window(window: int, minimum: int = 1) -> None:
    if not isinstance(window, int) or window < minimum:
        raise ValueError(f"窗口大小必须为 ≥ {minimum} 的整数，实得 {window}")


# ── 时序算子 ──────────────────────────────────────────────────────


def ts_delay(wide: Wide, window: int) -> Wide:
    """滞后 window 期。"""
    _require_window(window)
    return wide.shift(window)


def ts_delta(wide: Wide, window: int) -> Wide:
    """与 window 期前的差值。"""
    return wide - ts_delay(wide, window)


def ts_sum(wide: Wide, window: int) -> Wide:
    _require_window(window)
    return wide.rolling(window, min_periods=window).sum()


def ts_mean(wide: Wide, window: int) -> Wide:
    _require_window(window)
    return wide.rolling(window, min_periods=window).mean()


def ts_std(wide: Wide, window: int) -> Wide:
    """滚动标准差，**总体口径 ddof=0**（对齐移植来源 vnpy 的 `np.nanstd(s, ddof=0)`）。

    ddof 不是无关紧要的细节：在 alpha18（std 与价差、相关系数**相加**）和
    alpha21（mean±std 当作**比较阈值**）里，sqrt(n/(n-1)) 的常数偏差会真的改变
    最终排序与分支归属。只有当 std 整体被 cs_rank 包住时常数缩放才无害。

    注意与截面的 `cross_section.cs_std` 口径不同（那边是 ddof=1）——
    移植来源本身两者就不一致（polars `.std()` 默认 ddof=1），此处各自对齐。
    """
    _require_window(window, 2)
    return wide.rolling(window, min_periods=window).std(ddof=0)


def ts_min(wide: Wide, window: int) -> Wide:
    _require_window(window)
    return wide.rolling(window, min_periods=window).min()


def ts_max(wide: Wide, window: int) -> Wide:
    _require_window(window)
    return wide.rolling(window, min_periods=window).max()


def ts_argmax(wide: Wide, window: int) -> Wide:
    """窗口内最大值所在位置（1..window，越大表示极值越靠后/越近）。"""
    _require_window(window)
    return wide.rolling(window, min_periods=window).apply(
        lambda y: float(np.argmax(y) + 1), raw=True
    )


def ts_argmin(wide: Wide, window: int) -> Wide:
    """窗口内最小值所在位置（1..window）。"""
    _require_window(window)
    return wide.rolling(window, min_periods=window).apply(
        lambda y: float(np.argmin(y) + 1), raw=True
    )


def ts_rank(wide: Wide, window: int) -> Wide:
    """当前值在过去 window 期中的分位排名，值域 (0, 1]。"""
    _require_window(window)
    return wide.rolling(window, min_periods=window).rank(pct=True)


def ts_corr(a: Wide, b: Wide, window: int) -> Wide:
    """滚动 Pearson 相关；任一序列窗口标准差≈0 → NaN（口径同 operators.rolling_corr）。"""
    _require_window(window, 2)
    aligned = b.reindex(index=a.index, columns=a.columns)
    corr = a.rolling(window, min_periods=window).corr(aligned, pairwise=False)
    degenerate = (ts_std(a, window).abs() < STD_ATOL) | (
        ts_std(aligned, window).abs() < STD_ATOL
    )
    return corr.mask(degenerate).replace([np.inf, -np.inf], np.nan)


def ts_cov(a: Wide, b: Wide, window: int) -> Wide:
    """滚动协方差，**总体口径 ddof=0**（与 `ts_std` 一致）。

    移植来源把 ts_cov 定义为 `ts_corr · ts_std · ts_std`，两个 std 都是 ddof=0，
    等价于总体协方差；pandas 的 `.cov()` 默认 ddof=1，故按 (n-1)/n 折算。
    """
    _require_window(window, 2)
    aligned = b.reindex(index=a.index, columns=a.columns)
    sample_cov = a.rolling(window, min_periods=window).cov(aligned, pairwise=False)
    population_cov = sample_cov * ((window - 1) / window)
    return population_cov.replace([np.inf, -np.inf], np.nan)


def ts_decay_linear(wide: Wide, window: int) -> Wide:
    """线性衰减加权均值：权重 1..window 归一化，最近一根 bar 权重最大。"""
    _require_window(window)
    return wma(wide, window)


def ts_product(wide: Wide, window: int) -> Wide:
    """窗口内连乘。"""
    _require_window(window)
    return wide.rolling(window, min_periods=window).apply(np.prod, raw=True)


def ts_greater(a: Wide, b: Wide) -> Wide:
    """逐元素取较大者。"""
    return a.where(a >= b, b).mask(a.isna() | b.isna())


def ts_less(a: Wide, b: Wide) -> Wide:
    """逐元素取较小者。"""
    return a.where(a <= b, b).mask(a.isna() | b.isna())


# ── 数学/逻辑算子 ─────────────────────────────────────────────────


def sign(wide: Wide) -> Wide:
    """符号函数：正 → 1，负 → −1，0 → 0，NaN 保持 NaN。"""
    return np.sign(wide).mask(wide.isna())


def log(wide: Wide) -> Wide:
    """自然对数；非正值无定义 → NaN（不产生 −inf，也不静默改成 0）。"""
    return np.log(wide.where(wide > 0.0))


def pow1(base: Wide, exponent: float) -> Wide:
    """保号幂：sign(x)·|x|^e（vnpy pow1 口径，负底数不产生 NaN）。"""
    return sign(base) * base.abs().pow(exponent)


def pow2(base: Wide, exponent: Wide) -> Wide:
    """逐元素幂 base^exponent。

    - base > 0：正常求幂
    - base < 0 且指数为整数：−|base|^exponent
    - 其余（base = 0 / 指数非整数 / 任一为 NaN）：0
      （与 vnpy pow2 一致；0 表示「该处无有效幂」，Alpha101 靠截面排序消化）
    """
    aligned = exponent.reindex(index=base.index, columns=base.columns)
    positive = base.where(base > 0.0).pow(aligned)
    integral = aligned.notna() & (np.floor(aligned) == aligned)
    negative = -(base.abs().where(base < 0.0).pow(aligned)).where(integral)
    return positive.fillna(negative).fillna(0.0)


def quesval(threshold: float, cond: Wide, if_true: Wide | float, if_false: Wide | float) -> Wide:
    """threshold < cond 时取 if_true，否则取 if_false（vnpy quesval 口径）。"""
    mask = cond > threshold
    return _select(mask, cond, if_true, if_false)


def quesval2(threshold: Wide, cond: Wide, if_true: Wide | float, if_false: Wide | float) -> Wide:
    """threshold < cond 时取 if_true，否则取 if_false（阈值也是面板）。"""
    aligned = threshold.reindex(index=cond.index, columns=cond.columns)
    mask = aligned < cond
    return _select(mask, cond, if_true, if_false, extra_nan=aligned)


def lt(a: Wide, b: Wide) -> Wide:
    """a < b 的指示变量（1.0 / 0.0）；任一为 NaN → NaN，不当作 False。"""
    aligned = b.reindex(index=a.index, columns=a.columns)
    return (a < aligned).astype(float).mask(a.isna() | aligned.isna())


def _select(
    mask: Wide,
    cond: Wide,
    if_true: Wide | float,
    if_false: Wide | float,
    extra_nan: Wide | None = None,
) -> Wide:
    """按 mask 在两个分支间取值，并把条件侧的 NaN 传播出去（不静默当 False）。"""
    true_frame = _as_frame(if_true, cond)
    false_frame = _as_frame(if_false, cond)
    out = true_frame.where(mask, false_frame)
    invalid = cond.isna()
    if extra_nan is not None:
        invalid = invalid | extra_nan.isna()
    return out.mask(invalid)


def _as_frame(value: Wide | float, like: Wide) -> Wide:
    """标量 → 与 like 同形的常量面板；面板 → 对齐到 like 的形状。"""
    if isinstance(value, pd.DataFrame):
        return value.reindex(index=like.index, columns=like.columns)
    return pd.DataFrame(float(value), index=like.index, columns=like.columns)


__all__ = [
    "PANEL_EPS",
    "Wide",
    "log",
    "lt",
    "pow1",
    "pow2",
    "quesval",
    "quesval2",
    "sign",
    "ts_argmax",
    "ts_argmin",
    "ts_corr",
    "ts_cov",
    "ts_decay_linear",
    "ts_delay",
    "ts_delta",
    "ts_greater",
    "ts_less",
    "ts_max",
    "ts_mean",
    "ts_min",
    "ts_product",
    "ts_rank",
    "ts_std",
    "ts_sum",
]
