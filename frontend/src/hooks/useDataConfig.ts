import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"

export interface FeedStatus {
  name: string
  kind: "primary" | "fallback" | "demo"
  installed: boolean
  version: string | null
  ok: boolean
  error: string | null
  note: string | null
}

export interface MarketDataStatus {
  market: string
  label: string
  feeds: FeedStatus[]
  realtime: boolean
}

export interface DataConfigStatus {
  a_share: MarketDataStatus
  hk: MarketDataStatus
}

/** 查询 A股 / 港股数据通道实时状态（并行探测，约 8–10 秒超时） */
export function useDataConfigStatus(enabled = true) {
  return useQuery<DataConfigStatus>({
    queryKey: ["data-config-status"],
    queryFn: () => api.get<DataConfigStatus>("/api/v1/data-config/status"),
    enabled,
    staleTime: 1000 * 60 * 2,   // 2 分钟内不重复探测
    retry: 2,                   // 允许重试 2 次，应对 Docker 冷启动竞态
    retryDelay: 4000,           // 每次重试等待 4 秒（探测本身已有 8-10 秒超时）
  })
}

// ── 多源数据通道（Multi-Source Data Channel）─────────────────────

export type MarketKey = "US" | "HK" | "A"

export interface SourceStatus {
  id: string
  name: string
  requires: string | null
  realtime: boolean
  note: string
  enabled: boolean
  pinned: boolean
  ok: boolean
  latency_ms: number | null
  error: string | null
}

export interface MarketSources {
  market: string
  label: string
  sources: SourceStatus[]
  active_source: string | null
  has_realtime: boolean
}

export interface SourcesStatus {
  markets: Record<MarketKey, MarketSources>
}

export interface SourceMeta {
  id: string
  name: string
  requires: string | null
  realtime: boolean
  note: string
}

export interface MarketConfig {
  order: string[]
  disabled: string[]
  pinned: string | null
}

export interface SourcesConfig {
  catalog: Record<MarketKey, SourceMeta[]>
  config: Record<MarketKey, MarketConfig>
}

export interface ConfigUpdateRequest {
  market: MarketKey
  order: string[]
  disabled: string[]
  pinned: string | null
}

/** 实时探活全部市场全部源（真实拉取，含延迟）。支持自动刷新。 */
export function useSourcesStatus(refetchMs: number | false = false) {
  return useQuery<SourcesStatus>({
    queryKey: ["data-sources-status"],
    queryFn: () => api.get<SourcesStatus>("/api/v1/data-sources/status"),
    staleTime: 1000 * 10,
    retry: 1,
    retryDelay: 3000,
    refetchInterval: refetchMs,
  })
}

/** 源目录 + 当前多源配置。 */
export function useSourcesConfig() {
  return useQuery<SourcesConfig>({
    queryKey: ["data-sources-config"],
    queryFn: () => api.get<SourcesConfig>("/api/v1/data-sources/config"),
    staleTime: Infinity,
  })
}

/** 更新某市场多源配置（顺序/禁用/强制），成功后刷新状态与配置。 */
export function useUpdateSourcesConfig() {
  const qc = useQueryClient()
  return useMutation<MarketConfig, Error, ConfigUpdateRequest>({
    mutationFn: (req) => api.put<MarketConfig>("/api/v1/data-sources/config", req),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["data-sources-config"] })
      qc.invalidateQueries({ queryKey: ["data-sources-status"] })
    },
  })
}
