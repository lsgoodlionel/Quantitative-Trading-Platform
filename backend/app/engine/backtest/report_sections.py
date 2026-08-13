"""
扩展报告编排器 (Extended Report Orchestrator) — C6/C7

调用各分析模块，返回可直接拼入回测响应的八个可空 section。
保持 report.py 精简，所有新增分析逻辑集中在此汇总。

返回 keys (全部可空/带安全默认):
    trade_analytics / periodic_stats / rolling_stats / drawdown_periods / tag_metrics
    capacity_analysis / crisis_windows / rejected_signals   ← Wave N-a 追加

不变量: < 2 笔回合或 < 2 个周期时对应 section 返回 None / 空列表，供前端渲染空态。
N-a 的三个 section 同样遵循这条：缺少日结/净值/拒单台账时返回 None，
**不返回一堆 0** —— 0 会被读成「有数据但为零」，与「没数据」是两回事。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

from app.data.models import Bar
from app.engine.backtest.capacity import build_capacity_section
from app.engine.backtest.crisis import build_crisis_section
from app.engine.backtest.daily_result import PortfolioDailyResult
from app.engine.backtest.drawdown_periods import compute_drawdown_periods
from app.engine.backtest.periodic_stats import compute_periodic_stats
from app.engine.backtest.reject_reasons import build_rejected_signal_section
from app.engine.backtest.rolling_stats import compute_rolling_stats
from app.engine.backtest.roundtrips import build_round_trips
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
    *,
    daily_results: Sequence[PortfolioDailyResult] | None = None,
    bars_by_symbol: Mapping[str, Sequence[Bar]] | None = None,
    rejections: Sequence[Any] | None = None,
    rejection_overflow: int = 0,
    market: str | None = None,
) -> dict:
    """构建八个扩展 section；数据不足的 section 以 None/[] 收尾。

    N-a 的三个新 section 都由**关键字参数**驱动，调用方不传就是 None ——
    既有调用点（单标的 `/backtests/report`）无需改动即可保持原返回体。
    """
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
        **_wave_na_sections(
            equity_curve if has_curve else None,
            daily_results,
            bars_by_symbol,
            rejections,
            rejection_overflow,
            market,
            starting_balance,
        ),
    })


def _wave_na_sections(
    equity_curve: pd.Series | None,
    daily_results: Sequence[PortfolioDailyResult] | None,
    bars_by_symbol: Mapping[str, Sequence[Bar]] | None,
    rejections: Sequence[Any] | None,
    rejection_overflow: int,
    market: str | None,
    starting_balance: float,
) -> dict:
    """N2 容量·换手·杠杆 / N3 危机区间 / N4 拒绝信号 三个可空 section。"""
    capacity = (
        build_capacity_section(
            daily_results,
            equity_curve if equity_curve is not None else pd.Series(dtype=float),
            bars_by_symbol or {},
            reference_equity=starting_balance,
        )
        if daily_results else None
    )
    crisis = (
        build_crisis_section(equity_curve, market)
        if equity_curve is not None and market else None
    )
    rejected = build_rejected_signal_section(
        rejections or [], overflow=rejection_overflow
    )
    return {
        "capacity_analysis": capacity,
        "crisis_windows": crisis,
        "rejected_signals": rejected,
    }


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
