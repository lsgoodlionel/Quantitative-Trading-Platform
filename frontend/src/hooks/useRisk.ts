import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type { RiskConfig, RiskViolation } from "@/types"

// Field names match what backend risk/engine.daily_summary() actually returns
interface RiskSummary {
  date: string
  orders_today: number
  realized_pnl_today: number
  peak_portfolio_value: number
  violations?: RiskViolation[]
}

interface PreTradeCheckRequest {
  symbol: string
  market: string
  side: string
  qty: number
  price: number
  strategy_id?: string | null
}

export function useRiskConfig() {
  return useQuery<RiskConfig>({
    queryKey: ["risk"],
    queryFn: () => api.get<RiskConfig>("/api/v1/risk"),
  })
}

export function useRiskSummary() {
  return useQuery<RiskSummary>({
    queryKey: ["risk-summary"],
    queryFn: () => api.get<RiskSummary>("/api/v1/risk/summary"),
    refetchInterval: 15_000,
  })
}

export function useUpdateRiskConfig() {
  const qc = useQueryClient()
  return useMutation<RiskConfig, Error, RiskConfig>({
    mutationFn: (config) => api.put<RiskConfig>("/api/v1/risk", config),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["risk"] })
      qc.invalidateQueries({ queryKey: ["risk-summary"] })
    },
  })
}

export function usePreTradeCheck() {
  return useMutation<{ passed: boolean; violations: RiskViolation[] }, Error, PreTradeCheckRequest>({
    mutationFn: (req) =>
      api.post<{ passed: boolean; violations: RiskViolation[] }>("/api/v1/risk/check/pre-trade", req),
  })
}

interface VaRPosition { symbol: string; market: string; weight: number }

export interface VaRResult {
  hist_var_95_pct:    number
  hist_var_99_pct:    number
  hist_cvar_95_pct:   number
  hist_cvar_99_pct:   number
  hist_var_95_value:  number
  hist_var_99_value:  number
  hist_cvar_95_value: number
  hist_cvar_99_value: number
  param_var_95_pct:   number
  param_var_99_pct:   number
  param_cvar_95_pct:  number
  param_cvar_99_pct:  number
  mean_return_pct:    number
  std_return_pct:     number
  skewness:           number
  kurtosis:           number
  min_return_pct:     number
  max_return_pct:     number
  portfolio_value:    number
  n_days:             number
  weights:            Record<string, number>
  return_series:      number[]
}

export function useVaRAnalysis() {
  return useMutation<VaRResult, Error, { positions: VaRPosition[]; portfolio_value: number; lookback_days?: number }>({
    mutationFn: (req) => api.post<VaRResult>("/api/v1/risk/var", req),
  })
}
