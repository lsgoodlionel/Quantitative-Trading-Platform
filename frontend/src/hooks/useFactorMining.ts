import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type { Market, Frequency } from "@/types"

// ── 遗传因子挖掘（POST /quant/factor/mine）────────────────────────

export interface MineRequest {
  symbols: string[]
  market: Market
  frequency: Frequency
  start?: string
  end?: string
  forward_period: number
  population_size: number
  generations: number
  tournament_size?: number
  crossover_rate?: number
  mutation_rate?: number
  elite_count?: number
  max_depth: number
  top_k: number
  seed: number
  fee_rate?: number
  entry_threshold?: number
  min_activity?: number
  record_best: boolean
}

export interface MinedCandidate {
  tokens: string[]
  expr: string
  fitness: number | null
  ic_mean: number | null
  rank_ic_mean: number | null
  icir: number | null
  mean_net_return: number | null
  turnover: number | null
}

export interface GenerationStat {
  generation: number
  best_fitness: number | null
  mean_fitness: number | null
  best_expr: string
}

export interface MineResult {
  best: MinedCandidate | null
  candidates: MinedCandidate[]
  history: GenerationStat[]
  n_evaluated: number
  n_unique: number
  symbols: string[]
  market: string
  forward_period: number
  recorded_id: string | null
}

export function useFactorMine() {
  const qc = useQueryClient()
  return useMutation<MineResult, Error, MineRequest>({
    mutationFn: (req) => api.post<MineResult>("/api/v1/quant/factor/mine", req),
    onSuccess: (res) => {
      if (res.recorded_id) qc.invalidateQueries({ queryKey: ["experiments"] })
    },
  })
}

// ── 实验记录 & 排行榜（/quant/experiments）───────────────────────

export type ExperimentKind =
  | "factor_analysis"
  | "formula_factor"
  | "genetic_mining"
  | "factor_library"

export interface ExperimentMetrics {
  ic_mean: number | null
  rank_ic_mean: number | null
  icir: number | null
  fitness: number | null
  mean_net_return: number | null
}

export interface ExperimentRecord {
  id: string
  kind: ExperimentKind
  name: string
  market: string
  symbols: string[]
  tokens: string[]
  params: Record<string, unknown>
  metrics: ExperimentMetrics
  note: string
  created_at: number
}

export interface ExperimentListResult {
  sort_by: "score" | "time"
  kind: string | null
  count: number
  records: ExperimentRecord[]
}

export interface ExperimentFilters {
  sortBy: "score" | "time"
  kind?: ExperimentKind
  limit?: number
}

export function useExperiments(filters: ExperimentFilters) {
  const { sortBy, kind, limit = 50 } = filters
  return useQuery<ExperimentListResult>({
    queryKey: ["experiments", sortBy, kind ?? "all", limit],
    queryFn: () => {
      const params = new URLSearchParams({ sort_by: sortBy, limit: String(limit) })
      if (kind) params.set("kind", kind)
      return api.get<ExperimentListResult>(`/api/v1/quant/experiments?${params.toString()}`)
    },
    staleTime: 30_000,
  })
}

export interface RecordExperimentRequest {
  kind: ExperimentKind
  name: string
  market: string
  symbols?: string[]
  tokens?: string[]
  params?: Record<string, unknown>
  metrics?: Partial<ExperimentMetrics>
  note?: string
}

export function useRecordExperiment() {
  const qc = useQueryClient()
  return useMutation<ExperimentRecord, Error, RecordExperimentRequest>({
    mutationFn: (req) => api.post<ExperimentRecord>("/api/v1/quant/experiments", req),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["experiments"] }),
  })
}

export function useDeleteExperiment() {
  const qc = useQueryClient()
  return useMutation<void, Error, string>({
    mutationFn: (id) => api.delete<void>(`/api/v1/quant/experiments/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["experiments"] }),
  })
}
