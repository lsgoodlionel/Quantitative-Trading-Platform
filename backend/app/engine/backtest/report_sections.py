"""
扩展报告编排器 (Extended Report Orchestrator) — C6/C7

调用六个分析模块，返回可直接拼入回测响应的五个可空 section。
保持 report.py 精简，所有新增分析逻辑集中在此汇总。

返回 keys (全部可空/带安全默认):
    trade_analytics / periodic_stats / rolling_stats / drawdown_periods / tag_metrics

不变量: < 2 笔回合或 < 2 个周期时对应 section 返回 None / 空列表，供前端渲染空态。
"""

from __future__ import annotations

import math

import pandas as pd

from app.engine.backtest.drawdown_periods import compute_drawdown_periods
from app.engine.backtest.periodic_stats import compute_periodic_stats
from app.engine.backtest.roundtrips import build_round_trips
from app.engine.backtest.rolling_stats import compute_rolling_stats
from app.engine.backtest.tag_metrics import compute_tag_metrics
from app.engine.backtest.trade_analytics import compute_trade_analytics

_MIN_TRIPS = 2
_MIN_POINTS = 2


def build_extended_sections(
    equity_curve: pd.Series,
    fills: list[dict],
    starting_balance: float,
    benchmark_returns: pd.Series | None = None,
    bars_index: pd.DatetimeIndex | None = None,
    rolling_window: int = 63,
    periods_per_year: int = 252,
) -> dict:
    """构建五个扩展 section；数据不足的 section 以 None/[] 收尾。"""
    trips = build_round_trips(fills, bars_index=bars_index)
    has_trips = len(trips) >= _MIN_TRIPS
    has_curve = equity_curve is not None and len(equity_curve) >= _MIN_POINTS

    returns = equity_curve.pct_change().dropna() if has_curve else pd.Series(dtype=float)

    trade_analytics = (
        compute_trade_analytics(trips, starting_balance) if has_trips else None
    )
    periodic_stats = (
        compute_periodic_stats(trips, equity_curve) if has_trips and has_curve else None
    )
    rolling_stats = (
        compute_rolling_stats(
            equity_curve, returns, benchmark_returns, fills,
            window=rolling_window, periods_per_year=periods_per_year,
        )
        if has_curve else None
    )
    drawdown_periods = (
        compute_drawdown_periods(equity_curve) if has_curve else []
    )
    tag_metrics = (
        compute_tag_metrics(trips, returns, equity_curve, starting_balance, periods_per_year)
        if has_trips and has_curve else None
    )

    return _json_safe({
        "trade_analytics": trade_analytics,
        "periodic_stats": periodic_stats,
        "rolling_stats": rolling_stats,
        "drawdown_periods": drawdown_periods,
        "tag_metrics": tag_metrics,
    })


def _json_safe(obj):
    """递归清洗 NaN/Inf → 0.0，保证 JSON 序列化合法。"""
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return 0.0
        return obj
    return obj
