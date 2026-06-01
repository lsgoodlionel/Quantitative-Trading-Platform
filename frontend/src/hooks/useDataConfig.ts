import { useQuery } from "@tanstack/react-query"
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
