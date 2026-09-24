"""
因子面板求值原语（遗传挖掘 / 自动因子循环共用）

从 `genetic.py` 抽出的两件事：

  1. RPN token → `(datetime, instrument)` 单列因子面板（按公式形态自动选择
     「逐标的」或「整块面板」求值路径）
  2. 横截面 IC / RankIC / ICIR 统计（复用 `factor_lib.ranking` 的口径）

抽出来的理由不是「代码复用」而是**口径统一**：V3 自动因子循环要在两段不相交的
日期区间上分别打分（样本内 / 样本外），两个数字只有出自同一把尺子才可比。
把求值逻辑留在 `genetic.py` 里当私有函数，循环那边就只能复制一份 ——
复制品迟早会漂移，而漂移后的「样本外 IC」是个看不出问题的假数字。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.quant.formula_factor import (
    evaluate_formula,
    evaluate_formula_panel,
    formula_requires_panel,
)

#: 单日横截面 IC 所需最少标的数（与 factor_lib/ranking 口径一致）
MIN_NAMES_FOR_IC = 3


def ohlcv_to_panel(ohlcv_by_symbol: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """每标的 OHLCV 帧 → `(datetime, instrument)` 面板（截面求值的输入形态）。"""
    parts: list[pd.DataFrame] = []
    for symbol, frame in ohlcv_by_symbol.items():
        df = frame.copy()
        df.index = pd.Index(df.index, name="datetime")
        df["instrument"] = symbol
        parts.append(df.set_index("instrument", append=True))
    return pd.concat(parts).sort_index()


def build_factor_panel(
    ohlcv_by_symbol: dict[str, pd.DataFrame],
    tokens: list[str],
    panel: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    执行 RPN 公式，返回 `(datetime, instrument)` 单列（`factor`）因子面板。

    含 `CS_*` 截面算子的公式走整块面板求值，否则逐标的求值。
    `panel` 是可选的预构建面板缓存 —— 同一批 OHLCV 上评估上千条公式时，
    重复拼装面板是纯粹的浪费。
    """
    if not formula_requires_panel(tokens):
        return _per_symbol_factor_panel(ohlcv_by_symbol, tokens)
    frame = panel if panel is not None else ohlcv_to_panel(ohlcv_by_symbol)
    return evaluate_formula_panel(frame, tokens).astype(float).to_frame("factor")


def _per_symbol_factor_panel(
    ohlcv_by_symbol: dict[str, pd.DataFrame], tokens: list[str]
) -> pd.DataFrame:
    """逐标的执行 RPN 公式，拼为 `(datetime, instrument)` 单列因子面板。"""
    parts: list[pd.DataFrame] = []
    for symbol, frame in ohlcv_by_symbol.items():
        series = evaluate_formula(frame, tokens).astype(float)
        df = series.to_frame("factor")
        df.index.name = "datetime"
        df["instrument"] = symbol
        parts.append(df.set_index("instrument", append=True))
    return pd.concat(parts).sort_index()


def cross_sectional_ic_stats(
    factor: pd.Series, forward_return: pd.Series
) -> tuple[float, float, float]:
    """复用 `factor_lib.ranking` 的横截面 IC 口径，返回 `(ic_mean, rank_ic_mean, icir)`。"""
    from app.quant.factor_lib.ranking import _cross_sectional_ic

    ic_arr, rank_arr = _cross_sectional_ic(factor, forward_return, MIN_NAMES_FOR_IC)
    if ic_arr.size == 0:
        return float("nan"), float("nan"), float("nan")
    ic_mean = float(np.mean(ic_arr))
    ic_std = float(np.std(ic_arr))
    icir = ic_mean / ic_std if ic_std > 1e-9 else float("nan")
    rank_ic_mean = float(np.mean(rank_arr)) if rank_arr.size else float("nan")
    return ic_mean, rank_ic_mean, icir
