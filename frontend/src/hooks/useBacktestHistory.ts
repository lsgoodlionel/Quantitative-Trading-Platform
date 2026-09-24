import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"

// ── 回测历史（V3 · H5）─────────────────────────────────────────
// 后端落 TimescaleDB，无容量上限；净值曲线降采样至 1000 点后整条落库。

const BASE = "/api/v1/backtests/history"

export interface EquityPoint {
  time: string
  value: number
}

export interface HistorySummary {
  id: string
  name: string
  strategy_name: string
  symbol: string
  market: string
  frequency: string
  start_date: string
  end_date: string
  initial_cash: number
  final_value: number
  params: Record<string, unknown>
  metrics: Record<string, number>
  curve_points: number
  curve_downsampled: boolean
  note: string
  created_at: string
}

export interface HistoryDetail extends HistorySummary {
  equity_curve: EquityPoint[]
}

export interface HistoryListResult {
  total: number
  limit: number
  offset: number
  items: HistorySummary[]
  storage: string
}

export interface HistoryFilters {
  strategy_name?: string
  symbol?: string
  market?: string
  limit?: number
  offset?: number
}

export interface SaveHistoryRequest {
  strategy_name: string
  symbol: string
  market: string
  frequency: string
  start_date: string
  end_date: string
  initial_cash: number
  final_value: number
  params: Record<string, unknown>
  metrics: Record<string, number>
  equity_curve: EquityPoint[]
  name?: string
  note?: string
}

export interface RerunResult {
  source_id: string
  config: Record<string, unknown>
  final_value: number
  metrics: Record<string, number>
  equity_curve: EquityPoint[]
  metrics_changed: boolean
}

export interface CompareSeries {
  id: string
  name: string
  strategy_name: string
  symbol: string
  initial_cash: number
  values: (number | null)[]
}

export interface CompareResult {
  axis: string[]
  series: CompareSeries[]
  metrics: { keys: string[]; rows: { id: string; name: string; values: Record<string, number | null> }[] }
  missing_ids: string[]
}

function toQuery(filters: HistoryFilters): string {
  const params = new URLSearchParams()
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== "") params.set(key, String(value))
  })
  const query = params.toString()
  return query ? `?${query}` : ""
}

export function useBacktestHistoryList(filters: HistoryFilters = {}) {
  return useQuery<HistoryListResult>({
    queryKey: ["backtest-history", filters],
    queryFn: () => api.get<HistoryListResult>(`${BASE}${toQuery(filters)}`),
  })
}

export function useBacktestHistoryDetail(id: string | null) {
  return useQuery<HistoryDetail>({
    queryKey: ["backtest-history", "detail", id],
    queryFn: () => api.get<HistoryDetail>(`${BASE}/${id}`),
    enabled: Boolean(id),
  })
}

export function useSaveBacktestHistory() {
  const queryClient = useQueryClient()
  return useMutation<HistoryDetail, Error, SaveHistoryRequest>({
    mutationFn: (req) => api.post<HistoryDetail>(BASE, req),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["backtest-history"] }),
  })
}

export function useDeleteBacktestHistory() {
  const queryClient = useQueryClient()
  return useMutation<void, Error, string>({
    mutationFn: (id) => api.delete<void>(`${BASE}/${id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["backtest-history"] }),
  })
}

export function useRerunBacktestHistory() {
  return useMutation<RerunResult, Error, string>({
    mutationFn: (id) => api.post<RerunResult>(`${BASE}/${id}/rerun`),
  })
}

export function useCompareBacktestHistory(ids: string[]) {
  return useQuery<CompareResult>({
    queryKey: ["backtest-history", "compare", ids],
    queryFn: () => api.get<CompareResult>(`${BASE}/compare?ids=${ids.join(",")}`),
    enabled: ids.length >= 2,
  })
}
