import { useMutation, useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type { Market } from "@/types"

// ── 类型定义（A3 股票筛选器）────────────────────────────────────

export type ScreenerSortKey =
  | "change_pct"
  | "market_cap"
  | "pe"
  | "pb"
  | "dividend_yield"
  | "turnover"
  | "price"

export interface ScreenerFilter {
  market: Market
  min_price?: number | null
  max_price?: number | null
  min_market_cap_yi?: number | null
  max_market_cap_yi?: number | null
  min_pe?: number | null
  max_pe?: number | null
  min_pb?: number | null
  max_pb?: number | null
  min_dividend_yield?: number | null
  min_change_pct?: number | null
  max_change_pct?: number | null
  min_volume?: number | null
  sectors: string[]
  sort_by: ScreenerSortKey
  sort_dir: "asc" | "desc"
  limit: number
}

export interface ScreenerCandidate {
  symbol: string
  market: string
  name: string
  sector: string
  price: number | null
  change_pct: number | null
  pe: number | null
  pb: number | null
  market_cap: number | null
  market_cap_yi: number | null
  dividend_yield: number | null
  volume: number | null
  turnover: number | null
  turnover_rate: number | null
}

export interface ScreenerRunResult {
  market: string
  generated_at: string
  universe_size: number
  count: number
  candidates: ScreenerCandidate[]
}

export interface ScreenerPreset {
  id: string
  name: string
  desc: string
  criteria: Partial<ScreenerFilter>
}

export interface MoversResult {
  market: string
  generated_at: string
  gainers: ScreenerCandidate[]
  losers: ScreenerCandidate[]
}

// ── Hooks ──────────────────────────────────────────────────────

/** 运行筛选：条件 → 匹配标的列表 */
export function useScreenerRun() {
  return useMutation<ScreenerRunResult, Error, ScreenerFilter>({
    mutationFn: (filter) => api.post<ScreenerRunResult>("/api/v1/screener/run", filter),
  })
}

/** 预设筛选方案 */
export function useScreenerPresets() {
  return useQuery<ScreenerPreset[]>({
    queryKey: ["screener-presets"],
    queryFn: () => api.get<ScreenerPreset[]>("/api/v1/screener/presets"),
    staleTime: Infinity,
  })
}

/** 可选行业标签 */
export function useScreenerSectors() {
  return useQuery<string[]>({
    queryKey: ["screener-sectors"],
    queryFn: () => api.get<string[]>("/api/v1/screener/sectors"),
    staleTime: Infinity,
  })
}

/** 涨跌榜 */
export function useScreenerMovers(market: Market, top = 10) {
  return useQuery<MoversResult>({
    queryKey: ["screener-movers", market, top],
    queryFn: () => api.get<MoversResult>(`/api/v1/screener/movers?market=${market}&top=${top}`),
    staleTime: 1000 * 30,
  })
}
