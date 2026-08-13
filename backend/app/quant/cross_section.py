"""
截面算子（Cross-Section Operators）— Wave M-b / M2

「同一时刻、跨标的」的算子。移植自 vnpy `alpha/dataset/cs_function.py`
（**MIT 许可，可移植**），把 polars `over("datetime")` 的窗口语义改写为
pandas 的**宽表行向量**运算。

两种表示，一份实现（DRY）：
  - **宽表** `DataFrame(index=datetime, columns=instrument)` —— 算子的原生形态，
    一行就是一个截面，全部运算沿 axis=1 向量化，是 Alpha101 的计算底座。
  - **长表** `Series(MultiIndex[datetime, instrument])` —— 平台面板约定
    （见 `app/quant/panel.py`），公式引擎的 panel 模式用它。
    `cs_apply()` 负责长↔宽转换，算子本身只写一遍。

共同约定（六个算子一致）：
  1. **有效标的数 < 2 的截面整行返回 NaN。** 一个标的的「截面排名」恒为 1，
     那不是信息，是噪音，必须显式区别于真实排名。
  2. **输入为 NaN 的单元格输出仍为 NaN**，且该标的不参与截面统计
     —— 停牌/新上市标的不该污染其它标的的排名。
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

#: 构成一个有意义截面所需的最少有效标的数
MIN_CROSS_SECTION_NAMES: int = 2

#: 标准差判零阈值（低于此值视为截面无离散度，z-score 无意义）
CS_EPS: float = 1e-12

#: 宽表算子签名：宽表 → 同形宽表
WideOp = Callable[[pd.DataFrame], pd.DataFrame]


# ── 通用掩码 ──────────────────────────────────────────────────────


def _thin_section_mask(wide: pd.DataFrame) -> pd.Series:
    """有效标的数不足的行 → True（这些行整行置 NaN）。"""
    return wide.count(axis=1) < MIN_CROSS_SECTION_NAMES


def _finalize(wide: pd.DataFrame, source: pd.DataFrame) -> pd.DataFrame:
    """统一收尾：薄截面整行置 NaN、原本 NaN 的单元格保持 NaN、清理 ±inf。"""
    out = wide.mask(_thin_section_mask(source), other=np.nan)
    out = out.mask(source.isna(), other=np.nan)
    return out.replace([np.inf, -np.inf], np.nan)


# ── 六个截面算子（宽表形态）────────────────────────────────────────


def cs_rank(wide: pd.DataFrame) -> pd.DataFrame:
    """截面分位排名。

    值域 (0, 1]（Alpha101 通行的 pct rank 口径）：最小者为 1/n，最大者恒为 1。
    并列取平均名次。NaN 标的不计入 n。
    """
    return _finalize(wide.rank(axis=1, pct=True), wide)


def cs_mean(wide: pd.DataFrame) -> pd.DataFrame:
    """截面均值，广播回各标的。"""
    means = wide.mean(axis=1)
    broadcast = pd.DataFrame(
        np.repeat(means.to_numpy(dtype=float)[:, None], wide.shape[1], axis=1),
        index=wide.index,
        columns=wide.columns,
    )
    return _finalize(broadcast, wide)


def cs_std(wide: pd.DataFrame) -> pd.DataFrame:
    """截面标准差（**样本口径 ddof=1**），广播回各标的。

    与时序的 `factor_lib.panel_ops.ts_std`（ddof=0）刻意不同：移植来源本身
    两者就不一致（polars `.std()` 默认 ddof=1，而 ts_std 显式用 `np.nanstd(ddof=0)`），
    此处各自对齐，不强行统一。
    """
    stds = wide.std(axis=1)
    broadcast = pd.DataFrame(
        np.repeat(stds.to_numpy(dtype=float)[:, None], wide.shape[1], axis=1),
        index=wide.index,
        columns=wide.columns,
    )
    return _finalize(broadcast, wide)


def cs_demean(wide: pd.DataFrame) -> pd.DataFrame:
    """截面去均值：x − mean(x)。"""
    return _finalize(wide.sub(wide.mean(axis=1), axis=0), wide)


def cs_zscore(wide: pd.DataFrame) -> pd.DataFrame:
    """截面标准化：(x − mean) / std。std≈0（截面无离散度）→ NaN 而非 ±inf。"""
    std = wide.std(axis=1)
    safe_std = std.where(std.abs() > CS_EPS)
    centered = wide.sub(wide.mean(axis=1), axis=0)
    return _finalize(centered.div(safe_std, axis=0), wide)


def cs_scale(wide: pd.DataFrame) -> pd.DataFrame:
    """截面缩放至 Σ|x| = 1。

    截面全为 0 时 Σ|x| = 0，无法缩放到 1；此时保持 0（与 vnpy `cs_scale` 一致）,
    这是唯一诚实的答案 —— 全 0 因子本就没有分配可言。
    """
    abs_sum = wide.abs().sum(axis=1)
    safe_sum = abs_sum.where(abs_sum > CS_EPS)
    scaled = wide.div(safe_sum, axis=0)
    # 分母为 0 的行：原值必然全 0，直接补 0（不是 NaN，也不是 1/n）。
    # mul(..., axis=0) 是把行掩码沿 index 广播到每一列的写法（直接 & 会按列名对齐）。
    zero_cells = wide.notna().mul(abs_sum <= CS_EPS, axis=0).astype(bool)
    scaled = scaled.mask(zero_cells, other=0.0)
    return _finalize(scaled, wide)


#: token 名 → 宽表算子（公式引擎与 Alpha101 共用的单一真源）
CS_FUNCTIONS: dict[str, WideOp] = {
    "CS_RANK": cs_rank,
    "CS_ZSCORE": cs_zscore,
    "CS_DEMEAN": cs_demean,
    "CS_SCALE": cs_scale,
    "CS_MEAN": cs_mean,
    "CS_STD": cs_std,
}


# ── 长表 ↔ 宽表适配 ───────────────────────────────────────────────


def to_wide(series: pd.Series) -> pd.DataFrame:
    """长表 Series(MultiIndex[datetime, instrument]) → 宽表。"""
    validate_panel_index(series.index)
    return series.unstack("instrument").astype(float)


def to_long(wide: pd.DataFrame, index: pd.MultiIndex) -> pd.Series:
    """宽表 → 长表，并对齐到给定的面板索引（缺失位置为 NaN）。"""
    stacked = wide.stack()
    stacked.index = stacked.index.set_names(["datetime", "instrument"])
    return stacked.reorder_levels(["datetime", "instrument"]).reindex(index).astype(float)


def cs_apply(series: pd.Series, op: WideOp) -> pd.Series:
    """在长表 Series 上执行某个截面算子，返回同索引长表 Series。"""
    return to_long(op(to_wide(series)), series.index)


def validate_panel_index(index: pd.Index) -> None:
    """面板索引必须是 (datetime, instrument) 双层索引 —— 边界快速失败。"""
    if not isinstance(index, pd.MultiIndex) or index.nlevels != 2:
        raise ValueError(
            f"截面运算需要 (datetime, instrument) 双层索引，实得 {type(index).__name__}"
        )
    if list(index.names) != ["datetime", "instrument"]:
        raise ValueError(
            f"面板索引层名必须为 ['datetime', 'instrument']，实得 {list(index.names)}"
        )


__all__ = [
    "CS_EPS",
    "CS_FUNCTIONS",
    "MIN_CROSS_SECTION_NAMES",
    "cs_apply",
    "cs_demean",
    "cs_mean",
    "cs_rank",
    "cs_scale",
    "cs_std",
    "cs_zscore",
    "to_long",
    "to_wide",
    "validate_panel_index",
]
