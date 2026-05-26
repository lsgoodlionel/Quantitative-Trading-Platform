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
