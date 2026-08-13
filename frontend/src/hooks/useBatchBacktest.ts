import { useMutation } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type { Market } from "@/types"

// ── 批量回测（V3 G3：选股器多选 → 一次跑一批）─────────────────

/** 后端 MAX_BATCH_SYMBOLS，超过则直接 400；前端提前拦下给更友好的提示 */
export const MAX_BATCH_SYMBOLS = 50

export interface BatchBacktestRequest {
  symbols: string[]
  strategy_name: string
  market: Market
  frequency: string
  start_date: string
  end_date: string
  initial_cash: number
  params: Record<string, unknown>
}

export interface BatchBacktestMetrics {
  total_return_pct: number
  annual_return_pct: number
  sharpe_ratio: number
  max_drawdown_pct: number
  win_rate_pct: number
  total_trades: number
}

export interface BatchBacktestItem {
  symbol: string
  metrics: BatchBacktestMetrics | null
  final_value: number | null
  /** 该标的失败原因；成功时为 null。单项失败不影响整批 */
  error: string | null
}

export interface BatchBacktestResult {
  batch_id: string
  strategy_name: string
  market: string
  total: number
  succeeded: number
  failed: number
  results: BatchBacktestItem[]
}

/** POST /api/v1/backtests/batch */
export function useBatchBacktest() {
  return useMutation<BatchBacktestResult, Error, BatchBacktestRequest>({
    mutationFn: (body) =>
      api.post<BatchBacktestResult>("/api/v1/backtests/batch", body),
  })
}
