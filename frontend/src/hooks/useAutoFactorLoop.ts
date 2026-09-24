import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"

// ── 自动因子研发循环（V3 · I2）────────────────────────────────────
//
// 一轮 = 遗传搜索 + 样本外验证 + 入库 + LLM 复盘，以分钟计，走 Celery 异步。
// 提交拿 round_id，然后轮询；跑完（done/error）就停轮询。
//
// ⚠️ 这里没有「一键上线」。循环只把因子写进产物库，晋级为策略是人工路径。

export type LoopStatus = "queued" | "running" | "done" | "error"

export interface AutoLoopRequest {
  universe: string[]
  /** 样本内截止日。其后的数据在整个搜索过程中不可见 */
  is_end: string
  market?: "US" | "HK" | "A"
  frequency?: string
  start?: string
  end?: string
  forward_period?: number
  generations?: number
  population?: number
  top_k?: number
  max_candidates_evaluated?: number
  max_depth?: number
  seed?: number
  use_cross_section?: boolean
  seeds?: string[]
}

/** 一条存活因子。样本内 / 样本外两套指标并排，前端也不要把它们合并成一个数。 */
export interface LoopSurvivor {
  expr: string
  tokens: string[]
  is_fitness: number | null
  is_ic_mean: number | null
  is_rank_ic_mean: number | null
  is_icir: number | null
  is_n_dates: number
  oos_fitness?: number | null
  oos_ic_mean?: number | null
  oos_rank_ic_mean?: number | null
  oos_icir?: number | null
  oos_n_dates?: number
  ic_decay_ratio: number | null
  overfit_suspect: boolean
  out_of_sample_evaluated: boolean
}

export interface LoopResult {
  round_id: string
  seeds: string[]
  survivors: LoopSurvivor[]
  /** 本轮实际评估过的不同表达式数 —— 多重检验的分母，必须与指标同屏展示 */
  hypotheses_tested: number
  /** 后端给的措辞，前端如实照抄，不要自己编一套 */
  multiple_testing_note: string
  llm_review: string | null
  llm_error: string | null
  next_seeds: string[]
  rejected_seed_count: number
  invalid_seed_count: number
  truncated: boolean
  out_of_sample_available: boolean
  is_end: string
  embargo_bars: number
  universe: string[]
  artifact_id: string | null
  artifact_error: string | null
}

export interface LoopRound {
  round_id: string
  status: LoopStatus
  created_at: number
  updated_at: number
  request: Record<string, unknown>
  result: LoopResult | null
  error: string | null
}

export interface LoopRoundList {
  total: number
  items: LoopRound[]
}

/** 轮询间隔。一轮以分钟计，3s 足够及时又不至于把后端刷满。 */
const POLL_INTERVAL_MS = 3000

const LIST_KEY = ["auto-loop-rounds"]

export function useAutoLoopRounds(limit = 20) {
  return useQuery<LoopRoundList, Error>({
    queryKey: [...LIST_KEY, limit],
    queryFn: () => api.get<LoopRoundList>(`/api/v1/lab/auto-loop?limit=${limit}`),
    // 列表里只要还有没跑完的轮次就继续刷新，跑完就停 —— 不做无意义的常驻轮询
    refetchInterval: (query) =>
      hasPendingRound(query.state.data) ? POLL_INTERVAL_MS : false,
  })
}

export function useAutoLoopRound(roundId: string | null) {
  return useQuery<LoopRound, Error>({
    queryKey: ["auto-loop-round", roundId],
    queryFn: () => api.get<LoopRound>(`/api/v1/lab/auto-loop/${roundId}`),
    enabled: roundId !== null,
    refetchInterval: (query) =>
      isPending(query.state.data?.status) ? POLL_INTERVAL_MS : false,
  })
}

export function useStartAutoLoop() {
  const qc = useQueryClient()
  return useMutation<{ round_id: string; status: LoopStatus }, Error, AutoLoopRequest>({
    mutationFn: (req) => api.post("/api/v1/lab/auto-loop", req),
    onSuccess: () => void qc.invalidateQueries({ queryKey: LIST_KEY }),
  })
}

export function useDeleteAutoLoopRound() {
  const qc = useQueryClient()
  return useMutation<unknown, Error, string>({
    mutationFn: (roundId) => api.delete(`/api/v1/lab/auto-loop/${roundId}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: LIST_KEY }),
  })
}

// ── 展示辅助 ──────────────────────────────────────────────────────

export function isPending(status: LoopStatus | undefined): boolean {
  return status === "queued" || status === "running"
}

export function hasPendingRound(list: LoopRoundList | undefined): boolean {
  return (list?.items ?? []).some((r) => isPending(r.status))
}

export const STATUS_LABELS: Record<LoopStatus, string> = {
  queued: "排队中",
  running: "运行中",
  done: "已完成",
  error: "失败",
}

/** 指标格式化。null 显示为 n/a —— 不要用 0 冒充「没算出来」。 */
export function formatMetric(value: number | null | undefined, digits = 4): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "n/a"
  return value.toFixed(digits)
}

/** 样本外 ÷ 样本内。用百分比表达衰减幅度，比一串小数直观。 */
export function formatDecay(ratio: number | null | undefined): string {
  if (ratio === null || ratio === undefined || Number.isNaN(ratio)) return "n/a"
  return `${(ratio * 100).toFixed(0)}%`
}
