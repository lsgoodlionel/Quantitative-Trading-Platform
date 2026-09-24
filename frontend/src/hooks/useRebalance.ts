import { useMutation } from "@tanstack/react-query"
import { api } from "@/lib/api"

/** 单条调仓腿（后端 RebalanceLeg 的镜像，字段名保持一致，直接透传回 execute） */
export interface RebalanceLeg {
  symbol: string
  current_qty: number
  target_qty: number
  delta_qty: number
  price: number
  delta_value: number
  reason: "open" | "close" | "increase" | "decrease"
  side: "BUY" | "SELL"
}

export interface RebalancePreviewRequest {
  target_weights: Record<string, number>
  market: string
  min_trade_value?: number
  lot_size?: number
  allow_short?: boolean
}

export interface RebalancePreviewResult {
  market: string
  portfolio_value: number
  legs: RebalanceLeg[]
  total_buy_value: number
  total_sell_value: number
  estimated_commission: number
  warnings: string[]
  /** 执行时必须原样带回；服务端据此拒绝陈旧预览 */
  confirm_token: string
  expires_in_seconds: number
}

export interface RebalanceExecuteRequest {
  market: string
  legs: RebalanceLeg[]
  confirm_token: string
  strategy_id?: string | null
}

export interface SubmittedOrder {
  symbol: string
  order_id: string
  side: string
  qty: number
  status: string
}

export interface RejectedLeg {
  symbol: string
  side: string
  qty: number
  reason: string
}

export interface RebalanceExecuteResult {
  submitted: SubmittedOrder[]
  rejected: RejectedLeg[]
}

const PREVIEW_URL = "/api/v1/portfolio/rebalance/preview"
const EXECUTE_URL = "/api/v1/portfolio/rebalance/execute"

export function useRebalancePreview() {
  return useMutation<RebalancePreviewResult, Error, RebalancePreviewRequest>({
    mutationFn: (req) => api.post<RebalancePreviewResult>(PREVIEW_URL, req),
  })
}

export function useRebalanceExecute() {
  return useMutation<RebalanceExecuteResult, Error, RebalanceExecuteRequest>({
    mutationFn: (req) => api.post<RebalanceExecuteResult>(EXECUTE_URL, req),
  })
}

/** 整手默认值：港股/A 股 100 股一手，美股 1 股 */
export function defaultLotSize(market: string): number {
  return market === "US" ? 1 : 100
}
