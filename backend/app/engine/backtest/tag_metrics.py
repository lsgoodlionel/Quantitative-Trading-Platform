"""
标签分组 + 扩展风险比率 (Tag Metrics + Risk Ratios) — C6

- 按 entry_tag / exit_reason 分组统计 (含尾部 TOTAL 行)
- 扩展 60+ 风险比率套件中"metrics.py 尚未提供"的部分 (§5 去重)

参考:
- refs/jesse/jesse/services/metrics.py — serenity / ulcer / CVaR / 水下周期 / 多空胜率
- refs/freqtrade/.../optimize_reports.py::generate_tag_metrics
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from app.engine.backtest.metrics import compute_drawdown_series
from app.engine.backtest.roundtrips import RoundTrip

_EPS = 1e-12


def compute_tag_metrics(
    trips: list[RoundTrip],
    returns: pd.Series,
    equity_curve: pd.Series,
    starting_balance: float,
    periods_per_year: int = 252,
) -> dict:
    """计算 C6 标签分组与扩展风险比率，返回可序列化 dict。"""
    return {
        "by_entry_tag": _group_rows(trips, key=lambda t: t.entry_tag, starting_balance=starting_balance),
        "by_exit_reason": _group_rows(trips, key=lambda t: t.exit_reason, starting_balance=starting_balance),
        "risk_ratios": _risk_ratios(trips, returns, equity_curve, starting_balance, periods_per_year),
    }


# ── 标签分组 ────────────────────────────────────────────────────

def _group_rows(trips: list[RoundTrip], key, starting_balance: float) -> list[dict]:
    if not trips:
        return []
    groups: dict[str, list[RoundTrip]] = {}
    for t in trips:
        groups.setdefault(str(key(t)), []).append(t)

    rows = [_tag_row(name, items, starting_balance) for name, items in groups.items()]
    rows.sort(key=lambda r: r["profit_abs"], reverse=True)
    rows.append(_tag_row("TOTAL", trips, starting_balance))
    return rows


def _tag_row(name: str, items: list[RoundTrip], starting_balance: float) -> dict:
    pnls = [t.pnl for t in items]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    draws = [p for p in pnls if p == 0]
    profit_abs = float(sum(pnls))
    gross_win = float(sum(wins))
    gross_loss = float(abs(sum(losses)))
    pf = gross_win / gross_loss if gross_loss > _EPS else 0.0
    total = len(items)
    return {
        "key": name,
        "trades": total,
        "wins": len(wins),
        "draws": len(draws),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / total * 100, 4) if total else 0.0,
        "profit_abs": round(profit_abs, 4),
        "profit_pct": round(profit_abs / starting_balance * 100, 4) if starting_balance > _EPS else 0.0,
        "profit_factor": round(min(pf, 99.9), 4),
        "avg_pnl": round(profit_abs / total, 4) if total else 0.0,
        "avg_holding_days": round(float(np.mean([t.holding_days for t in items])), 4) if items else 0.0,
    }


# ── 扩展风险比率 (仅 metrics.py 未含者) ──────────────────────────

def _risk_ratios(trips, returns, equity_curve, starting_balance, periods) -> dict:
    ret = returns.dropna() if returns is not None else pd.Series(dtype=float)
    arr = ret.to_numpy() if not ret.empty else np.array([])

    cagr = _cagr(equity_curve)
    dd_series = compute_drawdown_series(equity_curve) if not equity_curve.empty else pd.Series(dtype=float)
    ulcer = _ulcer_index(dd_series)
    serenity = _serenity(ret, dd_series, ulcer)
    var95 = float(np.percentile(arr, 5)) * 100 if arr.size else 0.0
    cvar95 = _cvar(arr) * 100
    max_uw = _max_underwater_days(equity_curve)
    recovery = _recovery_factor(equity_curve)
    payoff = _payoff_ratio(trips)
    tail = _tail_ratio(arr)
    profit_factor = _profit_factor(trips)
    common_sense = tail * profit_factor
    win_rate = _win_rate(trips)
    kelly = win_rate - (1 - win_rate) / payoff if payoff > _EPS else 0.0
    skew = float(stats.skew(arr)) if arr.size > 2 else 0.0
    kurt = float(stats.kurtosis(arr)) if arr.size > 2 else 0.0
    downside = _downside_deviation(arr, periods)
    gain_pain = _gain_to_pain(arr)
    avg_hold = float(np.mean([t.holding_days for t in trips])) if trips else 0.0
    up_m, down_m = _monthly_up_down(equity_curve)
    wr_long, wr_short = _dir_win_rate(trips)
    pf_long, pf_short = _dir_profit_factor(trips)
    best_pct, worst_pct = _best_worst_pct(trips)

    return {
        "cagr_pct": round(cagr * 100, 4),
        "ulcer_index": round(ulcer, 4),
        "serenity_index": round(serenity, 4),
        "cvar_95_pct": round(cvar95, 4),
        "value_at_risk_95_pct": round(var95, 4),
        "max_underwater_days": max_uw,
        "recovery_factor": round(recovery, 4),
        "payoff_ratio": round(payoff, 4),
        "tail_ratio": round(tail, 4),
        "common_sense_ratio": round(common_sense, 4),
        "kelly_criterion": round(kelly, 4),
        "skew": round(skew, 4),
        "kurtosis": round(kurt, 4),
        "downside_deviation_pct": round(downside * 100, 4),
        "gain_to_pain_ratio": round(gain_pain, 4),
        "avg_holding_period_days": round(avg_hold, 4),
        "avg_up_month_pct": round(up_m, 4),
        "avg_down_month_pct": round(down_m, 4),
        "win_rate_long_pct": round(wr_long * 100, 4),
        "win_rate_short_pct": round(wr_short * 100, 4),
        "profit_factor_long": round(min(pf_long, 99.9), 4),
        "profit_factor_short": round(min(pf_short, 99.9), 4),
        "best_trade_pct": round(best_pct, 4),
        "worst_trade_pct": round(worst_pct, 4),
    }


def _cagr(equity: pd.Series) -> float:
    if equity.empty or len(equity) < 2:
        return 0.0
    start_val, end_val = float(equity.iloc[0]), float(equity.iloc[-1])
    if start_val <= _EPS:
        return 0.0
    days = max((equity.index[-1] - equity.index[0]).days, 1)
    return (end_val / start_val) ** (365.0 / days) - 1


def _ulcer_index(dd_series: pd.Series) -> float:
    if dd_series.empty or len(dd_series) < 2:
        return 0.0
    dd_pct = dd_series * 100
    return float(np.sqrt((dd_pct ** 2).sum() / (len(dd_pct) - 1)))


def _serenity(ret: pd.Series, dd_series: pd.Series, ulcer: float) -> float:
    if ret.empty or ulcer < _EPS:
        return 0.0
    std = float(ret.std())
    if std < _EPS:
        return 0.0
    dd_arr = dd_series.to_numpy()
    pitfall = -_cvar(dd_arr) / std if std > _EPS else 0.0
    denom = ulcer * pitfall
    return float(ret.sum()) / denom if abs(denom) > _EPS else 0.0


def _cvar(arr: np.ndarray, q: float = 5) -> float:
    if arr.size == 0:
        return 0.0
    threshold = np.percentile(arr, q)
    tail = arr[arr <= threshold]
    return float(tail.mean()) if tail.size else 0.0


def _max_underwater_days(equity: pd.Series) -> int:
    if equity.empty or len(equity) < 2:
        return 0
    running_max = equity.cummax()
    underwater = equity < running_max - _EPS
    max_span = cur_start = 0
    start_ts = None
    for ts, uw in underwater.items():
        if uw:
            if start_ts is None:
                start_ts = ts
            max_span = max(max_span, int((pd.Timestamp(ts) - pd.Timestamp(start_ts)).days))
        else:
            start_ts = None
    return max_span


def _recovery_factor(equity: pd.Series) -> float:
    if equity.empty or len(equity) < 2:
        return 0.0
    net_profit = float(equity.iloc[-1] - equity.iloc[0])
    running_max = equity.cummax()
    max_dd_abs = float((running_max - equity).max())
    return net_profit / max_dd_abs if max_dd_abs > _EPS else 0.0


def _payoff_ratio(trips) -> float:
    wins = [t.pnl for t in trips if t.pnl > 0]
    losses = [t.pnl for t in trips if t.pnl < 0]
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(abs(np.mean(losses))) if losses else 0.0
    return avg_win / avg_loss if avg_loss > _EPS else 0.0


def _tail_ratio(arr: np.ndarray) -> float:
    if arr.size < 2:
        return 0.0
    p95 = float(np.percentile(arr, 95))
    p5 = float(abs(np.percentile(arr, 5)))
    return p95 / p5 if p5 > _EPS else 0.0


def _profit_factor(trips) -> float:
    gross_win = float(sum(t.pnl for t in trips if t.pnl > 0))
    gross_loss = float(abs(sum(t.pnl for t in trips if t.pnl < 0)))
    return gross_win / gross_loss if gross_loss > _EPS else 0.0


def _win_rate(trips) -> float:
    if not trips:
        return 0.0
    return sum(1 for t in trips if t.pnl > 0) / len(trips)


def _downside_deviation(arr: np.ndarray, periods: int) -> float:
    neg = arr[arr < 0]
    if neg.size < 2:
        return 0.0
    return float(neg.std()) * np.sqrt(periods)


def _gain_to_pain(arr: np.ndarray) -> float:
    if arr.size == 0:
        return 0.0
    pain = float(abs(arr[arr < 0].sum()))
    return float(arr.sum()) / pain if pain > _EPS else 0.0


def _monthly_up_down(equity: pd.Series) -> tuple[float, float]:
    if equity.empty or len(equity) < 2:
        return 0.0, 0.0
    monthly = equity.resample("ME").last().pct_change().dropna() * 100
    if monthly.empty:
        return 0.0, 0.0
    up = monthly[monthly > 0]
    down = monthly[monthly < 0]
    return (
        float(up.mean()) if not up.empty else 0.0,
        float(down.mean()) if not down.empty else 0.0,
    )


def _dir_win_rate(trips) -> tuple[float, float]:
    longs = [t for t in trips if t.direction == "long"]
    shorts = [t for t in trips if t.direction == "short"]
    wr_long = sum(1 for t in longs if t.pnl > 0) / len(longs) if longs else 0.0
    wr_short = sum(1 for t in shorts if t.pnl > 0) / len(shorts) if shorts else 0.0
    return wr_long, wr_short


def _dir_profit_factor(trips) -> tuple[float, float]:
    return (
        _profit_factor([t for t in trips if t.direction == "long"]),
        _profit_factor([t for t in trips if t.direction == "short"]),
    )


def _best_worst_pct(trips) -> tuple[float, float]:
    if not trips:
        return 0.0, 0.0
    pcts = [t.pnl_pct for t in trips]
    return float(max(pcts)), float(min(pcts))
