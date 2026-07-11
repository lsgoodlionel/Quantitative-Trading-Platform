import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type { Market, OrderSide, OrderType } from "@/types"

// ── 类型定义（W3d 高级订单算法）──────────────────────────────

export type AlgoType = "TWAP" | "VWAP" | "ICEBERG"

export type AlgoStatus =
  | "pending"
  | "running"
  | "completed"
  | "cancelled"
  | "failed"

export type SliceStatus =
  | "scheduled"
  | "submitted"
  | "filled"
  | "rejected"
  | "skipped"

export interface AlgoChildSlice {
  index: number
  qty: number
  delay_seconds: number
  status: SliceStatus
  child_order_id: string | null
  filled_qty: number
  avg_fill_price: number | null
  error: string | null
  submitted_at: string | null
}

export interface AlgoOrder {
  algo_id: string
  algo_type: AlgoType
  symbol: string
  market: Market
  side: OrderSide
  total_qty: number
  order_type: OrderType
  limit_price: number | null
  strategy_id: string | null
  duration_seconds: number
  slice_count: number
  display_qty: number | null
  status: AlgoStatus
  filled_qty: number
  submitted_qty: number
  avg_fill_price: number | null
  progress_pct: number
  slices: AlgoChildSlice[]
  created_at: string
  started_at: string | null
  finished_at: string | null
  updated_at: string
}

export interface CreateAlgoOrderRequest {
  symbol: string
  market: Market
  side: OrderSide
  algo_type: AlgoType
  total_qty: number
  order_type: OrderType
  limit_price?: number | null
  strategy_id?: string | null
  duration_seconds: number
  slice_count: number
  display_qty?: number | null
}

// ── Hooks ────────────────────────────────────────────────────

const ALGO_KEY = ["order-algos"] as const

export function useAlgoOrders() {
  return useQuery<AlgoOrder[]>({
    queryKey: ALGO_KEY,
    queryFn: () => api.get<AlgoOrder[]>("/api/v1/orders/algo"),
    refetchInterval: 3000,
  })
}

export function useCreateAlgoOrder() {
  const qc = useQueryClient()
  return useMutation<AlgoOrder, Error, CreateAlgoOrderRequest>({
    mutationFn: (req) => api.post<AlgoOrder>("/api/v1/orders/algo", req),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ALGO_KEY })
      qc.invalidateQueries({ queryKey: ["orders"] })
    },
  })
}

export function useCancelAlgoOrder() {
  const qc = useQueryClient()
  return useMutation<AlgoOrder, Error, string>({
    mutationFn: (algoId) =>
      api.post<AlgoOrder>(`/api/v1/orders/algo/${algoId}/cancel`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ALGO_KEY }),
  })
}
