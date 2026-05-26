import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type { Position, Account, Market } from "@/types"

export function usePositions(market: Market = "US") {
  return useQuery<Position[]>({
    queryKey: ["positions", market],
    queryFn: () => api.get<Position[]>(`/api/v1/positions?market=${market}`),
    refetchInterval: 10_000,
  })
}

export function useAccount(market: Market = "US") {
  return useQuery<Account>({
    queryKey: ["account", market],
    queryFn: () => api.get<Account>(`/api/v1/positions/account?market=${market}`),
    refetchInterval: 10_000,
  })
}
