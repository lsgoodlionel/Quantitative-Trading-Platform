"""危机区间分段对照（Wave N-a / N3）

回撤区间表（`drawdown_periods.py`）回答的是「这条净值曲线自己最难受的几段」，
本模块回答的是另一个问题：「**市场**最难受的那几段里，这个策略表现如何」。
两者互补，不重叠。

⚠️ 区间与回测期无交集时**跳过**，不返回一行全 0 —— 全 0 看起来像
「这段时间策略没波动」，而事实是「这段时间根本没数据」。被跳过的窗口带
明确理由另行列出，见 `skipped_crisis_windows`。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

_EPS = 1e-12

#: 一个窗口内至少要有这么多个净值点才算得出收益率
_MIN_POINTS = 2

#: 跳过理由（对外常量，便于前端与测试引用）
SKIP_NOT_APPLICABLE = "该危机不适用于本市场"
SKIP_NO_OVERLAP = "回测期与该危机区间无交集"
SKIP_TOO_SHORT = "交集内净值点不足，算不出收益率"


@dataclass(frozen=True)
class CrisisWindow:
    """一段硬编码的历史危机区间。日期为收盘价口径的峰/谷，见下方溯源注释。"""

    name: str
    start: date
    end: date
    markets: tuple[str, ...]      # 该危机适用的市场（对齐 data.models.Market）


#: 危机区间是**硬编码的历史事实**，每条都以指数收盘价的峰→谷为锚点。
#: 不用「大概那阵子」这种印象填日期 —— 差几天就会把最深的一段切掉。
CRISIS_WINDOWS: tuple[CrisisWindow, ...] = (
    # 溯源：S&P 500 收盘峰值 1565.15（2007-10-09）→ 收盘谷底 676.53（2009-03-09），
    # 区间跌幅约 -56.8%。恒生指数同期的峰（2007-10-30, 31638）与谷
    # （2008-10-27, 11016）都落在此区间内，故 HK 共用该窗口。
    CrisisWindow("2008 金融危机", date(2007, 10, 9), date(2009, 3, 9), ("US", "HK")),
    # 溯源：上证综指收盘峰值 5166.35（2015-06-12）→ 本轮收盘谷底 2655.66
    # （2016-01-28，熔断机制试行并暂停后的低点），区间跌幅约 -48.6%。
    CrisisWindow("2015 A股股灾", date(2015, 6, 12), date(2016, 1, 28), ("A",)),
    # 溯源：S&P 500 收盘峰值 3386.15（2020-02-19）→ 收盘谷底 2237.40（2020-03-23），
    # 区间跌幅约 -33.9%。港股与 A 股同期同步下挫，三个市场共用该窗口。
    CrisisWindow("2020 疫情崩盘", date(2020, 2, 19), date(2020, 3, 23), ("US", "HK", "A")),
    # 溯源：S&P 500 收盘峰值 4796.56（2022-01-03）→ 收盘谷底 3577.03（2022-10-12），
    # 区间跌幅约 -25.4%。同期恒指自 2022-01 高位跌至 2022-10-31 的 14687。
    CrisisWindow("2022 加息熊市", date(2022, 1, 3), date(2022, 10, 12), ("US", "HK")),
)


def crisis_performance(equity: pd.Series, market: str) -> list[dict]:
    """逐个危机区间算该策略的表现；无交集/不适用/样本不足的窗口**不出现在结果里**。"""
    covered, _ = _split_windows(equity, market)
    return covered


def skipped_crisis_windows(equity: pd.Series, market: str) -> list[dict]:
    """被跳过的窗口及其理由 —— 让「没有这一行」和「这一行是 0」不再混淆。"""
    _, skipped = _split_windows(equity, market)
    return skipped


def build_crisis_section(equity: pd.Series, market: str) -> dict | None:
    """组装 N3 的可空 section；净值曲线不足时返回 None。"""
    if equity is None or len(equity) < _MIN_POINTS:
        return None
    covered, skipped = _split_windows(equity, market)
    return {
        "market": _market_code(market),
        "windows": covered,
        "skipped": skipped,
    }


# ── 内部 ─────────────────────────────────────────────────────────

def _split_windows(
    equity: pd.Series, market: str
) -> tuple[list[dict], list[dict]]:
    """一次遍历同时产出「有数据的窗口」与「被跳过的窗口 + 理由」。"""
    covered: list[dict] = []
    skipped: list[dict] = []
    if equity is None or equity.empty:
        return covered, skipped

    code = _market_code(market)
    curve = _sorted_curve(equity)

    for window in CRISIS_WINDOWS:
        if code not in window.markets:
            skipped.append(_skip(window, SKIP_NOT_APPLICABLE))
            continue
        segment = _slice(curve, window)
        if segment.empty:
            skipped.append(_skip(window, SKIP_NO_OVERLAP))
            continue
        if len(segment) < _MIN_POINTS:
            skipped.append(_skip(window, SKIP_TOO_SHORT))
            continue
        covered.append(_row(window, segment))

    return covered, skipped


def _row(window: CrisisWindow, segment: pd.Series) -> dict:
    """一段危机区间内的表现快照。"""
    returns = segment.pct_change().dropna()
    first, last = float(segment.iloc[0]), float(segment.iloc[-1])
    covered_start = pd.Timestamp(segment.index[0]).date()
    covered_end = pd.Timestamp(segment.index[-1]).date()

    return {
        "name": window.name,
        "start": window.start.isoformat(),
        "end": window.end.isoformat(),
        "covered_start": covered_start.isoformat(),
        "covered_end": covered_end.isoformat(),
        "is_partial": covered_start > window.start or covered_end < window.end,
        "trading_days": int(len(segment)),
        "return_pct": _pct(last / first - 1.0) if first > _EPS else 0.0,
        "max_drawdown_pct": _max_drawdown_pct(segment),
        "volatility_pct": _volatility_pct(returns),
        "best_day_pct": _pct(float(returns.max())) if not returns.empty else 0.0,
        "worst_day_pct": _pct(float(returns.min())) if not returns.empty else 0.0,
        "positive_days_pct": (
            _pct(float((returns > 0).mean())) if not returns.empty else 0.0
        ),
    }


def _skip(window: CrisisWindow, reason: str) -> dict:
    return {
        "name": window.name,
        "start": window.start.isoformat(),
        "end": window.end.isoformat(),
        "markets": list(window.markets),
        "reason": reason,
    }


def _slice(curve: pd.Series, window: CrisisWindow) -> pd.Series:
    start = pd.Timestamp(window.start)
    end = pd.Timestamp(window.end) + pd.Timedelta(days=1)   # 含末日的全部时点
    return curve.loc[(curve.index >= start) & (curve.index < end)]


def _sorted_curve(equity: pd.Series) -> pd.Series:
    """整理成 tz-naive 的**本地日历日**索引。

    去时区必须用 `tz_localize(None)`（丢标签、保留墙上时间），**不能**用
    `tz_convert(None)`（先折算到 UTC 再丢标签）。后者会把
    `2015-06-12 00:00+08:00` 变成 `2015-06-11 16:00`，硬编码危机窗口的边界
    就此错开一天；而 `capacity.py` 那边是直接取 `.date()`（即本地日历日），
    两个模块的日期口径也会因此对不上。
    """
    curve = equity.astype(float)
    if not isinstance(curve.index, pd.DatetimeIndex):
        curve.index = pd.DatetimeIndex(curve.index)
    if getattr(curve.index, "tz", None) is not None:
        curve.index = curve.index.tz_localize(None)
    return curve.sort_index()


def _max_drawdown_pct(segment: pd.Series) -> float:
    peak = segment.cummax()
    drawdown = (segment - peak) / peak.where(peak.abs() > _EPS, other=np.nan)
    worst = drawdown.min()
    return _pct(float(worst)) if pd.notna(worst) else 0.0


def _volatility_pct(returns: pd.Series) -> float:
    """区间内的**已实现**波动率（未年化）—— 危机窗口长短不一，年化反而误导。"""
    if len(returns) < _MIN_POINTS:
        return 0.0
    return _pct(float(returns.std()))


def _market_code(market: str) -> str:
    """接受 Market 枚举或裸字符串，统一成 'US' / 'HK' / 'A'。"""
    return str(getattr(market, "value", market))


def _pct(value: float) -> float:
    return round(value * 100, 4)


def crisis_window_names() -> Sequence[str]:
    """全部硬编码危机窗口的名称（前端做筛选器用）。"""
    return tuple(w.name for w in CRISIS_WINDOWS)
