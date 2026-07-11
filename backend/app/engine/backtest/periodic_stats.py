"""
周期分组统计 (Periodic Breakdown) — C6

按 日 / 周 / 月 / 星期几 分桶汇总回合盈亏，产出每桶的
profit / 胜平负 / profit_factor，并给出最佳/最差周期与胜负桶计数。

注意: 本模块基于"回合盈亏 (trade-PnL)"，与既有 monthly_returns
(净值收益热力图) 口径不同，两者并存 (§5)。

参考: refs/freqtrade/.../optimize_reports.py::generate_periodic_breakdown_stats
"""

from __future__ import annotations

import pandas as pd

from app.engine.backtest.roundtrips import RoundTrip

_WEEKDAY_LABELS = ["Monday", "Tuesday", "Wednesday", "Thursday",
                   "Friday", "Saturday", "Sunday"]


def compute_periodic_stats(trips: list[RoundTrip], equity_curve: pd.Series) -> dict:
    """计算 C6 周期分组统计，返回可直接序列化的 dict。"""
    if not trips:
        return _empty()

    starting_balance = float(equity_curve.iloc[0]) if not equity_curve.empty else 0.0
    df = pd.DataFrame([
        {"exit": pd.Timestamp(t.exit_time), "pnl": t.pnl}
        for t in trips
    ]).set_index("exit").sort_index()

    daily = _bucket(df, df.index.normalize(), _iso_date, _epoch_ms, starting_balance)
    weekly = _bucket(df, df.index.to_period("W-MON"), _iso_week, _period_epoch_ms, starting_balance)
    monthly = _bucket(df, df.index.to_period("M"), _iso_month, _period_epoch_ms, starting_balance)
    weekday = _weekday_bucket(df, starting_balance)

    return {
        "daily": daily,
        "weekly": weekly,
        "monthly": monthly,
        "weekday": weekday,
        "best_day": _extreme(daily, best=True),
        "worst_day": _extreme(daily, best=False),
        "best_month": _extreme(monthly, best=True),
        "worst_month": _extreme(monthly, best=False),
        "winning_days": _count(daily, positive=True),
        "losing_days": _count(daily, positive=False),
        "winning_weeks": _count(weekly, positive=True),
        "losing_weeks": _count(weekly, positive=False),
        "winning_months": _count(monthly, positive=True),
        "losing_months": _count(monthly, positive=False),
    }


def _bucket(df, grouper, label_fn, ts_fn, starting_balance: float) -> list[dict]:
    buckets: list[dict] = []
    for key, group in df.groupby(grouper):
        buckets.append(_make_bucket(label_fn(key), ts_fn(key), group["pnl"], starting_balance))
    buckets.sort(key=lambda b: b["date_ts"])
    return buckets


def _weekday_bucket(df, starting_balance: float) -> list[dict]:
    dow = df.index.dayofweek
    buckets: list[dict] = []
    for d in range(7):
        group = df[dow == d]
        if group.empty:
            continue
        buckets.append(_make_bucket(_WEEKDAY_LABELS[d], d, group["pnl"], starting_balance))
    return buckets


def _make_bucket(label: str, date_ts: int, pnls: pd.Series, starting_balance: float) -> dict:
    profit_abs = float(pnls.sum())
    wins = int((pnls > 0).sum())
    losses = int((pnls < 0).sum())
    draws = int((pnls == 0).sum())
    winning_profit = float(pnls[pnls > 0].sum())
    losing_profit = float(abs(pnls[pnls < 0].sum()))
    pf = winning_profit / losing_profit if losing_profit > 1e-12 else 0.0
    profit_pct = profit_abs / starting_balance * 100 if starting_balance > 1e-12 else 0.0
    return {
        "label": label,
        "date_ts": int(date_ts),
        "profit_abs": round(profit_abs, 4),
        "profit_pct": round(profit_pct, 4),
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "trades": int(len(pnls)),
        "profit_factor": round(min(pf, 99.9), 4),
    }


def _extreme(buckets: list[dict], best: bool) -> dict | None:
    if not buckets:
        return None
    return (max if best else min)(buckets, key=lambda b: b["profit_abs"])


def _count(buckets: list[dict], positive: bool) -> int:
    if positive:
        return sum(1 for b in buckets if b["profit_abs"] > 0)
    return sum(1 for b in buckets if b["profit_abs"] < 0)


# ── 标签与时间戳格式化 ──────────────────────────────────────────

def _iso_date(ts: pd.Timestamp) -> str:
    return ts.strftime("%Y-%m-%d")


def _iso_week(period) -> str:
    start = period.start_time
    iso = start.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _iso_month(period) -> str:
    return period.start_time.strftime("%Y-%m")


def _epoch_ms(ts: pd.Timestamp) -> int:
    return int(ts.timestamp() * 1000)


def _period_epoch_ms(period) -> int:
    return int(period.start_time.timestamp() * 1000)


def _empty() -> dict:
    return {
        "daily": [], "weekly": [], "monthly": [], "weekday": [],
        "best_day": None, "worst_day": None, "best_month": None, "worst_month": None,
        "winning_days": 0, "losing_days": 0, "winning_weeks": 0, "losing_weeks": 0,
        "winning_months": 0, "losing_months": 0,
    }
