"""
逐笔回合聚合统计 (Trade Analytics) — C7

从重构后的 RoundTrip 列表计算总/胜/负、胜率、平均盈亏、最大盈亏、
连胜连败、持仓周期、多空拆分与期望值等聚合指标。

参考:
- refs/backtrader/backtrader/analyzers/tradeanalyzer.py — 盈亏/多空拆分、连胜
- refs/freqtrade/.../optimize_reports.py::calc_streak — 连续同号分组
"""

from __future__ import annotations

import numpy as np

from app.engine.backtest.roundtrips import RoundTrip

_ROW_CAP = 500          # round_trips 表最多返回行数，与 fills[:500] 一致
_DAYS_PER_WEEK = 7.0
_DAYS_PER_MONTH = 30.44


def compute_trade_analytics(trips: list[RoundTrip], starting_balance: float) -> dict:
    """计算 C7 逐笔回合聚合统计，返回可直接序列化的 dict。"""
    total = len(trips)
    if total == 0:
        return _empty()

    pnls = [t.pnl for t in trips]
    wins = [t for t in trips if t.pnl > 0]
    losses = [t for t in trips if t.pnl < 0]
    breakeven = [t for t in trips if t.pnl == 0]

    gross_profit = float(sum(t.pnl for t in wins))
    gross_loss = float(sum(t.pnl for t in losses))      # 负数
    net_profit = gross_profit + gross_loss

    avg_win = float(np.mean([t.pnl for t in wins])) if wins else 0.0
    avg_loss = float(np.mean([t.pnl for t in losses])) if losses else 0.0
    ratio_avg = avg_win / abs(avg_loss) if abs(avg_loss) > 1e-12 else 0.0

    longest_win, longest_loss, current = _calc_streak(trips)
    holding = _holding_stats(trips, wins, losses)
    long_short = _long_short_split(trips)
    activity = _activity(trips, total)

    return {
        "total_trades": total,
        "won": len(wins),
        "lost": len(losses),
        "breakeven": len(breakeven),
        "win_rate_pct": round(len(wins) / total * 100, 4),
        "gross_profit": round(gross_profit, 4),
        "gross_loss": round(gross_loss, 4),
        "net_profit": round(net_profit, 4),
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "ratio_avg_win_loss": round(ratio_avg, 4),
        "largest_win": round(float(max(pnls)), 4),
        "largest_loss": round(float(min(pnls)), 4),
        "avg_trade_pnl": round(net_profit / total, 4),
        "longest_win_streak": longest_win,
        "longest_loss_streak": longest_loss,
        "current_streak": current,
        **holding,
        **long_short,
        **activity,
        "round_trips": [t.to_row() for t in trips[:_ROW_CAP]],
    }


def _calc_streak(trips: list[RoundTrip]) -> tuple[int, int, int]:
    """
    freqtrade 风格连胜连败：连续同号分组，取每侧最长连续段。
    current_streak 为末尾同号连续段长度，胜为正、负为负。
    """
    longest_win = longest_loss = 0
    cur_win = cur_loss = 0
    for t in trips:
        if t.pnl > 0:
            cur_win += 1
            cur_loss = 0
        elif t.pnl < 0:
            cur_loss += 1
            cur_win = 0
        else:
            cur_win = cur_loss = 0
        longest_win = max(longest_win, cur_win)
        longest_loss = max(longest_loss, cur_loss)

    if cur_win > 0:
        current = cur_win
    elif cur_loss > 0:
        current = -cur_loss
    else:
        current = 0
    return longest_win, longest_loss, current


def _holding_stats(trips, wins, losses) -> dict:
    all_hd = [t.holding_days for t in trips]
    win_hd = [t.holding_days for t in wins]
    loss_hd = [t.holding_days for t in losses]
    return {
        "avg_holding_days": round(float(np.mean(all_hd)), 4) if all_hd else 0.0,
        "avg_winning_holding_days": round(float(np.mean(win_hd)), 4) if win_hd else 0.0,
        "avg_losing_holding_days": round(float(np.mean(loss_hd)), 4) if loss_hd else 0.0,
        "max_holding_days": round(float(max(all_hd)), 4) if all_hd else 0.0,
        "min_holding_days": round(float(min(all_hd)), 4) if all_hd else 0.0,
    }


def _long_short_split(trips: list[RoundTrip]) -> dict:
    total = len(trips)
    longs = [t for t in trips if t.direction == "long"]
    shorts = [t for t in trips if t.direction == "short"]
    long_wins = [t for t in longs if t.pnl > 0]
    short_wins = [t for t in shorts if t.pnl > 0]
    return {
        "long_count": len(longs),
        "short_count": len(shorts),
        "long_pct": round(len(longs) / total * 100, 4) if total else 0.0,
        "short_pct": round(len(shorts) / total * 100, 4) if total else 0.0,
        "win_rate_long_pct": round(len(long_wins) / len(longs) * 100, 4) if longs else 0.0,
        "win_rate_short_pct": round(len(short_wins) / len(shorts) * 100, 4) if shorts else 0.0,
        "long_pnl": round(float(sum(t.pnl for t in longs)), 4),
        "short_pnl": round(float(sum(t.pnl for t in shorts)), 4),
    }


def _activity(trips: list[RoundTrip], total: int) -> dict:
    first = min(t.entry_time for t in trips)
    last = max(t.exit_time for t in trips)
    duration_days = max((last - first).total_seconds() / 86400.0, 1e-6)
    per_day = total / duration_days
    return {
        "avg_trades_per_day": round(per_day, 4),
        "avg_trades_per_week": round(per_day * _DAYS_PER_WEEK, 4),
        "avg_trades_per_month": round(per_day * _DAYS_PER_MONTH, 4),
    }


def _empty() -> dict:
    return {
        "total_trades": 0, "won": 0, "lost": 0, "breakeven": 0, "win_rate_pct": 0.0,
        "gross_profit": 0.0, "gross_loss": 0.0, "net_profit": 0.0,
        "avg_win": 0.0, "avg_loss": 0.0, "ratio_avg_win_loss": 0.0,
        "largest_win": 0.0, "largest_loss": 0.0, "avg_trade_pnl": 0.0,
        "longest_win_streak": 0, "longest_loss_streak": 0, "current_streak": 0,
        "avg_holding_days": 0.0, "avg_winning_holding_days": 0.0,
        "avg_losing_holding_days": 0.0, "max_holding_days": 0.0, "min_holding_days": 0.0,
        "long_count": 0, "short_count": 0, "long_pct": 0.0, "short_pct": 0.0,
        "win_rate_long_pct": 0.0, "win_rate_short_pct": 0.0, "long_pnl": 0.0, "short_pnl": 0.0,
        "avg_trades_per_day": 0.0, "avg_trades_per_week": 0.0, "avg_trades_per_month": 0.0,
        "round_trips": [],
    }
