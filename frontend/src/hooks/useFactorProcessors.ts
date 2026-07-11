import { useMutation, useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type { Market, Frequency } from "@/types"

// ── 处理器元数据 ──────────────────────────────────────────────────
export type ProcessorKind = "infer" | "learn"
export type ProcessorParamType = "int" | "float" | "bool" | "str" | "list[str]"

export interface ProcessorParamMeta {
  name: string
  type: ProcessorParamType
  default: unknown | null
  description: string
}

export interface ProcessorMeta {
  name: string
  label: string
  kind: ProcessorKind
  is_for_infer: boolean
  params: ProcessorParamMeta[]
}

export interface ProcessorConfig {
  name: string
  params: Record<string, unknown>
}

// ── 预览（B1）─────────────────────────────────────────────────────
export interface ProcessorPreviewRequest {
  symbols: string[]
  market: Market
  frequency: Frequency
  start?: string
  end?: string
  fit_end: string
  base_factor: string
  tokens?: string[]
  infer_processors: ProcessorConfig[]
  learn_processors: ProcessorConfig[]
  forward_period: number
}

export interface FactorStats {
  count: number
  mean: number
  std: number
  min: number
  p25: number
  median: number
  p75: number
  max: number
  nan_rate: number
}

export interface PanelCell {
  time: string
  instrument: string
  value: number | null
}

export interface ProcessorPreviewResult {
  symbols: string[]
  market: string
  fit_end: string
  n_rows_in: number
  n_rows_out: number
  dropped_rows: number
  fitted_learn: string[]
  columns: string[]
  raw_stats: FactorStats
  processed_stats: FactorStats
  sample_before: PanelCell[]
  sample_after: PanelCell[]
}

// ── 适应度（B4）───────────────────────────────────────────────────
export interface FitnessConfig {
  fee_rate: number
  max_impact: number
  trade_notional: number
  entry_threshold: number
  drawdown_bar: number
  drawdown_penalty: number
  min_activity: number
}

export interface FactorFitnessRequest {
  symbols: string[]
  market: Market
  frequency: Frequency
  start?: string
  end?: string
  base_factor: string
  tokens?: string[]
  forward_period: number
  fee_rate?: number
  max_impact?: number
  trade_notional?: number
  entry_threshold?: number
  drawdown_bar?: number
  drawdown_penalty?: number
  min_activity?: number
}

export interface FactorFitnessResult {
  symbols: string[]
  market: string
  base_factor: string
  tokens: string[] | null
  forward_period: number
  fitness: number
  mean_net_return: number
  gross_return: number
  total_cost: number
  turnover: number
  avg_activity: number
  n_big_drawdowns: number
  activity_gate_passed: boolean
  per_instrument_score: Record<string, number>
  config_used: FitnessConfig
}

// ── Hooks ─────────────────────────────────────────────────────────
export function useProcessorMeta() {
  return useQuery<ProcessorMeta[]>({
    queryKey: ["processor-meta"],
    queryFn: () => api.get<ProcessorMeta[]>("/api/v1/quant/processors/meta"),
    staleTime: Infinity,
  })
}

export function useProcessorPreview() {
  return useMutation<ProcessorPreviewResult, Error, ProcessorPreviewRequest>({
    mutationFn: (req) =>
      api.post<ProcessorPreviewResult>("/api/v1/quant/processors/preview", req),
  })
}

export function useFactorFitness() {
  return useMutation<FactorFitnessResult, Error, FactorFitnessRequest>({
    mutationFn: (req) =>
      api.post<FactorFitnessResult>("/api/v1/quant/factor/fitness", req),
  })
}
