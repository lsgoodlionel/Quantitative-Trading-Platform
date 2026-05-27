import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type { LiveOrder, OrderSide, OrderType, Market } from "@/types"

interface CreateOrderRequest {
  symbol: string
  market: Market
  side: OrderSide
  qty: number
  order_type: OrderType
  limit_price?: number | null
  strategy_id?: string | null
}

export function useOrders() {
  return useQuery<LiveOrder[]>({
    queryKey: ["orders"],
    queryFn: () => api.get<LiveOrder[]>("/api/v1/orders"),
    refetchInterval: 5000,
  })
}

export function useCreateOrder() {
  const qc = useQueryClient()
  return useMutation<LiveOrder, Error, CreateOrderRequest>({
    mutationFn: (req) => api.post<LiveOrder>("/api/v1/orders", req),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["orders"] }),
  })
}

export function useCancelOrder() {
  const qc = useQueryClient()
  return useMutation<void, Error, string>({
    mutationFn: (orderId) => api.post(`/api/v1/orders/${orderId}/cancel`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["orders"] }),
  })
}

export interface AttributionItem {
  symbol:       string
  market:       string
  buy_qty:      number
  sell_qty:     number
  net_qty:      number
  buy_value:    number
  sell_value:   number
  avg_buy_cost: number
  commission:   number
  realized_pnl: number
  trade_count:  number
}

export interface AttributionResult {
  positions: AttributionItem[]
  totals: {
    realized_pnl:  number
    commission:    number
    trade_count:   number
    symbol_count:  number
  }
}

export function useAttribution(market?: Market) {
  const qs = market ? `?market=${market}` : ""
  return useQuery<AttributionResult>({
    queryKey: ["attribution", market],
    queryFn: () => api.get<AttributionResult>(`/api/v1/orders/attribution${qs}`),
    refetchInterval: 15_000,
  })
}
