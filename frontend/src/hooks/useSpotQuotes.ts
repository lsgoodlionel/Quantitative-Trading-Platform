import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"

export interface SpotQuote {
  symbol: string
  market: string
  name: string
  name_zh: string | null
  price: number | null
  prev_close: number | null
  change_pct: number | null
  change: number | null
  volume: number | null
  high: number | null
  low: number | null
  source: "realtime" | "delayed" | "daily" | "demo"
  updated_at: string | null
}

export interface SpotQuotesResponse {
  A: SpotQuote[]
  HK: SpotQuote[]
  US: SpotQuote[]
}

/** 三市实时/延迟行情快照，轮询间隔 8 秒 */
export function useSpotQuotes(enabled = true) {
  return useQuery<SpotQuotesResponse>({
    queryKey: ["spot-quotes"],
    queryFn: () => api.get<SpotQuotesResponse>("/api/v1/bars/spot"),
    enabled,
    refetchInterval: 8_000,
    staleTime: 6_000,
    retry: 1,
  })
}

/** 从 SpotQuotesResponse 中按 market+symbol 快速查找 */
export function findQuote(
  data: SpotQuotesResponse | undefined,
  symbol: string,
  market: string,
): SpotQuote | undefined {
  if (!data) return undefined
  const list = data[market as keyof SpotQuotesResponse] ?? []
  return list.find((q) => q.symbol === symbol)
}
