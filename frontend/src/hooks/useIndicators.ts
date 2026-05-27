import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type { Market, Frequency } from "@/types"

export type IndicatorKey =
  | "sma" | "ema" | "rsi" | "macd" | "bb" | "atr" | "adx"
  | "stoch" | "cci" | "obv" | "vwap" | "williams_r" | "roc" | "mfi"
  | "donchian" | "keltner"

export interface IndicatorData {
  time: string[]
  [key: string]: (number | null)[] | string[]
}

interface IndicatorParams {
  symbol: string
  market: Market
  frequency: Frequency
  start?: string
  end?: string
  indicators: IndicatorKey[]
}

export function useIndicators(params: IndicatorParams | null) {
  return useQuery<IndicatorData>({
    queryKey: ["indicators", params],
    queryFn: () => {
      if (!params) throw new Error("No params")
      const qs = new URLSearchParams({
        symbol: params.symbol,
        market: params.market,
        frequency: params.frequency,
        indicators: params.indicators.join(","),
      })
      if (params.start) qs.set("start", params.start)
      if (params.end) qs.set("end", params.end)
      return api.get<IndicatorData>(`/api/v1/bars/indicators?${qs}`)
    },
    enabled: params !== null && params.indicators.length > 0,
    staleTime: 1000 * 60 * 5,
  })
}
