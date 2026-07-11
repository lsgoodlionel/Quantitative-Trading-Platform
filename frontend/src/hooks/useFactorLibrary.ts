import { useMutation, useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type { Market, Frequency } from "@/types"

// ── 因子库目录（GET /quant/factor/library）───────────────────────

export interface LibraryFactorMeta {
  name: string
  label: string
  group: string
  window: number
  expr: string
}

export interface LibraryGroupMeta {
  name: string
  label: string
  description: string
  count: number
}

export interface FactorLibraryCatalog {
  n_factors: number
  windows: number[]
  groups: LibraryGroupMeta[]
  factors: LibraryFactorMeta[]
}

/** 因子库目录（静态元数据，缓存不失效） */
export function useFactorLibraryCatalog() {
  return useQuery<FactorLibraryCatalog>({
    queryKey: ["factor-library-catalog"],
    queryFn: () => api.get<FactorLibraryCatalog>("/api/v1/quant/factor/library"),
    staleTime: Infinity,
  })
}

// ── 横截面 IC 排行（POST /quant/factor/library/analyze）──────────

export type LibraryMethod = "rank_ic" | "ic"

export interface FactorLibraryAnalyzeRequest {
  symbols: string[]
  market: Market
  frequency: Frequency
  start?: string
  end?: string
  forward_period: number
  groups?: string[]
  windows?: number[]
  method: LibraryMethod
  top_k: number
}

export interface FactorICRow {
  name: string
  label: string
  group: string
  window: number
  expr: string
  ic_mean: number | null
  ic_std: number | null
  icir: number | null
  rank_ic_mean: number | null
  positive_rate: number | null
  coverage: number | null
  n_dates: number
}

export interface FactorLibraryAnalyzeResult {
  symbols: string[]
  market: string
  forward_period: number
  method: LibraryMethod
  n_factors: number
  n_symbols: number
  n_dates: number
  ranking: FactorICRow[]
  best: FactorICRow | null
}

export function useFactorLibraryAnalyze() {
  return useMutation<FactorLibraryAnalyzeResult, Error, FactorLibraryAnalyzeRequest>({
    mutationFn: (req) =>
      api.post<FactorLibraryAnalyzeResult>("/api/v1/quant/factor/library/analyze", req),
  })
}
