import { useMutation, useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type { BacktestRequest, BacktestResult, Strategy } from "@/types"

export function useStrategies() {
  return useQuery<Strategy[]>({
    queryKey: ["strategies"],
    queryFn: () => api.get<Strategy[]>("/api/v1/strategies/presets"),
    staleTime: Infinity,
  })
}

export function useRunBacktest() {
  return useMutation<BacktestResult, Error, BacktestRequest>({
    mutationFn: (req) => api.post<BacktestResult>("/api/v1/backtests/run", req),
  })
}
