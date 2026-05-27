import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"

export interface BrokerStatus {
  gateway: string
  configured: boolean
  key_hint: string | null
  base_url: string | null
  paper_mode: boolean
}

export interface AllBrokerConfig {
  alpaca: BrokerStatus
}

export interface AlpacaSaveRequest {
  api_key: string
  api_secret: string
  base_url: string
  paper_mode: boolean
}

export interface TestConnectionResponse {
  ok: boolean
  account_id: string | null
  buying_power: number | null
  error: string | null
}

export function useBrokerConfig() {
  return useQuery<AllBrokerConfig>({
    queryKey: ["broker-config"],
    queryFn: () => api.get<AllBrokerConfig>("/api/v1/broker-config"),
  })
}

export function useSaveAlpacaConfig() {
  const qc = useQueryClient()
  return useMutation<BrokerStatus, Error, AlpacaSaveRequest>({
    mutationFn: (req) => api.post<BrokerStatus>("/api/v1/broker-config/alpaca", req),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["broker-config"] }),
  })
}

export function useDeleteAlpacaConfig() {
  const qc = useQueryClient()
  return useMutation<void, Error, void>({
    mutationFn: () => api.delete("/api/v1/broker-config/alpaca"),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["broker-config"] }),
  })
}

export function useTestAlpacaConnection() {
  return useMutation<TestConnectionResponse, Error, void>({
    mutationFn: () => api.post<TestConnectionResponse>("/api/v1/broker-config/alpaca/test"),
  })
}
