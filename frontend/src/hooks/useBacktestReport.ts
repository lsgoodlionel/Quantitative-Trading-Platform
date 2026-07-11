import { useMutation } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type { BacktestRequest } from "@/types"

// ── C7 逐笔回合 (TradeAnalytics) ─────────────────────────────────
export interface RoundTripRow {
  trip_id: number
  entry_time: string
  exit_time: string
  direction: "long" | "short"
  entry_tag: string
  exit_reason: string
  qty: number
  entry_price: number
  exit_price: number
  pnl: number
  pnl_pct: number
  commission: number
  holding_bars: number
  holding_days: number
}

export interface TradeAnalytics {
  total_trades: number
  won: number
  lost: number
  breakeven: number
  win_rate_pct: number
  gross_profit: number
  gross_loss: number
  net_profit: number
  avg_win: number
  avg_loss: number
  ratio_avg_win_loss: number
  largest_win: number
  largest_loss: number
  avg_trade_pnl: number
  longest_win_streak: number
  longest_loss_streak: number
  current_streak: number
  avg_holding_days: number
  avg_winning_holding_days: number
  avg_losing_holding_days: number
  max_holding_days: number
  min_holding_days: number
  long_count: number
  short_count: number
  long_pct: number
  short_pct: number
  win_rate_long_pct: number
  win_rate_short_pct: number
  long_pnl: number
  short_pnl: number
  avg_trades_per_day: number
  avg_trades_per_week: number
  avg_trades_per_month: number
  round_trips: RoundTripRow[]
}

// ── C6 周期分组 (PeriodicStats) ──────────────────────────────────
export interface PeriodBucket {
  label: string
  date_ts: number
  profit_abs: number
  profit_pct: number
  wins: number
  draws: number
  losses: number
  trades: number
  profit_factor: number
}

export interface PeriodicStats {
  daily: PeriodBucket[]
  weekly: PeriodBucket[]
  monthly: PeriodBucket[]
  weekday: PeriodBucket[]
  best_day: PeriodBucket | null
  worst_day: PeriodBucket | null
  best_month: PeriodBucket | null
  worst_month: PeriodBucket | null
  winning_days: number
  losing_days: number
  winning_weeks: number
  losing_weeks: number
  winning_months: number
  losing_months: number
}

// ── C7 Tearsheet 序列 (RollingStats) ─────────────────────────────
export interface SeriesPoint {
  time: string
  value: number
}

export interface RollingStats {
  window: number
  returns_series: SeriesPoint[]
  cum_returns: SeriesPoint[]
  rolling_sharpe: SeriesPoint[]
  rolling_volatility: SeriesPoint[]
  rolling_beta: SeriesPoint[]
  exposure_series: SeriesPoint[]
  turnover_series: SeriesPoint[]
  avg_exposure_pct: number
  total_turnover: number
  beta: number
  alpha_annual_pct: number
}

// ── C6 回撤区间 (DrawdownPeriod) ─────────────────────────────────
export interface DrawdownPeriod {
  rank: number
  peak_date: string
  valley_date: string
  recovery_date: string | null
  depth_pct: number
  length_days: number
  drawdown_days: number
  recovery_days: number | null
  max_underwater_days: number
}

// ── C6 标签分组 + 扩展风险比率 (TagMetrics) ──────────────────────
export interface TagRow {
  key: string
  trades: number
  wins: number
  draws: number
  losses: number
  win_rate_pct: number
  profit_abs: number
  profit_pct: number
  profit_factor: number
  avg_pnl: number
  avg_holding_days: number
}

export interface RiskRatios {
  cagr_pct: number
  ulcer_index: number
  serenity_index: number
  cvar_95_pct: number
  value_at_risk_95_pct: number
  max_underwater_days: number
  recovery_factor: number
  payoff_ratio: number
  tail_ratio: number
  common_sense_ratio: number
  kelly_criterion: number
  skew: number
  kurtosis: number
  downside_deviation_pct: number
  gain_to_pain_ratio: number
  avg_holding_period_days: number
  avg_up_month_pct: number
  avg_down_month_pct: number
  win_rate_long_pct: number
  win_rate_short_pct: number
  profit_factor_long: number
  profit_factor_short: number
  best_trade_pct: number
  worst_trade_pct: number
}

export interface TagMetrics {
  by_entry_tag: TagRow[]
  by_exit_reason: TagRow[]
  risk_ratios: RiskRatios
}

// ── 扩展报告响应 ─────────────────────────────────────────────────
export interface BacktestReport {
  backtest_id: string
  strategy_name: string
  symbol: string
  market: string
  trade_analytics?: TradeAnalytics | null
  periodic_stats?: PeriodicStats | null
  rolling_stats?: RollingStats | null
  drawdown_periods: DrawdownPeriod[]
  tag_metrics?: TagMetrics | null
}

/** 运行回测并返回 tearsheet + 逐笔分析扩展 section */
export function useBacktestReport() {
  return useMutation<BacktestReport, Error, BacktestRequest>({
    mutationFn: (req) => api.post<BacktestReport>("/api/v1/backtests/report", req),
  })
}
