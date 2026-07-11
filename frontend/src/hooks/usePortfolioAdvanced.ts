import { useMutation } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type { Market } from "@/types"

// ── D3/D4/D5 高级组合优化 ───────────────────────────────────────
// 复用 /api/v1/portfolio/optimize，扩展 method=hrp/black_litterman/min_cdar
// 与 BL 观点字段。类型独立于 types/index.ts，避免与既有 PortfolioOpt 冲突。

/** 扩展后的优化方法（含 D3/D4/D5）*/
export type AdvancedOptMethod =
  | "max_sharpe" | "min_volatility" | "risk_parity" | "min_cvar"
  | "equal_weight" | "hrp" | "black_litterman" | "min_cdar"

export type AdvancedRiskModel =
  | "sample_cov" | "ledoit_wolf" | "exp_cov" | "semicovariance"

export type AdvancedReturnsMethod =
  | "mean_historical" | "ema_historical" | "capm"

export type HrpLinkage = "single" | "complete" | "average" | "ward"

/** Black-Litterman 单条观点 */
export interface BLViewInput {
  kind: "absolute" | "relative"
  /** absolute: [sym]；relative: [long, short] */
  assets: string[]
  /** 年化收益 / 超额收益，小数形式（0.12 = 12%）*/
  value: number
  /** Idzorek 置信度 0~1 */
  confidence: number
}

export interface AdvancedOptRequest {
  symbols: string[]
  market: Market
  start_date: string
  end_date: string
  method: AdvancedOptMethod
  include_frontier: boolean
  risk_model?: AdvancedRiskModel
  expected_returns_method?: AdvancedReturnsMethod
  // BL
  views?: BLViewInput[]
  market_caps?: Record<string, number> | null
  bl_risk_aversion?: number | null
  bl_tau?: number
  // HRP
  linkage_method?: HrpLinkage
  // CVaR / CDaR
  cvar_beta?: number
}

export interface AdvancedFrontierPoint {
  vol: number
  ret: number
  sharpe: number
}

export interface AdvancedOptResult {
  method: string
  weights: Record<string, number>
  expected_return: number
  expected_volatility: number
  sharpe_ratio: number
  cvar_95: number
  frontier: AdvancedFrontierPoint[]
  risk_contributions: Record<string, number>
  risk_model?: string
  expected_returns_method?: string
  // ── D3/D4/D5 回显 ──
  bl_prior_returns?: Record<string, number>
  bl_posterior_returns?: Record<string, number>
  bl_risk_aversion?: number | null
  bl_views?: string[]
  linkage_method?: string | null
  cvar_beta?: number | null
}

/** 高级组合优化 mutation（HRP / Black-Litterman / CVaR / CDaR）*/
export function useAdvancedPortfolioOptimize() {
  return useMutation<AdvancedOptResult, Error, AdvancedOptRequest>({
    mutationFn: (req) =>
      api.post<AdvancedOptResult>("/api/v1/portfolio/optimize", req),
  })
}
