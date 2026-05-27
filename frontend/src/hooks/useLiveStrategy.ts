import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type { LiveStrategyInstance, StartStrategyRequest } from "@/types"

const QK = ["live-strategies"] as const

export function useLiveStrategies() {
  return useQuery<LiveStrategyInstance[]>({
    queryKey: QK,
    queryFn: () => api.get<LiveStrategyInstance[]>("/api/v1/live-strategies/"),
    refetchInterval: 5_000,   // 每 5 秒刷新状态
  })
}

export function useStartStrategy() {
  const qc = useQueryClient()
  return useMutation<LiveStrategyInstance, Error, StartStrategyRequest>({
    mutationFn: (req) =>
      api.post<LiveStrategyInstance>("/api/v1/live-strategies/start", req),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK })
    },
  })
}

export function useStopStrategy() {
  const qc = useQueryClient()
  return useMutation<LiveStrategyInstance, Error, string>({
    mutationFn: (instanceId) =>
      api.post<LiveStrategyInstance>(`/api/v1/live-strategies/${instanceId}/stop`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK })
    },
  })
}

export function useDeleteStrategyInstance() {
  const qc = useQueryClient()
  return useMutation<void, Error, string>({
    mutationFn: (instanceId) =>
      api.delete<void>(`/api/v1/live-strategies/${instanceId}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: QK })
    },
  })
}
