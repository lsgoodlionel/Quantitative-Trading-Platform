import { useMutation, useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type {
  BacktestRequest, BacktestResult, Strategy,
  OptimizeRequest, OptimizeResult, MonteCarloResult,
} from "@/types"

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

export function useOptimize() {
  return useMutation<OptimizeResult, Error, OptimizeRequest>({
    mutationFn: (req) => api.post<OptimizeResult>("/api/v1/backtests/optimize", req),
  })
}

export function useMonteCarlo() {
  return useMutation<MonteCarloResult, Error, BacktestRequest & { n_simulations?: number; seed?: number }>({
    mutationFn: (req) => api.post<MonteCarloResult>("/api/v1/backtests/montecarlo", req),
  })
}
