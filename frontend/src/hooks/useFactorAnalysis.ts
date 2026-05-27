import { useMutation, useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type { Market, Frequency } from "@/types"

export interface FactorInfo {
  name: string
  label: string
  group: string
}

export interface FactorAnalysisRequest {
  symbol: string
  market: Market
  frequency: Frequency
  start?: string
  end?: string
  factor_name: string
  forward_periods: number[]
}

export interface IcPoint { time: string; ic: number }
export interface CumIcPoint { time: string; cum_ic: number }
export interface FactorPoint { time: string; value: number }

export interface FactorAnalysisResult {
  symbol: string
  market: string
  factor_name: string
  forward_periods: number[]
  factor_series: FactorPoint[]
  ic_series: Record<string, IcPoint[]>
  cumulative_ic: Record<string, CumIcPoint[]>
  ic_mean: Record<string, number>
  ic_std: Record<string, number>
  ic_ir: Record<string, number>
  ic_positive_rate: Record<string, number>
  ic_abs_mean: Record<string, number>
  quantile_returns: Record<string, number[]>
}

export function useFactorList() {
  return useQuery<FactorInfo[]>({
    queryKey: ["factor-list"],
    queryFn: () => api.get<FactorInfo[]>("/api/v1/quant/factor/list"),
    staleTime: Infinity,
  })
}

export function useFactorAnalysis() {
  return useMutation<FactorAnalysisResult, Error, FactorAnalysisRequest>({
    mutationFn: (req) => api.post<FactorAnalysisResult>("/api/v1/quant/factor/analyze", req),
  })
}
