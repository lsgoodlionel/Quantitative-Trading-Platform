"""
声明式因子库（B2）— 配置 → Alpha158 式因子表达式生成

参照 qlib `contrib/data/loader.py` 的 `Alpha158DL.get_feature_config` **设计思想**
（非复制代码）：以「字段 × 算子 × 窗口」的配置组合，程序化生成数百个标准化因子。

与 qlib 差异：
  - qlib 生成字符串表达式交由其表达式引擎解析；这里直接生成 `compute(df)->Series`
    可调用体，用本平台的 operators.py 原语求值，无需引入 qlib 依赖。
  - 全部因子以 close 归一化（除以现价再减 1 / 直接比率），使其在**跨标的横截面**上
    可比，天然适配下游横截面 IC 排行与横截面处理流水线。

面板约定沿用平台惯例：单标的 OHLCV 帧（index 为时间字符串，列含 open/high/low/close/volume）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

from app.quant.factor_lib.operators import (
    EPS,
    ema,
    rolling_corr,
    rolling_idxmax,
    rolling_idxmin,
    rolling_mad,
    rolling_quantile,
    rolling_rank,
    rolling_resi,
    rolling_rsquare,
    rolling_slope,
    wma,
)

# 默认滚动窗口（对齐 Alpha158 的 [5,10,20,30,60]）
DEFAULT_WINDOWS: tuple[int, ...] = (5, 10, 20, 30, 60)
# 硬上限：一次生成的因子数（防止 universe × 因子数 计算爆炸）
MAX_FACTORS: int = 240

FactorFn = Callable[[pd.DataFrame], pd.Series]


@dataclass(frozen=True)
class FactorSpec:
    """单个因子的定义。compute 不参与相等比较/序列化。"""

    name: str
    label: str
    group: str
    window: int
    expr: str
    compute: FactorFn = field(compare=False, repr=False)

    def to_meta(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "group": self.group,
            "window": self.window,
            "expr": self.expr,
        }


# ── 字段访问器 ────────────────────────────────────────────────────

def _c(df: pd.DataFrame) -> pd.Series:
    return df["close"].astype(float)


def _o(df: pd.DataFrame) -> pd.Series:
    return df["open"].astype(float)


def _h(df: pd.DataFrame) -> pd.Series:
    return df["high"].astype(float)


def _l(df: pd.DataFrame) -> pd.Series:
    return df["low"].astype(float)


def _v(df: pd.DataFrame) -> pd.Series:
    return df["volume"].astype(float)


def _safe_div(num: pd.Series, den: pd.Series) -> pd.Series:
    return num / den.replace(0.0, np.nan)


# ── 复杂族的具名计算函数（保持 <50 行 / 避免巨型 lambda）──────────

def _corr_pv(df: pd.DataFrame, w: int) -> pd.Series:
    """价量相关：Corr(close, log(volume+1), w)。"""
    return rolling_corr(_c(df), np.log1p(_v(df)), w)


def _cord_pv(df: pd.DataFrame, w: int) -> pd.Series:
    """收益-量变相关：Corr(close/Ref(close,1), log(volume/Ref(volume,1)+1), w)。"""
    price_ratio = _safe_div(_c(df), _c(df).shift(1))
    vol_ratio = _safe_div(_v(df), _v(df).shift(1))
    return rolling_corr(price_ratio, np.log1p(vol_ratio.clip(lower=0.0)), w)


def _rsv(df: pd.DataFrame, w: int) -> pd.Series:
    """随机指标 RSV：(close − minLow) / (maxHigh − minLow)。"""
    low_min = _l(df).rolling(w, min_periods=w).min()
    high_max = _h(df).rolling(w, min_periods=w).max()
    return (_c(df) - low_min) / (high_max - low_min + EPS)


# ── 家族描述表（滚动族，DRY 生成）─────────────────────────────────

@dataclass(frozen=True)
class _Family:
    code: str                                 # 因子名前缀，如 "MA"
    group: str                                # 分组
    label: str                                # 中文标签（不含窗口）
    expr: str                                 # 表达式模板（{w} 占位窗口）
    fn: Callable[[pd.DataFrame, int], pd.Series]


_FAMILIES: tuple[_Family, ...] = (
    _Family("ROC",  "动量", "价格动量",     "$close/Ref($close,{w})-1",
            lambda df, w: _safe_div(_c(df), _c(df).shift(w)) - 1.0),
    _Family("MA",   "均线", "均线偏离",     "Mean($close,{w})/$close-1",
            lambda df, w: _safe_div(_c(df).rolling(w, min_periods=w).mean(), _c(df)) - 1.0),
    _Family("WMA",  "均线", "加权均线偏离", "WMA($close,{w})/$close-1",
            lambda df, w: _safe_div(wma(_c(df), w), _c(df)) - 1.0),
    _Family("EMA",  "均线", "指数均线偏离", "EMA($close,{w})/$close-1",
            lambda df, w: _safe_div(ema(_c(df), w), _c(df)) - 1.0),
    _Family("STD",  "波动", "收盘波动率",   "Std($close,{w})/$close",
            lambda df, w: _safe_div(_c(df).rolling(w, min_periods=w).std(), _c(df))),
    _Family("MAD",  "波动", "平均绝对偏差", "Mad($close,{w})/$close",
            lambda df, w: _safe_div(rolling_mad(_c(df), w), _c(df))),
    _Family("BETA", "回归", "回归斜率",     "Slope($close,{w})/$close",
            lambda df, w: _safe_div(rolling_slope(_c(df), w), _c(df))),
    _Family("RSQR", "回归", "回归拟合度",   "Rsquare($close,{w})",
            lambda df, w: rolling_rsquare(_c(df), w)),
    _Family("RESI", "回归", "回归残差",     "Resi($close,{w})/$close",
            lambda df, w: _safe_div(rolling_resi(_c(df), w), _c(df))),
    _Family("MAX",  "极值", "区间最高偏离", "Max($high,{w})/$close-1",
            lambda df, w: _safe_div(_h(df).rolling(w, min_periods=w).max(), _c(df)) - 1.0),
    _Family("MIN",  "极值", "区间最低偏离", "Min($low,{w})/$close-1",
            lambda df, w: _safe_div(_l(df).rolling(w, min_periods=w).min(), _c(df)) - 1.0),
    _Family("QTLU", "分位", "上分位偏离",   "Quantile($close,{w},0.8)/$close-1",
            lambda df, w: _safe_div(rolling_quantile(_c(df), w, 0.8), _c(df)) - 1.0),
    _Family("QTLD", "分位", "下分位偏离",   "Quantile($close,{w},0.2)/$close-1",
            lambda df, w: _safe_div(rolling_quantile(_c(df), w, 0.2), _c(df)) - 1.0),
    _Family("RANK", "位置", "收盘分位排名", "Rank($close,{w})",
            lambda df, w: rolling_rank(_c(df), w)),
    _Family("RSV",  "位置", "随机指标",     "($close-Min($low,{w}))/(Max($high,{w})-Min($low,{w}))",
            _rsv),
    _Family("IMAX", "位置", "最高价位置",   "IdxMax($high,{w})/{w}",
            lambda df, w: rolling_idxmax(_h(df), w) / w),
    _Family("IMIN", "位置", "最低价位置",   "IdxMin($low,{w})/{w}",
            lambda df, w: rolling_idxmin(_l(df), w) / w),
    _Family("IMXD", "位置", "高低点间距",   "(IdxMax($high,{w})-IdxMin($low,{w}))/{w}",
            lambda df, w: (rolling_idxmax(_h(df), w) - rolling_idxmin(_l(df), w)) / w),
    _Family("CORR", "量价", "价量相关",     "Corr($close,Log($volume+1),{w})",
            _corr_pv),
    _Family("CORD", "量价", "收益量变相关", "Corr($close/Ref($close,1),Log($volume/Ref($volume,1)+1),{w})",
            _cord_pv),
    _Family("CNTP", "涨跌", "上涨占比",     "Mean($close>Ref($close,1),{w})",
            lambda df, w: (_c(df) > _c(df).shift(1)).astype(float).rolling(w, min_periods=w).mean()),
    _Family("CNTN", "涨跌", "下跌占比",     "Mean($close<Ref($close,1),{w})",
            lambda df, w: (_c(df) < _c(df).shift(1)).astype(float).rolling(w, min_periods=w).mean()),
    _Family("VMA",  "成交量", "量均比",     "Mean($volume,{w})/$volume-1",
            lambda df, w: _v(df).rolling(w, min_periods=w).mean() / (_v(df) + EPS) - 1.0),
    _Family("VSTD", "成交量", "量波动率",   "Std($volume,{w})/$volume",
            lambda df, w: _v(df).rolling(w, min_periods=w).std() / (_v(df) + EPS)),
)


# ── 无窗口 K 线形态族（KBAR）──────────────────────────────────────

def _kbar_specs() -> list[FactorSpec]:
    open_ = lambda df: _o(df) + EPS  # noqa: E731 — 防除零
    hl = lambda df: (_h(df) - _l(df)) + EPS  # noqa: E731
    families: list[tuple[str, str, str, FactorFn]] = [
        ("KMID", "实体幅度", "($close-$open)/$open",
         lambda df: (_c(df) - _o(df)) / open_(df)),
        ("KLEN", "K线长度", "($high-$low)/$open",
         lambda df: (_h(df) - _l(df)) / open_(df)),
        ("KMID2", "实体占比", "($close-$open)/($high-$low)",
         lambda df: (_c(df) - _o(df)) / hl(df)),
        ("KUP", "上影线", "($high-Greater($open,$close))/$open",
         lambda df: (_h(df) - np.maximum(_o(df), _c(df))) / open_(df)),
        ("KLOW", "下影线", "(Less($open,$close)-$low)/$open",
         lambda df: (np.minimum(_o(df), _c(df)) - _l(df)) / open_(df)),
        ("KSFT", "重心偏移", "(2*$close-$high-$low)/$open",
         lambda df: (2 * _c(df) - _h(df) - _l(df)) / open_(df)),
    ]
    return [
        FactorSpec(name=code, label=label, group="K线", window=0, expr=expr, compute=fn)
        for code, label, expr, fn in families
    ]


# ── 生成入口 ──────────────────────────────────────────────────────

GROUP_DESCRIPTIONS: dict[str, str] = {
    "K线":   "单根 K 线形态（实体、影线、重心），刻画即时买卖压力",
    "动量":  "不同窗口的价格变化率，捕捉趋势延续",
    "均线":  "价格相对（加权/指数）均线的偏离，衡量趋势位置",
    "波动":  "收盘价的滚动波动率与离散度",
    "回归":  "滚动一元回归的斜率/拟合度/残差，量化趋势线性程度",
    "极值":  "区间最高/最低价相对现价的偏离",
    "分位":  "收盘价在窗口分布中的分位偏离",
    "位置":  "现价在窗口内的相对位置与极值出现的时点",
    "量价":  "价格与成交量的滚动相关性",
    "涨跌":  "窗口内上涨/下跌天数占比",
    "成交量": "成交量的滚动均值/波动相对现量",
}


def generate_factor_library(
    windows: tuple[int, ...] | None = None,
    groups: tuple[str, ...] | None = None,
) -> list[FactorSpec]:
    """按配置生成因子库。

    Parameters
    ----------
    windows : 滚动窗口集合，默认 DEFAULT_WINDOWS
    groups  : 仅保留这些分组（None = 全部）
    """
    win = tuple(windows) if windows else DEFAULT_WINDOWS
    if any((not isinstance(w, int) or w < 2) for w in win):
        raise ValueError(f"窗口集合必须均为 ≥ 2 的整数，实得 {win}")

    specs: list[FactorSpec] = []
    if groups is None or "K线" in groups:
        specs.extend(_kbar_specs())

    for fam in _FAMILIES:
        if groups is not None and fam.group not in groups:
            continue
        for w in win:
            specs.append(
                FactorSpec(
                    name=f"{fam.code}{w}",
                    label=f"{w}日{fam.label}",
                    group=fam.group,
                    window=w,
                    expr=fam.expr.format(w=w),
                    compute=(lambda df, _fn=fam.fn, _w=w: _fn(df, _w)),
                )
            )

    if len(specs) > MAX_FACTORS:
        raise ValueError(
            f"生成因子数 {len(specs)} 超过上限 {MAX_FACTORS}，请缩小 windows/groups 范围"
        )
    return specs


def build_feature_fn(specs: list[FactorSpec]) -> FactorFn:
    """把因子列表编译为 单标的 OHLCV → 多列因子帧 的映射（供 bars_to_panel）。"""
    if not specs:
        raise ValueError("因子列表为空：请检查分组/窗口过滤条件是否匹配到因子")

    def feature_fn(ohlcv: pd.DataFrame) -> pd.DataFrame:
        # 一次性 concat 所有列，避免逐列 insert 造成 DataFrame 碎片化
        columns = {
            spec.name: pd.Series(spec.compute(ohlcv), index=ohlcv.index).astype(float)
            for spec in specs
        }
        out = pd.concat(columns, axis=1)
        return out.replace([np.inf, -np.inf], np.nan)

    return feature_fn


def library_group_meta(specs: list[FactorSpec]) -> list[dict]:
    """按分组汇总（name/label/description/count），保持插入顺序。"""
    order: list[str] = []
    counts: dict[str, int] = {}
    for spec in specs:
        if spec.group not in counts:
            counts[spec.group] = 0
            order.append(spec.group)
        counts[spec.group] += 1
    return [
        {
            "name": g,
            "label": g,
            "description": GROUP_DESCRIPTIONS.get(g, ""),
            "count": counts[g],
        }
        for g in order
    ]
