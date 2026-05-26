import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type { RiskConfig, RiskViolation } from "@/types"

interface RiskSummary {
  daily_orders_submitted: number
  peak_equity: number
  current_equity: number | null
  daily_realized_pnl: number
  violations: RiskViolation[]
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
