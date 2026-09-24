"""
Alpha101 因子集（M3）— WorldQuant「101 Formulaic Alphas」的面板实现

移植自 vnpy `alpha/dataset/datasets/alpha_101.py`（**MIT，可移植**），
算子层见 `panel_ops.py`（时序/数学）与 `app/quant/cross_section.py`（截面）。

**这些 alpha 绝大多数是截面型的**（`cs_rank` / `cs_scale`），装不进
`FactorSpec.compute` 的单标的签名，因此走 `FactorSpec.compute_panel`：
入参是 (datetime, instrument) 面板，出参是同索引的因子序列。

诚实边界（见 `SKIPPED_ALPHAS`）：
  - 平台面板只有 OHLCV。用到 `vwap` / `cap` / `IndNeutralize` 的 alpha **一律跳过**，
    不用 (high+low+close)/3 之类的近似顶替 —— 一个「名字叫 alpha42 但算法不是
    alpha42」的因子，比没有这个因子危险得多。
  - 每个实现都与 `alpha101_exprs.ALPHA_EXPRESSIONS` 里的原始表达式逐算子对应，
    该表随因子一起暴露在 `FactorSpec.expr` 上，便于事后审计。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.quant.cross_section import cs_rank, cs_scale, to_long, to_wide
from app.quant.factor_lib.panel_ops import (
    Wide,
    log,
    lt,
    pow1,
    pow2,
    quesval,
    quesval2,
    sign,
    ts_argmax,
    ts_corr,
    ts_cov,
    ts_decay_linear,
    ts_delay,
    ts_delta,
    ts_less,
    ts_max,
    ts_mean,
    ts_min,
    ts_product,
    ts_rank,
    ts_std,
    ts_sum,
)

#: 因子库中的分组名
ALPHA101_GROUP = "Alpha101"

#: 未实现的 alpha 及原因（报告与前端都从这里取，避免口径漂移）
SKIPPED_ALPHAS: dict[str, str] = {
    **{
        f"alpha{n}": "依赖 vwap 字段，平台面板只有 OHLCV，不做近似顶替"
        for n in (
            5, 11, 25, 27, 32, 36, 41, 42, 47, 50, 57, 61, 62, 64, 65, 66,
            71, 72, 73, 74, 75, 77, 78, 81, 83, 84, 86, 94, 96, 98,
        )
    },
    **{
        f"alpha{n}": "原式含 IndNeutralize（行业中性化），平台无行业分类数据"
        for n in (
            48, 58, 59, 63, 67, 69, 70, 76, 79, 80, 82, 87, 89, 90, 91, 93, 97, 100,
        )
    },
    "alpha56": "原式含 cap（市值）字段，平台无基本面数据",
}


# ── 字段视图 ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class Fields:
    """面板的宽表字段视图（一次 unstack，供整条 alpha 表达式复用）。"""

    open_: Wide
    high: Wide
    low: Wide
    close: Wide
    volume: Wide
    returns: Wide

    def adv(self, window: int) -> Wide:
        """平均日成交量 adv{d}（Alpha101 原文记号）。"""
        return ts_mean(self.volume, window)


def build_fields(panel: pd.DataFrame) -> Fields:
    """(datetime, instrument) 面板 → 宽表字段视图。缺列快速失败。"""
    missing = [c for c in ("open", "high", "low", "close", "volume") if c not in panel.columns]
    if missing:
        raise ValueError(f"Alpha101 需要完整 OHLCV，面板缺少列: {', '.join(missing)}")

    ordered = panel.sort_index()
    close = to_wide(ordered["close"])
    return Fields(
        open_=to_wide(ordered["open"]),
        high=to_wide(ordered["high"]),
        low=to_wide(ordered["low"]),
        close=close,
        volume=to_wide(ordered["volume"]),
        returns=_safe_div(close, ts_delay(close, 1)) - 1.0,
    )


def _safe_div(num: Wide, den: Wide) -> Wide:
    """除法：分母为 0 → NaN（绝不产生 ±inf 后再被下游算子放大）。"""
    return num / den.replace(0.0, np.nan)


AlphaFn = Callable[[Fields], Wide]


# ── alpha 实现（逐条对应 alpha101_exprs.ALPHA_EXPRESSIONS）─────────


def _alpha1(f: Fields) -> Wide:
    base = quesval(0.0, f.returns, f.close, ts_std(f.returns, 20))
    return cs_rank(ts_argmax(pow1(base, 2.0), 5)) - 0.5


def _alpha2(f: Fields) -> Wide:
    left = cs_rank(ts_delta(log(f.volume), 2))
    right = cs_rank(_safe_div(f.close - f.open_, f.open_))
    return -1.0 * ts_corr(left, right, 6)


def _alpha3(f: Fields) -> Wide:
    return -1.0 * ts_corr(cs_rank(f.open_), cs_rank(f.volume), 10)


def _alpha4(f: Fields) -> Wide:
    return -1.0 * ts_rank(cs_rank(f.low), 9)


def _alpha6(f: Fields) -> Wide:
    return -1.0 * ts_corr(f.open_, f.volume, 10)


def _alpha7(f: Fields) -> Wide:
    momentum = (-1.0 * ts_rank((f.close - ts_delay(f.close, 7)).abs(), 60)) * sign(
        ts_delta(f.close, 7)
    )
    return quesval2(f.adv(20), f.volume, momentum, -1.0)


def _alpha8(f: Fields) -> Wide:
    product = ts_sum(f.open_, 5) * ts_sum(f.returns, 5)
    return -1.0 * cs_rank(product - ts_delay(product, 10))


def _reversal_switch(f: Fields, window: int) -> Wide:
    """alpha9 / alpha10 共用的「连涨取动量、连跌取反转」分支。"""
    delta = ts_delta(f.close, 1)
    inner = quesval(0.0, ts_max(delta, window), -1.0 * delta, delta)
    return quesval(0.0, ts_min(delta, window), delta, inner)


def _alpha9(f: Fields) -> Wide:
    return _reversal_switch(f, 5)


def _alpha10(f: Fields) -> Wide:
    return cs_rank(_reversal_switch(f, 4))


def _alpha12(f: Fields) -> Wide:
    return sign(ts_delta(f.volume, 1)) * (-1.0 * ts_delta(f.close, 1))


def _alpha13(f: Fields) -> Wide:
    return -1.0 * cs_rank(ts_cov(cs_rank(f.close), cs_rank(f.volume), 5))


def _alpha14(f: Fields) -> Wide:
    return (-1.0 * cs_rank(f.returns - ts_delay(f.returns, 3))) * ts_corr(
        f.open_, f.volume, 10
    )


def _alpha15(f: Fields) -> Wide:
    return -1.0 * ts_sum(cs_rank(ts_corr(cs_rank(f.high), cs_rank(f.volume), 3)), 3)


def _alpha16(f: Fields) -> Wide:
    return -1.0 * cs_rank(ts_cov(cs_rank(f.high), cs_rank(f.volume), 5))


def _alpha17(f: Fields) -> Wide:
    curvature = f.close - 2.0 * ts_delay(f.close, 1) + ts_delay(f.close, 2)
    relative_volume = _safe_div(f.volume, f.adv(20))
    return (
        (-1.0 * cs_rank(ts_rank(f.close, 10)))
        * cs_rank(curvature)
        * cs_rank(ts_rank(relative_volume, 5))
    )


def _alpha18(f: Fields) -> Wide:
    body = f.close - f.open_
    return -1.0 * cs_rank(
        (ts_std(body.abs(), 5) + body) + ts_corr(f.close, f.open_, 10)
    )


def _alpha19(f: Fields) -> Wide:
    momentum = ts_delta(f.close, 7) + (f.close - ts_delay(f.close, 7))
    return (-1.0 * sign(momentum)) * (cs_rank(ts_sum(f.returns, 250) + 1.0) + 1.0)


def _alpha20(f: Fields) -> Wide:
    return (
        (-1.0 * cs_rank(f.open_ - ts_delay(f.high, 1)))
        * cs_rank(f.open_ - ts_delay(f.close, 1))
        * cs_rank(f.open_ - ts_delay(f.low, 1))
    )


def _alpha21(f: Fields) -> Wide:
    mean8, std8, mean2 = ts_mean(f.close, 8), ts_std(f.close, 8), ts_mean(f.close, 2)
    volume_gate = quesval(1.0, _safe_div(f.volume, f.adv(20)), 1.0, -1.0)
    inner = quesval2(mean2, mean8 - std8, 1.0, volume_gate)
    return quesval2(mean8 + std8, mean2, -1.0, inner)


def _alpha22(f: Fields) -> Wide:
    return -1.0 * ts_delta(ts_corr(f.high, f.volume, 5), 5) * cs_rank(ts_std(f.close, 20))


def _alpha23(f: Fields) -> Wide:
    return quesval2(ts_mean(f.high, 20), f.high, -1.0 * ts_delta(f.high, 2), 0.0)


def _alpha24(f: Fields) -> Wide:
    drift = _safe_div(ts_delta(ts_sum(f.close, 100) / 100.0, 100), ts_delay(f.close, 100))
    return quesval(
        0.05, drift, -1.0 * ts_delta(f.close, 3), -1.0 * (f.close - ts_min(f.close, 100))
    )


def _alpha26(f: Fields) -> Wide:
    return -1.0 * ts_max(ts_corr(ts_rank(f.volume, 5), ts_rank(f.high, 5), 5), 3)


def _alpha28(f: Fields) -> Wide:
    return cs_scale(ts_corr(f.adv(20), f.low, 5) + (f.high + f.low) / 2.0 - f.close)


def _alpha29(f: Fields) -> Wide:
    inner = cs_rank(cs_rank(-1.0 * cs_rank(ts_delta(f.close - 1.0, 5))))
    # ts_sum(..., 1) 与 ts_product(..., 1) 在原式中都是恒等运算，保留以对齐表达式
    scaled = cs_scale(log(ts_sum(ts_min(inner, 2), 1)))
    left = ts_min(ts_product(cs_rank(cs_rank(scaled)), 1), 5)
    return left + ts_rank(ts_delay(-1.0 * f.returns, 6), 5)


def _alpha30(f: Fields) -> Wide:
    streak = (
        sign(f.close - ts_delay(f.close, 1))
        + sign(ts_delay(f.close, 1) - ts_delay(f.close, 2))
        + sign(ts_delay(f.close, 2) - ts_delay(f.close, 3))
    )
    return _safe_div((cs_rank(streak) * -1.0 + 1.0) * ts_sum(f.volume, 5), ts_sum(f.volume, 20))


def _alpha31(f: Fields) -> Wide:
    decayed = ts_decay_linear(-1.0 * cs_rank(cs_rank(ts_delta(f.close, 10))), 10)
    return (
        cs_rank(cs_rank(cs_rank(decayed)))
        + cs_rank(-1.0 * ts_delta(f.close, 3))
        + sign(cs_scale(ts_corr(f.adv(20), f.low, 12)))
    )


def _alpha33(f: Fields) -> Wide:
    return cs_rank(-1.0 * (_safe_div(f.open_, f.close) * -1.0 + 1.0))


def _alpha34(f: Fields) -> Wide:
    volatility_ratio = _safe_div(ts_std(f.returns, 2), ts_std(f.returns, 5))
    return cs_rank(
        (cs_rank(volatility_ratio) * -1.0 + 1.0) + (cs_rank(ts_delta(f.close, 1)) * -1.0 + 1.0)
    )


def _alpha35(f: Fields) -> Wide:
    return (
        ts_rank(f.volume, 32)
        * (ts_rank(f.close + f.high - f.low, 16) * -1.0 + 1.0)
        * (ts_rank(f.returns, 32) * -1.0 + 1.0)
    )


def _alpha37(f: Fields) -> Wide:
    gap = f.open_ - f.close
    return cs_rank(ts_corr(ts_delay(gap, 1), f.close, 200)) + cs_rank(gap)


def _alpha38(f: Fields) -> Wide:
    return (-1.0 * cs_rank(ts_rank(f.close, 10))) * cs_rank(_safe_div(f.close, f.open_))


def _alpha39(f: Fields) -> Wide:
    decayed = cs_rank(ts_decay_linear(_safe_div(f.volume, f.adv(20)), 9)) * -1.0 + 1.0
    return (-1.0 * cs_rank(ts_delta(f.close, 7) * decayed)) * (
        cs_rank(ts_sum(f.returns, 250)) + 1.0
    )


def _alpha40(f: Fields) -> Wide:
    return (-1.0 * cs_rank(ts_std(f.high, 10))) * ts_corr(f.high, f.volume, 10)


def _alpha43(f: Fields) -> Wide:
    return ts_rank(_safe_div(f.volume, f.adv(20)), 20) * ts_rank(
        -1.0 * ts_delta(f.close, 7), 8
    )


def _alpha44(f: Fields) -> Wide:
    return -1.0 * ts_corr(f.high, cs_rank(f.volume), 5)


def _alpha45(f: Fields) -> Wide:
    return (
        -1.0
        * cs_rank(ts_sum(ts_delay(f.close, 5), 20) / 20.0)
        * ts_corr(f.close, f.volume, 2)
        * cs_rank(ts_corr(ts_sum(f.close, 5), ts_sum(f.close, 20), 2))
    )


def _decay_slope(f: Fields) -> Wide:
    """alpha46/49/51 共用的「二阶价格斜率」条件量。"""
    return (ts_delay(f.close, 20) - ts_delay(f.close, 10)) / 10.0 - (
        ts_delay(f.close, 10) - f.close
    ) / 10.0


def _alpha46(f: Fields) -> Wide:
    slope = _decay_slope(f)
    inner = quesval(0.0, slope, -1.0 * (f.close - ts_delay(f.close, 1)), 1.0)
    return quesval(0.25, slope, -1.0, inner)


def _alpha49(f: Fields) -> Wide:
    return quesval(-0.1, _decay_slope(f), -1.0 * (f.close - ts_delay(f.close, 1)), 1.0)


def _alpha51(f: Fields) -> Wide:
    return quesval(-0.05, _decay_slope(f), -1.0 * (f.close - ts_delay(f.close, 1)), 1.0)


def _alpha52(f: Fields) -> Wide:
    low_min = ts_min(f.low, 5)
    long_term = (ts_sum(f.returns, 240) - ts_sum(f.returns, 20)) / 220.0
    return ((-1.0 * low_min) + ts_delay(low_min, 5)) * cs_rank(long_term) * ts_rank(
        f.volume, 5
    )


def _alpha53(f: Fields) -> Wide:
    body = (f.close - f.low) - (f.high - f.close)
    return -1.0 * ts_delta(_safe_div(body, f.close - f.low), 9)


def _alpha54(f: Fields) -> Wide:
    numerator = -1.0 * ((f.low - f.close) * pow1(f.open_, 5.0))
    return _safe_div(numerator, (f.low - f.high) * pow1(f.close, 5.0))


def _alpha55(f: Fields) -> Wide:
    low12, high12 = ts_min(f.low, 12), ts_max(f.high, 12)
    stochastic = _safe_div(f.close - low12, high12 - low12)
    return -1.0 * ts_corr(cs_rank(stochastic), cs_rank(f.volume), 6)


def _alpha60(f: Fields) -> Wide:
    pressure = _safe_div((f.close - f.low) - (f.high - f.close), f.high - f.low) * f.volume
    return -1.0 * (
        2.0 * cs_scale(cs_rank(pressure)) - cs_scale(cs_rank(ts_argmax(f.close, 10)))
    )


def _alpha68(f: Fields) -> Wide:
    left = ts_rank(ts_corr(cs_rank(f.high), cs_rank(f.adv(15)), 9), 14)
    right = cs_rank(ts_delta(f.close * 0.518371 + f.low * (1.0 - 0.518371), 1))
    return lt(left, right) * -1.0


def _alpha85(f: Fields) -> Wide:
    base = cs_rank(ts_corr(f.high * 0.876703 + f.close * 0.123297, f.adv(30), 10))
    exponent = cs_rank(ts_corr(ts_rank((f.high + f.low) / 2.0, 4), ts_rank(f.volume, 10), 7))
    return pow2(base, exponent)


def _alpha88(f: Fields) -> Wide:
    spread = (cs_rank(f.open_) + cs_rank(f.low)) - (cs_rank(f.high) + cs_rank(f.close))
    left = cs_rank(ts_decay_linear(spread, 8))
    corr = ts_corr(ts_rank(f.close, 8), ts_rank(f.adv(60), 21), 8)
    right = ts_rank(ts_decay_linear(corr, 7), 3)
    return ts_less(left, right)


def _alpha92(f: Fields) -> Wide:
    gate = quesval2((f.high + f.low) / 2.0 + f.close, f.low + f.open_, 1.0, 0.0)
    left = ts_rank(ts_decay_linear(gate, 15), 19)
    corr = ts_corr(cs_rank(f.low), cs_rank(f.adv(30)), 8)
    right = ts_rank(ts_decay_linear(corr, 7), 7)
    return ts_less(left, right)


def _alpha95(f: Fields) -> Wide:
    corr = ts_corr(ts_sum((f.high + f.low) / 2.0, 19), ts_sum(f.adv(40), 19), 13)
    threshold = cs_rank(f.open_ - ts_min(f.open_, 12))
    return quesval2(threshold, ts_rank(pow1(cs_rank(corr), 5.0), 12), 1.0, 0.0)


def _alpha99(f: Fields) -> Wide:
    threshold = cs_rank(ts_corr(ts_sum((f.high + f.low) / 2.0, 20), ts_sum(f.adv(60), 20), 9))
    return quesval2(threshold, cs_rank(ts_corr(f.low, f.volume, 6)), 1.0, 0.0) * -1.0


def _alpha101(f: Fields) -> Wide:
    return (f.close - f.open_) / ((f.high - f.low) + 0.001)


#: alpha 名 → 宽表实现（键集合必须与 ALPHA_EXPRESSIONS 完全一致）
ALPHA_FUNCTIONS: dict[str, AlphaFn] = {
    "alpha1": _alpha1, "alpha2": _alpha2, "alpha3": _alpha3, "alpha4": _alpha4,
    "alpha6": _alpha6, "alpha7": _alpha7, "alpha8": _alpha8, "alpha9": _alpha9,
    "alpha10": _alpha10, "alpha12": _alpha12, "alpha13": _alpha13, "alpha14": _alpha14,
    "alpha15": _alpha15, "alpha16": _alpha16, "alpha17": _alpha17, "alpha18": _alpha18,
    "alpha19": _alpha19, "alpha20": _alpha20, "alpha21": _alpha21, "alpha22": _alpha22,
    "alpha23": _alpha23, "alpha24": _alpha24, "alpha26": _alpha26, "alpha28": _alpha28,
    "alpha29": _alpha29, "alpha30": _alpha30, "alpha31": _alpha31, "alpha33": _alpha33,
    "alpha34": _alpha34, "alpha35": _alpha35, "alpha37": _alpha37, "alpha38": _alpha38,
    "alpha39": _alpha39, "alpha40": _alpha40, "alpha43": _alpha43, "alpha44": _alpha44,
    "alpha45": _alpha45, "alpha46": _alpha46, "alpha49": _alpha49, "alpha51": _alpha51,
    "alpha52": _alpha52, "alpha53": _alpha53, "alpha54": _alpha54, "alpha55": _alpha55,
    "alpha60": _alpha60, "alpha68": _alpha68, "alpha85": _alpha85, "alpha88": _alpha88,
    "alpha92": _alpha92, "alpha95": _alpha95, "alpha99": _alpha99, "alpha101": _alpha101,
}


def compute_alpha(panel: pd.DataFrame, name: str) -> pd.Series:
    """在面板上计算单个 alpha，返回 (datetime, instrument) 长表序列。"""
    fn = ALPHA_FUNCTIONS.get(name)
    if fn is None:
        raise ValueError(f"未知 Alpha101 因子: {name}")
    wide = fn(build_fields(panel))
    return to_long(wide.replace([np.inf, -np.inf], np.nan), panel.index)
