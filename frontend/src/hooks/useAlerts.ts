import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"

export type AlertCondition = "above" | "below" | "pct_change"

export interface PriceAlert {
  id:           string
  symbol:       string
  market:       string
  condition:    AlertCondition
  threshold:    number
  base_price:   number | null
  note:         string
  is_active:    boolean
  is_triggered: boolean
  created_at:   string
  triggered_at: string | null
}

export interface CreateAlertRequest {
  symbol:     string
  market:     string
  condition:  AlertCondition
  threshold:  number
  base_price?: number | null
  note?:      string
}

export interface CheckAlertsRequest {
  prices: { symbol: string; market: string; price: number }[]
}

export interface CheckAlertsResult {
  triggered: PriceAlert[]
  count:     number
}

const QUERY_KEY = ["alerts"]

export function useAlerts() {
  return useQuery<PriceAlert[]>({
    queryKey: QUERY_KEY,
    queryFn: () => api.get<PriceAlert[]>("/api/v1/alerts"),
    refetchInterval: 10_000,
  })
}

export function useCreateAlert() {
  const qc = useQueryClient()
  return useMutation<PriceAlert, Error, CreateAlertRequest>({
    mutationFn: (req) => api.post<PriceAlert>("/api/v1/alerts", req),
    onSuccess: () => qc.invalidateQueries({ queryKey: QUERY_KEY }),
  })
}

export function useDeleteAlert() {
  const qc = useQueryClient()
  return useMutation<{ deleted: string }, Error, string>({
    mutationFn: (id) => api.delete<{ deleted: string }>(`/api/v1/alerts/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: QUERY_KEY }),
  })
}

export function useToggleAlert() {
  const qc = useQueryClient()
  return useMutation<PriceAlert, Error, { id: string; is_active: boolean }>({
    mutationFn: ({ id, is_active }) =>
      api.patch<PriceAlert>(`/api/v1/alerts/${id}`, { is_active }),
    onSuccess: () => qc.invalidateQueries({ queryKey: QUERY_KEY }),
  })
}

export function useCheckAlerts() {
  const qc = useQueryClient()
  return useMutation<CheckAlertsResult, Error, CheckAlertsRequest>({
    mutationFn: (req) => api.post<CheckAlertsResult>("/api/v1/alerts/check", req),
    onSuccess: () => qc.invalidateQueries({ queryKey: QUERY_KEY }),
  })
}

export function useResetAlert() {
  const qc = useQueryClient()
  return useMutation<PriceAlert, Error, string>({
    mutationFn: (id) => api.post<PriceAlert>(`/api/v1/alerts/${id}/reset`),
    onSuccess: () => qc.invalidateQueries({ queryKey: QUERY_KEY }),
  })
}
