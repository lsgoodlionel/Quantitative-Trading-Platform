"""
回测扩展报告 API 端点 — C6 / C7

在既有同步回测之外，提供 pyfolio 式 tearsheet + 逐笔回合分析的五个扩展
section (trade_analytics / periodic_stats / rolling_stats / drawdown_periods /
tag_metrics)。

设计要点:
- 独立端点 POST /backtests/report，完全向后兼容，不改动既有 /backtests/run。
- 复用既有 BacktestRequest 校验、DataService 与 BacktestEngine。
- 五个 section 均为可空/带安全默认；数据不足时前端渲染空态。
"""

from __future__ import annotations

import uuid
from typing import Annotated

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.data.models import Market
from app.data.service import DataService
from app.engine.backtest.engine import BacktestEngine, BacktestConfig
from app.engine.backtest.metrics import (
    TRADING_DAYS_US, TRADING_DAYS_HK, TRADING_DAYS_A,
)
from app.engine.backtest.report_sections import build_extended_sections
from app.api.v1.endpoints.backtests import (
    BacktestRequest, get_service, _validate_and_fetch,
)
from app.strategy.presets import STRATEGY_REGISTRY

router = APIRouter()

_ROLLING_WINDOW = 63


# ── C7 逐笔回合 (TradeAnalytics) ─────────────────────────────────

class RoundTripRow(BaseModel):
    trip_id: int
    entry_time: str
    exit_time: str
    direction: str
    entry_tag: str
    exit_reason: str
    qty: float
    entry_price: float
    exit_price: float
    pnl: float
    pnl_pct: float
    commission: float
    holding_bars: int
    holding_days: float


class TradeAnalytics(BaseModel):
    total_trades: int
    won: int
    lost: int
    breakeven: int
    win_rate_pct: float
    gross_profit: float
    gross_loss: float
    net_profit: float
    avg_win: float
    avg_loss: float
    ratio_avg_win_loss: float
    largest_win: float
    largest_loss: float
    avg_trade_pnl: float
    longest_win_streak: int
    longest_loss_streak: int
    current_streak: int
    avg_holding_days: float
    avg_winning_holding_days: float
    avg_losing_holding_days: float
    max_holding_days: float
    min_holding_days: float
    long_count: int
    short_count: int
    long_pct: float
    short_pct: float
    win_rate_long_pct: float
    win_rate_short_pct: float
    long_pnl: float
    short_pnl: float
    avg_trades_per_day: float
    avg_trades_per_week: float
    avg_trades_per_month: float
    round_trips: list[RoundTripRow] = Field(default_factory=list)


# ── C6 周期分组 (PeriodicStats) ──────────────────────────────────

class PeriodBucket(BaseModel):
    label: str
    date_ts: int
    profit_abs: float
    profit_pct: float
    wins: int
    draws: int
    losses: int
    trades: int
    profit_factor: float


class PeriodicStats(BaseModel):
    daily: list[PeriodBucket] = Field(default_factory=list)
    weekly: list[PeriodBucket] = Field(default_factory=list)
    monthly: list[PeriodBucket] = Field(default_factory=list)
    weekday: list[PeriodBucket] = Field(default_factory=list)
    best_day: PeriodBucket | None = None
    worst_day: PeriodBucket | None = None
    best_month: PeriodBucket | None = None
    worst_month: PeriodBucket | None = None
    winning_days: int
    losing_days: int
    winning_weeks: int
    losing_weeks: int
    winning_months: int
    losing_months: int


# ── C7 Tearsheet 序列 (RollingStats) ─────────────────────────────

class SeriesPoint(BaseModel):
    time: str
    value: float


class RollingStats(BaseModel):
    window: int
    returns_series: list[SeriesPoint] = Field(default_factory=list)
    cum_returns: list[SeriesPoint] = Field(default_factory=list)
    rolling_sharpe: list[SeriesPoint] = Field(default_factory=list)
    rolling_volatility: list[SeriesPoint] = Field(default_factory=list)
    rolling_beta: list[SeriesPoint] = Field(default_factory=list)
    exposure_series: list[SeriesPoint] = Field(default_factory=list)
    turnover_series: list[SeriesPoint] = Field(default_factory=list)
    avg_exposure_pct: float
    total_turnover: float
    beta: float
    alpha_annual_pct: float


# ── C6 回撤区间 (DrawdownPeriod) ─────────────────────────────────

