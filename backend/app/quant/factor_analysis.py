"""
因子分析模块

计算单标的历史因子值与未来收益率的 IC（信息系数）分析。
支持多种内置因子，可用于评估因子的预测能力。

指标说明：
  IC   = 因子值与前瞻收益率的 Pearson 相关系数
  IR   = IC 均值 / IC 标准差（信息比率，越高越稳定）
  IC>0% = IC 为正的占比（方向准确率）
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.quant.indicators import (
    rsi, sma, ema, bollinger_bands, atr, obv, macd, adx, mfi,
)


# ── 因子计算 ──────────────────────────────────────────────────────

def _compute_factor(df: pd.DataFrame, factor_name: str) -> pd.Series:
    """根据因子名计算对应的因子值序列。"""
    close = df["close"]
    high  = df["high"]
    low   = df["low"]
    vol   = df["volume"]

    if factor_name == "momentum_20":
        # 20日动量：当前收盘 / 20日前收盘 - 1
        return close.pct_change(20)

    elif factor_name == "momentum_5":
        return close.pct_change(5)

    elif factor_name == "rsi_14":
        return rsi(df, 14)

    elif factor_name == "rsi_21":
        return rsi(df, 21)

    elif factor_name == "macd_hist":
        _, _, hist = macd(df)
        return hist

    elif factor_name == "bb_position":
        # 布林带位置：(close - lower) / (upper - lower)，0=下轨，1=上轨
        upper, _, lower = bollinger_bands(df, 20)
        band_width = (upper - lower).replace(0, np.nan)
        return (close - lower) / band_width

    elif factor_name == "atr_ratio":
        # ATR 相对值：ATR / close（波动率因子）
        atr_val = atr(df, 14)
        return atr_val / close

    elif factor_name == "volume_change":
        # 成交量变化率：当前成交量 / 20日均量 - 1
        avg_vol = vol.rolling(20).mean()
        return vol / avg_vol.replace(0, np.nan) - 1

    elif factor_name == "obv_momentum":
        # OBV 20日动量
        obv_val = obv(df)
        return obv_val.pct_change(20)

    elif factor_name == "price_to_sma20":
        # 价格相对均线偏离：close / SMA20 - 1
        ma20 = sma(df, 20)
        return close / ma20.replace(0, np.nan) - 1

    elif factor_name == "adx_strength":
        return adx(df, 14)

    elif factor_name == "mfi_14":
        return mfi(df, 14)

    else:
        raise ValueError(f"Unknown factor: {factor_name}")


# ── IC 分析核心 ───────────────────────────────────────────────────

@dataclass
class FactorAnalysisResult:
    factor_name: str
    forward_periods: list[int]
    # 因子值序列
    factor_series: list[dict]          # [{time, value}]
    # 各前瞻期 IC 序列
    ic_series: dict[str, list[dict]]   # {period_str: [{time, ic}]}
    # 各前瞻期统计
    ic_mean:         dict[str, float]
    ic_std:          dict[str, float]
    ic_ir:           dict[str, float]
    ic_positive_rate: dict[str, float]
    ic_abs_mean:     dict[str, float]
    # 累计 IC 序列（用于图表展示）
    cumulative_ic:   dict[str, list[dict]]  # {period_str: [{time, cum_ic}]}
    # 分位数收益分析（factor 按 5 分位，各分位平均前瞻收益）
    quantile_returns: dict[str, list[float]]  # {period_str: [Q1,Q2,Q3,Q4,Q5]}


def analyze_factor(
    df: pd.DataFrame,
    factor_name: str,
    forward_periods: list[int] | None = None,
    factor_override: pd.Series | None = None,
) -> FactorAnalysisResult:
    """
    计算因子与前瞻收益的 IC 分析。

    Parameters
    ----------
    df            : OHLCV DataFrame，index 为时间字符串
    factor_name   : 因子名称（见 _compute_factor）；当传入 factor_override 时仅作标签
    forward_periods: 前瞻期列表，默认 [5, 10, 20]
    factor_override: 预先计算好的因子值序列（用于公式因子），传入时跳过内置计算
    """
    if forward_periods is None:
        forward_periods = [5, 10, 20]

    close = df["close"]

    # 1. 计算因子值（公式因子直接使用预算好的序列）
    factor = factor_override if factor_override is not None else _compute_factor(df, factor_name)

    # 2. 计算各前瞻期收益率
    fwd_returns: dict[str, pd.Series] = {}
    for p in forward_periods:
        fwd_returns[str(p)] = close.pct_change(p).shift(-p)

    # 3. 对齐并清洗
    result_ic_series:    dict[str, list[dict]] = {}
    result_ic_mean:      dict[str, float] = {}
    result_ic_std:       dict[str, float] = {}
    result_ic_ir:        dict[str, float] = {}
    result_ic_pos_rate:  dict[str, float] = {}
    result_ic_abs_mean:  dict[str, float] = {}
    result_cum_ic:       dict[str, list[dict]] = {}
    result_qtl_ret:      dict[str, list[float]] = {}

    for p in forward_periods:
        key = str(p)
        fwd = fwd_returns[key]

        # Rolling 30-bar IC (Pearson correlation in rolling window)
        window = 30
        ic_roll: list[dict] = []

        valid_mask = factor.notna() & fwd.notna()
        f_clean = factor[valid_mask]
        r_clean = fwd[valid_mask]

        if len(f_clean) < window + 2:
            # Not enough data — fill with zeros
            result_ic_series[key]    = []
            result_ic_mean[key]      = float("nan")
            result_ic_std[key]       = float("nan")
            result_ic_ir[key]        = float("nan")
            result_ic_pos_rate[key]  = float("nan")
            result_ic_abs_mean[key]  = float("nan")
            result_cum_ic[key]       = []
            result_qtl_ret[key]      = []
            continue

        # Rolling IC
        f_arr = f_clean.values
        r_arr = r_clean.values
        times = f_clean.index.tolist()

        ic_values: list[float] = []
        ic_times:  list[str]   = []
        for i in range(window - 1, len(f_arr)):
            f_window = f_arr[i - window + 1 : i + 1]
            r_window = r_arr[i - window + 1 : i + 1]
            if np.std(f_window) < 1e-12 or np.std(r_window) < 1e-12:
                continue
            ic = float(np.corrcoef(f_window, r_window)[0, 1])
            if np.isnan(ic):
                continue
            ic_values.append(ic)
            ic_times.append(times[i])

        ic_arr = np.array(ic_values)

        result_ic_series[key] = [
            {"time": t, "ic": round(float(v), 4)}
            for t, v in zip(ic_times, ic_arr)
        ]

        result_ic_mean[key]     = round(float(np.mean(ic_arr)), 4) if len(ic_arr) else float("nan")
        result_ic_std[key]      = round(float(np.std(ic_arr)), 4)  if len(ic_arr) else float("nan")
        ic_ir_val = (float(np.mean(ic_arr)) / float(np.std(ic_arr))) if (len(ic_arr) and np.std(ic_arr) > 0) else float("nan")
        result_ic_ir[key]       = round(ic_ir_val, 4)
        result_ic_pos_rate[key] = round(float(np.mean(ic_arr > 0)), 4) if len(ic_arr) else float("nan")
        result_ic_abs_mean[key] = round(float(np.mean(np.abs(ic_arr))), 4) if len(ic_arr) else float("nan")

        # Cumulative IC
        cum = np.cumsum(ic_arr)
        result_cum_ic[key] = [
            {"time": t, "cum_ic": round(float(v), 4)}
            for t, v in zip(ic_times, cum)
        ]

        # Quantile analysis (5 quintiles)
        combo = pd.DataFrame({"f": f_clean, "r": r_clean}).dropna()
        if len(combo) >= 10:
            combo["q"] = pd.qcut(combo["f"], q=5, labels=False, duplicates="drop")
            qtl_means = combo.groupby("q")["r"].mean().reindex(range(5), fill_value=0.0)
            result_qtl_ret[key] = [round(float(v) * 100, 3) for v in qtl_means.values]
        else:
            result_qtl_ret[key] = [0.0, 0.0, 0.0, 0.0, 0.0]

    # 4. Factor series for chart
    factor_chart = [
        {"time": str(t), "value": round(float(v), 4)}
        for t, v in factor.items()
        if not (isinstance(v, float) and np.isnan(v))
    ]

    return FactorAnalysisResult(
        factor_name=factor_name,
        forward_periods=forward_periods,
        factor_series=factor_chart,
        ic_series=result_ic_series,
        ic_mean=result_ic_mean,
        ic_std=result_ic_std,
        ic_ir=result_ic_ir,
        ic_positive_rate=result_ic_pos_rate,
        ic_abs_mean=result_ic_abs_mean,
        cumulative_ic=result_cum_ic,
        quantile_returns=result_qtl_ret,
    )


# ── 可用因子列表 ──────────────────────────────────────────────────

AVAILABLE_FACTORS = [
    {"name": "momentum_20",  "label": "20日动量",       "group": "动量"},
    {"name": "momentum_5",   "label": "5日动量",        "group": "动量"},
    {"name": "rsi_14",       "label": "RSI(14)",        "group": "动量"},
    {"name": "rsi_21",       "label": "RSI(21)",        "group": "动量"},
    {"name": "macd_hist",    "label": "MACD 柱",        "group": "趋势"},
    {"name": "bb_position",  "label": "布林带位置",      "group": "均值回归"},
    {"name": "atr_ratio",    "label": "ATR 比率（波动）", "group": "波动率"},
    {"name": "volume_change","label": "成交量变化率",    "group": "成交量"},
    {"name": "obv_momentum", "label": "OBV 动量",       "group": "成交量"},
    {"name": "price_to_sma20","label": "价格/SMA20偏离", "group": "均值回归"},
    {"name": "adx_strength", "label": "ADX 趋势强度",   "group": "趋势"},
    {"name": "mfi_14",       "label": "MFI(14) 资金流",  "group": "成交量"},
]
