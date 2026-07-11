import { useMutation } from "@tanstack/react-query"
import { api } from "@/lib/api"

// ── 类型定义（本 hook 自包含）─────────────────────────────────────

export type TopkScoreMethod = "momentum" | "reversal" | "vol_scaled_momentum"
export type TopkSellMethod = "bottom" | "random"
export type TopkBuyMethod = "top" | "random"

export interface TopkRequest {
  symbols: string[]
  market: string
  frequency?: string
  start?: string | null
  end?: string | null

  score_method: TopkScoreMethod
  lookback: number

  rebalance_days: number
  topk: number
  n_drop: number
  hold_thresh: number
  risk_degree: number
  method_sell: TopkSellMethod
  method_buy: TopkBuyMethod
}

export interface TopkPeriod {
  date: string
  holdings: string[]
  weights: Record<string, number>
  buys: string[]
  sells: string[]
  turnover: number
  n_holdings: number
  period_return: number
  equity: number
}

export interface TopkMetrics {
  total_return: number
  annual_return: number
  annual_vol: number
  sharpe: number
  max_drawdown: number
  avg_turnover: number
  avg_holdings: number
  win_rate: number
}

export interface TopkResult {
  symbols: string[]
  market: string
  score_method: TopkScoreMethod
  lookback: number
  rebalance_days: number
  topk: number
  n_drop: number
  hold_thresh: number
  risk_degree: number
  method_sell: TopkSellMethod
  method_buy: TopkBuyMethod
  n_periods: number
  metrics: TopkMetrics
  equity_curve: { date: string; equity: number }[]
  periods: TopkPeriod[]
}

// ── Hook ──────────────────────────────────────────────────────────

export function useTopkPortfolio() {
  return useMutation<TopkResult, Error, TopkRequest>({
    mutationFn: (req) => api.post<TopkResult>("/api/v1/portfolio/topk", req),
  })
}