class DrawdownPeriod(BaseModel):
    rank: int
    peak_date: str
    valley_date: str
    recovery_date: str | None = None
    depth_pct: float
    length_days: int
    drawdown_days: int
    recovery_days: int | None = None
    max_underwater_days: int


# ── C6 标签分组 + 扩展风险比率 (TagMetrics) ──────────────────────

class TagRow(BaseModel):
    key: str
    trades: int
    wins: int
    draws: int
    losses: int
    win_rate_pct: float
    profit_abs: float
    profit_pct: float
    profit_factor: float
    avg_pnl: float
    avg_holding_days: float


class RiskRatios(BaseModel):
    cagr_pct: float
    ulcer_index: float
    serenity_index: float
    cvar_95_pct: float
    value_at_risk_95_pct: float
    max_underwater_days: int
    recovery_factor: float
    payoff_ratio: float
    tail_ratio: float
    common_sense_ratio: float
    kelly_criterion: float
    skew: float
    kurtosis: float
    downside_deviation_pct: float
    gain_to_pain_ratio: float
    avg_holding_period_days: float
    avg_up_month_pct: float
    avg_down_month_pct: float
    win_rate_long_pct: float
    win_rate_short_pct: float
    profit_factor_long: float
    profit_factor_short: float
    best_trade_pct: float
    worst_trade_pct: float


class TagMetrics(BaseModel):
    by_entry_tag: list[TagRow] = Field(default_factory=list)
    by_exit_reason: list[TagRow] = Field(default_factory=list)
    risk_ratios: RiskRatios


# ── 响应信封 ─────────────────────────────────────────────────────

class BacktestReportResponse(BaseModel):
    backtest_id: str
    strategy_name: str
    symbol: str
    market: str
    trade_analytics: TradeAnalytics | None = None
    periodic_stats: PeriodicStats | None = None
    rolling_stats: RollingStats | None = None
    drawdown_periods: list[DrawdownPeriod] = Field(default_factory=list)
    tag_metrics: TagMetrics | None = None


# ── 端点 ─────────────────────────────────────────────────────────

def _periods_per_year(market: Market) -> int:
    if market == Market.HK:
        return TRADING_DAYS_HK
    if market == Market.A:
        return TRADING_DAYS_A
    return TRADING_DAYS_US


def _benchmark_returns(bars, equity_index: pd.DatetimeIndex) -> pd.Series:
    """由 bar 收盘价构建买入持有基准日收益，对齐净值曲线索引。"""
    if not bars:
        return pd.Series(dtype=float)
    close = pd.Series(
        [float(b.close) for b in bars],
        index=pd.DatetimeIndex([b.time for b in bars]),
    ).sort_index()
    close = close[~close.index.duplicated(keep="last")]
    aligned = close.reindex(equity_index).ffill()
    return aligned.pct_change().fillna(0.0)


@router.post("/report", response_model=BacktestReportResponse)
async def backtest_report(
    body: BacktestRequest,
    svc: Annotated[DataService, Depends(get_service)],
) -> BacktestReportResponse:
    """
    运行回测并返回 tearsheet + 逐笔分析的五个扩展 section。

    与 /backtests/run 使用相同的请求体与引擎；此端点专注于 C6/C7 扩展分析，
    与既有响应完全解耦、向后兼容。
    """
    market, _frequency, bars = await _validate_and_fetch(
        body.strategy_name, body.market, body.frequency,
        body.start_date, body.end_date, svc, body.symbol,
    )

    strategy_cls = STRATEGY_REGISTRY[body.strategy_name]
    strategy = strategy_cls(params=body.params)
    config = BacktestConfig(initial_cash=body.initial_cash, market=market)
    engine = BacktestEngine(config)
    backtest_id = str(uuid.uuid4())

    try:
        result = engine.run(strategy, bars, strategy_id=backtest_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Backtest engine error: {e}")

    equity_curve = result.equity_curve
    benchmark_returns = _benchmark_returns(bars, equity_curve.index)
    bars_index = pd.DatetimeIndex([b.time for b in bars])

    sections = build_extended_sections(
        equity_curve=equity_curve,
        fills=result.fills,
        starting_balance=body.initial_cash,
        benchmark_returns=benchmark_returns,
        bars_index=bars_index,
        rolling_window=_ROLLING_WINDOW,
        periods_per_year=_periods_per_year(market),
    )

    return BacktestReportResponse(
        backtest_id=backtest_id,
        strategy_name=body.strategy_name,
        symbol=body.symbol,
        market=body.market,
        **sections,
    )
