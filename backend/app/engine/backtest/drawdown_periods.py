"""
回撤区间表 (Drawdown Periods) — C6

扫描净值曲线，识别每一段 峰(peak)→谷(valley)→恢复(recovery) 的回撤片段，
记录深度、时长、恢复时长与水下天数，按深度排序取 top-N。

参考: pyfolio drawdown table / refs/freqtrade drawdown 片段扫描
"""

from __future__ import annotations

import pandas as pd

_EPS = 1e-9


def compute_drawdown_periods(equity_curve: pd.Series, top_n: int = 10) -> list[dict]:
    """
    返回 top-N 回撤区间（按深度降序，rank=1 为最深）。

    算法: 遍历净值，维护滚动峰值。净值创新高即闭合上一段回撤片段并记录。
    末尾仍处于水下的片段以"未恢复"(recovery_date=None) 收尾。
    """
    if equity_curve is None or equity_curve.empty or len(equity_curve) < 2:
        return []

    equity = equity_curve.astype(float)
    index = equity.index

    episodes: list[dict] = []
    peak_val = float(equity.iloc[0])
    peak_pos = 0
    valley_val = peak_val
    valley_pos = 0
    in_dd = False

    for i in range(1, len(equity)):
        val = float(equity.iloc[i])
        if val >= peak_val - _EPS:
            # 创新高：若正处于回撤中，则闭合该片段（已恢复）
            if in_dd:
                episodes.append(_episode(index, equity, peak_pos, valley_pos, i))
                in_dd = False
            peak_val = val
            peak_pos = i
            valley_val = val
            valley_pos = i
        else:
            in_dd = True
            if val < valley_val:
                valley_val = val
                valley_pos = i

    # 末尾未恢复的片段
    if in_dd:
        episodes.append(_episode(index, equity, peak_pos, valley_pos, None))

    episodes.sort(key=lambda e: e["depth_pct"])   # depth 为负，最深在前
    top = episodes[:top_n]
    for rank, ep in enumerate(top, start=1):
        ep["rank"] = rank
    return top


def _episode(index, equity, peak_pos: int, valley_pos: int, recovery_pos: int | None) -> dict:
    peak_val = float(equity.iloc[peak_pos])
    valley_val = float(equity.iloc[valley_pos])
    peak_date = index[peak_pos]
    valley_date = index[valley_pos]

    depth_pct = (valley_val - peak_val) / peak_val * 100 if peak_val > _EPS else 0.0
    drawdown_days = _days(peak_date, valley_date)

    if recovery_pos is not None:
        recovery_date = index[recovery_pos]
        recovery_days = _days(valley_date, recovery_date)
        length_days = _days(peak_date, recovery_date)
        recovery_iso = _fmt(recovery_date)
    else:
        recovery_date = index[-1]
        recovery_days = None
        length_days = _days(peak_date, recovery_date)
        recovery_iso = None

    # 水下天数: 峰值之后到恢复(或末尾)前，净值持续低于峰值的最长跨度
    underwater = _max_underwater(index, equity, peak_pos, recovery_pos)

    return {
        "rank": 0,
        "peak_date": _fmt(peak_date),
        "valley_date": _fmt(valley_date),
        "recovery_date": recovery_iso,
        "depth_pct": round(depth_pct, 4),
        "length_days": length_days,
        "drawdown_days": drawdown_days,
        "recovery_days": recovery_days,
        "max_underwater_days": underwater,
    }


def _max_underwater(index, equity, peak_pos: int, recovery_pos: int | None) -> int:
    end = recovery_pos if recovery_pos is not None else len(equity) - 1
    peak_val = float(equity.iloc[peak_pos])
    start_underwater = None
    max_span = 0
    for i in range(peak_pos + 1, end + 1):
        below = float(equity.iloc[i]) < peak_val - _EPS
        if below:
            if start_underwater is None:
                start_underwater = index[i - 1] if i > 0 else index[i]
            max_span = max(max_span, _days(start_underwater, index[i]))
        else:
            start_underwater = None
    return max_span


def _days(a, b) -> int:
    try:
        return int(max((pd.Timestamp(b) - pd.Timestamp(a)).days, 0))
    except Exception:
        return 0


def _fmt(dt) -> str:
    try:
        return pd.Timestamp(dt).isoformat()
    except Exception:
        return str(dt)
