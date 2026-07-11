import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type { Market } from "@/types"

// ── 类型定义（E5 动态标的池规则链）─────────────────────────────

export type PairlistRuleKind =
  | "volume"
  | "price"
  | "market_cap"
  | "volatility"
  | "performance"
  | "spread"

export type SortDir = "asc" | "desc"

export interface PairlistRule {
  kind: PairlistRuleKind
  min_value?: number | null
  max_value?: number | null
  sort?: SortDir | null
  top?: number | null
}

export interface PairlistRunRequest {
  market: Market
  rules: PairlistRule[]
  lookback_days: number
}

export interface PairMetrics {
  symbol: string
  market: string
  name: string
  sector: string
  price: number | null
  change_pct: number | null
  volume: number | null
  turnover: number | null
  market_cap: number | null
  market_cap_yi: number | null
  volatility: number | null
  performance: number | null
  spread_proxy: number | null
}

export interface PairlistRunResult {
  market: string
  generated_at: string
  lookback_days: number
  universe_size: number
  count: number
  symbols: string[]
  items: PairMetrics[]
}

export interface SavedPairlist {
  id: string
  name: string
  market: Market
  rules: PairlistRule[]
  lookback_days: number
  created_at: string | null
  updated_at: string | null
}

export interface SavePairlistRequest {
  id?: string | null
  name: string
  market: Market
  rules: PairlistRule[]
  lookback_days: number
}

// ── 规则元数据（前端表单渲染用）────────────────────────────────

export const RULE_META: Record<
  PairlistRuleKind,
  { label: string; unit: string; step: number; help: string }
> = {
  volume: { label: "成交量", unit: "股", step: 1000, help: "当日成交量（来自快照）" },
  price: { label: "价格", unit: "本币", step: 1, help: "最新价" },
  market_cap: { label: "市值", unit: "亿", step: 10, help: "总市值（本币，单位亿）" },
  volatility: { label: "波动率", unit: "%", step: 1, help: "近 N 日日收益率年化波动率" },
  performance: { label: "近期表现", unit: "%", step: 1, help: "近 N 日累计收益率" },
  spread: { label: "价差代理", unit: "%", step: 0.1, help: "近 N 日 (高-低)/收 均值，越低越流动" },
}

export const RULE_KINDS: PairlistRuleKind[] = [
  "volume",
  "price",
  "market_cap",
  "volatility",
  "performance",
  "spread",
]

// ── Hooks ──────────────────────────────────────────────────────

/** 运行规则链：规则 → 可交易 universe */
export function usePairlistRun() {
  return useMutation<PairlistRunResult, Error, PairlistRunRequest>({
    mutationFn: (req) => api.post<PairlistRunResult>("/api/v1/screener/pairlist", req),
  })
}

/** 已保存标的池列表 */
export function useSavedPairlists() {
  return useQuery<SavedPairlist[]>({
    queryKey: ["pairlist-saved"],
    queryFn: () => api.get<SavedPairlist[]>("/api/v1/screener/pairlist/saved"),
    staleTime: 1000 * 30,
  })
}

/** 新建 / 更新标的池 */
export function useSavePairlist() {
  const qc = useQueryClient()
  return useMutation<SavedPairlist, Error, SavePairlistRequest>({
    mutationFn: (req) => api.put<SavedPairlist>("/api/v1/screener/pairlist/saved", req),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["pairlist-saved"] }),
  })
}

/** 删除标的池 */
export function useDeletePairlist() {
  const qc = useQueryClient()
  return useMutation<void, Error, string>({
    mutationFn: (id) => api.delete<void>(`/api/v1/screener/pairlist/saved/${encodeURIComponent(id)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["pairlist-saved"] }),
  })
}
