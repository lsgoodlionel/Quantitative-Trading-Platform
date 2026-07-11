import { useMutation, useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"

// ══════════════════════════════════════════════════════════════════
// 策略验证与稳健性（Wave 2 · C1/C2/C3）
// 对应后端 backend/app/api/v1/endpoints/backtest_validation.py
// ══════════════════════════════════════════════════════════════════

// ── C1 Hyperopt 参数优化 ─────────────────────────────────────────

export interface LossFunctionInfo {
  name: string
  label: string
}

/** 参数空间：值为离散列表或连续区间定义 */
export type ParamSpaceDef = Record<
  string,
  number[] | { low: number; high: number; step?: number; type?: "int" | "float" } | { choices: unknown[] }
>

export interface HyperoptRequest {
  strategy_name: string
  symbol: string
  market: string
  frequency: string
  start_date: string
  end_date: string
  initial_cash: number
  param_space: ParamSpaceDef
  algorithm: "grid" | "random" | "bayesian"
  loss_function: string
  n_trials: number
  min_trades: number
  seed?: number
}

export interface HyperoptTrial {
  params: Record<string, unknown>
  score: number
  total_return_pct: number
  annual_return_pct: number
  sharpe_ratio: number
  max_drawdown_pct: number
  total_trades: number
}

export interface HyperoptResult {
  algorithm: string
  loss_function: string
  best_params: Record<string, unknown>
  best_score: number
  best_metrics: Record<string, number>
  total_space: number
  evaluated: number
  used_fallback: boolean
  trials: HyperoptTrial[]
}

export function useLossFunctions() {
  return useQuery<LossFunctionInfo[]>({
    queryKey: ["hyperopt-loss-functions"],
    queryFn: () => api.get<LossFunctionInfo[]>("/api/v1/backtests/hyperopt/loss-functions"),
    staleTime: Infinity,
  })
}

export function useHyperopt() {
  return useMutation<HyperoptResult, Error, HyperoptRequest>({
    mutationFn: (req) => api.post<HyperoptResult>("/api/v1/backtests/hyperopt", req),
  })
}

// ── C2 Walk-Forward 分析 ─────────────────────────────────────────

export interface WalkForwardRequest {
  strategy_name: string
  symbol: string
  market: string
  frequency: string
  start_date: string
  end_date: string
  initial_cash: number
  param_space: ParamSpaceDef
  train_size: number
  test_size: number
  mode: "rolling" | "anchored"
  algorithm: "grid" | "random" | "bayesian"
  loss_function: string
  inner_trials: number
  seed?: number
}

export interface WalkForwardWindow {
  index: number
  train_start: string
  train_end: string
  test_start: string
  test_end: string
  train_bars: number
  test_bars: number
  best_params: Record<string, unknown>
  is_sharpe: number
  oos_sharpe: number
  is_return_pct: number
  oos_return_pct: number
  oos_max_drawdown_pct: number
  oos_total_trades: number
}

export interface WalkForwardResult {
  mode: string
  train_size: number
  test_size: number
  total_windows: number
  avg_is_sharpe: number
  avg_oos_sharpe: number
  avg_is_return_pct: number
  avg_oos_return_pct: number
  oos_is_efficiency: number
  oos_consistency: number
  oos_win_windows: number
  windows: WalkForwardWindow[]
}

export function useWalkForward() {
  return useMutation<WalkForwardResult, Error, WalkForwardRequest>({
    mutationFn: (req) => api.post<WalkForwardResult>("/api/v1/backtests/walkforward", req),
  })
}

// ── C3 前视 / 递归偏差检测 ───────────────────────────────────────

export interface BiasCheckRequest {
  strategy_name: string
  symbol: string
  market: string
  frequency: string
  start_date: string
  end_date: string
  initial_cash: number
  params: Record<string, unknown>
  startup_candles: number[]
  lookahead_cut_ratio: number
}

export interface SignalDiff {
  checked_signals: number
  changed_signals: number
  detail: string
}

export interface RecursiveDiff {
  startup_candle: number
  checked_signals: number
  changed_signals: number
}

export interface BiasCheckResult {
  has_lookahead_bias: boolean
  has_recursive_bias: boolean
  total_signals: number
  lookahead: SignalDiff
  recursive: RecursiveDiff[]
  notes: string[]
}

export function useBiasCheck() {
  return useMutation<BiasCheckResult, Error, BiasCheckRequest>({
    mutationFn: (req) => api.post<BiasCheckResult>("/api/v1/backtests/bias-check", req),
  })
}
