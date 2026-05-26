import { useMutation } from "@tanstack/react-query"
import { api } from "@/lib/api"

// ── GBM ──────────────────────────────────────────────────────────
export interface GBMRequest {
  S0: number; mu: number; sigma: number; T: number
  n_paths?: number; n_steps?: number; seed?: number
}
export interface GBMResult {
  S0: number; mu: number; sigma: number; T: number
  sample_paths: number[][]
  final_mean: number; final_std: number; final_median: number
  final_p5: number; final_p95: number
  var_95: number; cvar_95: number; prob_loss: number; expected_return: number
  time_axis: number[]
}
export const useGBM = () =>
  useMutation<GBMResult, Error, GBMRequest>({
    mutationFn: (req) => api.post<GBMResult>("/api/v1/quant/gbm", req),
  })

// ── BSM ──────────────────────────────────────────────────────────
export interface BSMRequest {
  S: number; K: number; r: number; sigma: number; T: number
  q?: number; option_type?: "call" | "put"
}
export interface BSMResult {
  option_type: string; S: number; K: number; r: number; sigma: number; T: number; q: number
  price: number; intrinsic_value: number; time_value: number
  delta: number; gamma: number; theta: number; vega: number; rho: number
  d1: number; d2: number; nd1: number; nd2: number
}
export const useBSM = () =>
  useMutation<BSMResult, Error, BSMRequest>({
    mutationFn: (req) => api.post<BSMResult>("/api/v1/quant/bsm", req),
  })

// ── GARCH ─────────────────────────────────────────────────────────
export interface GARCHRequest { returns: number[]; forecast_horizon?: number }
export interface GARCHResult {
  omega: number; alpha: number; beta: number
  log_likelihood: number; aic: number; bic: number
  long_run_vol_annualized: number; persistence: number; half_life_days: number
  conditional_vol: number[]; forecast_vol: number[]
}
export const useGARCH = () =>
  useMutation<GARCHResult, Error, GARCHRequest>({
    mutationFn: (req) => api.post<GARCHResult>("/api/v1/quant/garch", req),
  })

// ── Kelly ─────────────────────────────────────────────────────────
export interface KellyRequest {
  win_rate: number; avg_win: number; avg_loss: number
  fraction?: number; max_f?: number
}
export interface KellyResult {
  win_rate: number; avg_win: number; avg_loss: number; odds_ratio: number; edge: number
  full_kelly: number; half_kelly: number; quarter_kelly: number; recommended: number
  kelly_continuous: number; ruin_probability_full: number; ruin_probability_half: number
  growth_curve: { f: number; expected_log_growth: number }[]
}
export const useKelly = () =>
  useMutation<KellyResult, Error, KellyRequest>({
    mutationFn: (req) => api.post<KellyResult>("/api/v1/quant/kelly", req),
  })

// ── Cointegration ─────────────────────────────────────────────────
export interface CointRequest {
  y: number[]; x: number[]; lookback?: number
  entry_z?: number; exit_z?: number; use_log?: boolean
}
export interface CointResult {
  hedge_ratio: number; intercept: number; adf_stat: number; adf_pvalue: number; is_cointegrated: boolean
  spread_mean: number; spread_std: number; spread_last: number
  z_score_last: number; signal: string
  spread_series: number[]; z_score_series: number[]
  n_observations: number; correlation: number; half_life_days: number
}
export const useCointegration = () =>
  useMutation<CointResult, Error, CointRequest>({
    mutationFn: (req) => api.post<CointResult>("/api/v1/quant/cointegration", req),
  })

// ── HMM ──────────────────────────────────────────────────────────
export interface HMMRequest { returns: number[]; n_states?: number; n_iterations?: number }
export interface HMMResult {
  n_states: number; n_observations: number
  initial_probs: number[]; transition_matrix: number[][]
  state_means: number[]; state_vols: number[]
  state_sequence: number[]
  current_state: number; current_state_prob: number; state_labels: string[]
  log_likelihood: number; n_iterations: number
}
export const useHMM = () =>
  useMutation<HMMResult, Error, HMMRequest>({
    mutationFn: (req) => api.post<HMMResult>("/api/v1/quant/hmm", req),
  })
