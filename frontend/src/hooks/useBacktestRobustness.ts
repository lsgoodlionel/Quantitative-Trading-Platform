import { useMutation } from "@tanstack/react-query"
import { api } from "@/lib/api"

// ══════════════════════════════════════════════════════════════════
// 蒙特卡洛稳健性 & 统计显著性（Wave 3 · C4/C5）
// 对应后端 backend/app/api/v1/endpoints/backtest_robustness.py
// ══════════════════════════════════════════════════════════════════

// ── C4 蒙特卡洛稳健性（逐笔重采样）────────────────────────────────

export type McMethod = "bootstrap" | "shuffle"

export interface McRobustnessRequest {
  strategy_name: string
  symbol: string
  market: string
  frequency: string
  start_date: string
  end_date: string
  initial_cash: number
  params: Record<string, unknown>
  method: McMethod
  n_scenarios: number
  seed?: number
}

export interface McMetricStat {
  name: string
  original: number
  mean: number
  std: number
  min: number
  max: number
  p5: number
  p25: number
  p50: number
  p75: number
  p95: number
  ci90_lower: number
  ci90_upper: number
  ci95_lower: number
  ci95_upper: number
  p_value: number
  is_significant_5pct: boolean
  is_significant_1pct: boolean
}

export interface McEnvelopePoint {
  step: number
  p5: number
  p25: number
  p50: number
  p75: number
  p95: number
}

export interface McRobustnessResult {
  method: string
  n_scenarios: number
  n_trades: number
  prob_profit: number
  prob_beat_original: number
  metrics: McMetricStat[]
  envelope: McEnvelopePoint[]
  original_curve: number[]
}

export function useMcRobustness() {
  return useMutation<McRobustnessResult, Error, McRobustnessRequest>({
    mutationFn: (req) => api.post<McRobustnessResult>("/api/v1/backtests/mc-robustness", req),
  })
}

// ── C5 统计显著性检验（Bootstrap 假设检验 + 规则贡献度）──────────

export interface SignificanceRequest {
  strategy_name: string
  symbol: string
  market: string
  frequency: string
  start_date: string
  end_date: string
  initial_cash: number
  params: Record<string, unknown>
  n_simulations: number
  seed?: number
}

export interface RuleContribution {
  entry_tag: string
  n_trades: number
  total_pnl: number
  pnl_share_pct: number
  mean_pnl: number
  win_rate: number
  p_value: number
  is_significant_5pct: boolean
  tested: boolean
}

export interface NullHistBin {
  center: number
  count: number
}

export interface SignificanceResult {
  n_trades: number
  n_simulations: number
  observed_mean_pnl: number
  observed_total_pnl: number
  win_rate: number
  t_stat: number
  effect_size: number
  p_value: number
  is_significant_5pct: boolean
  is_significant_1pct: boolean
  ci95_mean_lower: number
  ci95_mean_upper: number
  null_hist: NullHistBin[]
  observed_marker: number
  rule_contributions: RuleContribution[]
}

export function useSignificance() {
  return useMutation<SignificanceResult, Error, SignificanceRequest>({
    mutationFn: (req) => api.post<SignificanceResult>("/api/v1/backtests/significance", req),
  })
}

// ── 指标中文标签 ─────────────────────────────────────────────────

export const MC_METRIC_LABELS: Record<string, string> = {
  total_return_pct: "总收益率 (%)",
  max_drawdown_pct: "最大回撤 (%)",
  sharpe_ratio: "夏普 (逐笔)",
  profit_factor: "盈亏比",
}
