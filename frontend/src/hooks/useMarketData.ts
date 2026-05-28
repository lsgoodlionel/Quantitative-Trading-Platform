import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type { Bar, Market, Frequency, MarketOverview } from "@/types"

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
      // backend expects `start` and `end` (not start_date / end_date)
      const qs = new URLSearchParams({
        symbol: params.symbol,
        market: params.market,
        frequency: params.frequency,
        start: params.start_date,
        end: params.end_date,
      })
      return api.get<BarsResponse>(`/api/v1/bars?${qs}`)
    },
    enabled: params !== null,
    staleTime: 1000 * 60,
  })
}

/** 获取单个标的最新一根 K 线，用于行情看板轮询刷新 */
export function useLatestBar(
  symbol: string,
  market: Market,
  frequency: Frequency = "1d",
  enabled = true,
) {
  return useQuery<Bar | null>({
    queryKey: ["bar-latest", symbol, market, frequency],
    queryFn: () => {
      const qs = new URLSearchParams({ symbol, market, frequency })
      return api.get<Bar | null>(`/api/v1/bars/latest?${qs}`)
    },
    enabled: enabled && !!symbol,
    refetchInterval: 30_000,   // 每 30 秒刷新一次价格
    staleTime: 25_000,
  })
}

/** 获取市场概览（A股/港股/美股列表带价格涨跌幅），1分钟刷新 */
export function useMarketOverview() {
  return useQuery<MarketOverview>({
    queryKey: ["market-overview"],
    queryFn: () => api.get<MarketOverview>("/api/v1/bars/market-overview"),
    refetchInterval: 60_000,
    staleTime: 50_000,
  })
}

/** 搜索股票代码/名称（使用后端 symbol_dict + CN 名称匹配） */
export function useSymbolSearch(query: string, market: string | null) {
  return useQuery({
    queryKey: ["symbol-search", query, market],
    queryFn: () => {
      const qs = new URLSearchParams({ q: query })
      if (market) qs.set("market", market)
      return api.get<{ symbol: string; market: string; name: string; name_zh: string | null }[]>(
        `/api/v1/bars/symbols/search?${qs}`,
      )
    },
    enabled: query.trim().length >= 1,
    staleTime: 1000 * 60 * 5, // 5 分钟
  })
}

/** 批量获取自选列表最新价格 */
export function useWatchlistLatest(
  items: { symbol: string; market: Market }[],
) {
  return useQuery<Record<string, Bar | null>>({
    queryKey: ["watchlist-latest", items],
    queryFn: async () => {
      const results: Record<string, Bar | null> = {}
      await Promise.all(
        items.map(async ({ symbol, market }) => {
          try {
            const qs = new URLSearchParams({ symbol, market, frequency: "1d" })
            const bar = await api.get<Bar | null>(`/api/v1/bars/latest?${qs}`)
            results[`${market}:${symbol}`] = bar
          } catch {
            results[`${market}:${symbol}`] = null
          }
        }),
      )
      return results
    },
    enabled: items.length > 0,
    refetchInterval: 30_000,
    staleTime: 25_000,
  })
}
