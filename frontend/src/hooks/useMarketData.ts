import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type { Bar, Market, Frequency } from "@/types"

interface BarsParams {
  symbol: string
  market: Market
  frequency: Frequency
  start_date: string
  end_date: string
}

interface BarsResponse {
  symbol: string
  market: string
  frequency: string
  bars: Bar[]
}

export function useBars(params: BarsParams | null) {
  return useQuery<BarsResponse>({
    queryKey: ["bars", params],
    queryFn: () => {
      if (!params) throw new Error("No params")
      const qs = new URLSearchParams({
        symbol: params.symbol,
        market: params.market,
        frequency: params.frequency,
        start_date: params.start_date,
        end_date: params.end_date,
      })
      return api.get<BarsResponse>(`/api/v1/market/bars?${qs}`)
    },
    enabled: params !== null,
    staleTime: 1000 * 60,
  })
}
