import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type { Market, Frequency } from "@/types"

// 因子策略适配器（V3 Wave A-a / G1）——「看到一个因子 → 立刻知道它能不能赚钱」
//
//   POST   /api/v1/factors/strategy/backtest
//   POST   /api/v1/factors/strategy/promote
//   GET    /api/v1/factors/strategy
//   GET    /api/v1/factors/strategy/methods
//   DELETE /api/v1/factors/strategy/{name}

const BASE = "/api/v1/factors/strategy"

// ── 类型 ──────────────────────────────────────────────────────────

/** 与后端 `FactorStrategySpec` 一一对应的纯数据描述 */
export interface FactorStrategySpec {
  formula: string
  universe: string[]
  /** 做多分数最高的**比例**（0.2 = 前 20%） */
  long_quantile: number
  /** 做空分数最低的比例；null = 纯多头 */
  short_quantile: number | null
  rebalance_days: number
  portfolio_method: string
  max_positions: number | null
}

export interface FactorStrategyRecord {
  name: string
  spec: FactorStrategySpec
  note: string
  source_experiment_id: string | null
  created_at: number
  updated_at: number
}

export interface EquityPoint {
  date: string
  value: number
}

export interface BacktestMetrics {
  total_return_pct: number
  annual_return_pct: number
  volatility_pct: number
  sharpe_ratio: number
  sortino_ratio: number
  calmar_ratio: number
  max_drawdown_pct: number
  total_trades: number
  win_rate_pct: number
  profit_factor: number
  buy_hold_return_pct: number
}

export interface FactorBacktestResult {
  spec: FactorStrategySpec
  symbols: string[]
  strategy_name: string
  start_date: string
  end_date: string
  initial_cash: number
  final_value: number
  metrics: BacktestMetrics
  equity_curve: EquityPoint[]
  n_fills: number
}

export interface FactorBacktestRequest {
  spec: FactorStrategySpec
  market: Market
  frequency: Frequency
  start?: string
  end?: string
  initial_cash: number
  allow_short: boolean
}

export interface PromoteRequest {
  name: string
  spec: FactorStrategySpec
  note?: string
  experiment_id?: string
}

// ── 默认值与校验（纯函数，便于单测）───────────────────────────────

export const DEFAULT_SPEC: FactorStrategySpec = {
  formula: "",
  universe: [],
  long_quantile: 0.2,
  short_quantile: null,
  rebalance_days: 5,
  portfolio_method: "equal_weight",
  max_positions: null,
}

/** 组合权重方法的中文标签（后端 /methods 只给 key） */
export const METHOD_LABELS: Record<string, string> = {
  equal_weight: "等权",
  insight_weight: "因子分数加权",
  max_sharpe: "最大夏普",
  min_volatility: "最小波动",
  risk_parity: "风险平价",
  min_cvar: "最小 CVaR",
  hrp: "层次风险平价 (HRP)",
  black_litterman: "Black-Litterman",
  min_cdar: "最小 CDaR",
}

/**
 * 提交前的本地校验，规则与后端 `FactorStrategySpec` 保持一致。
 * 返回 null 表示通过 —— 在前端先拦一道，用户不必等一趟网络才知道填错了。
 */
export function validateSpec(spec: FactorStrategySpec): string | null {
  if (!spec.formula.trim()) return "请先填写 RPN 公式"
  if (spec.universe.length < 2) return "标的池至少需要 2 个标的"
  if (!(spec.long_quantile > 0 && spec.long_quantile <= 1)) return "多头比例需落在 (0, 1]"
  if (spec.short_quantile != null) {
    if (spec.short_quantile < 0 || spec.short_quantile >= 1) return "空头比例需落在 [0, 1)"
    if (spec.short_quantile + spec.long_quantile >= 1) return "多空分位重叠：两者之和需 < 1"
  }
  if (spec.rebalance_days < 1) return "再平衡天数至少为 1"
  if (spec.max_positions != null && spec.max_positions < 1) return "持仓上限至少为 1"
  return null
}

/** 由因子公式 token 与标的池组装一条 spec */
export function specFromTokens(
  tokens: string[],
  universe: string[],
  overrides: Partial<FactorStrategySpec> = {},
): FactorStrategySpec {
  return { ...DEFAULT_SPEC, formula: tokens.join(" "), universe, ...overrides }
}

// ── Hooks ─────────────────────────────────────────────────────────

export function useFactorStrategyBacktest() {
  return useMutation<FactorBacktestResult, Error, FactorBacktestRequest>({
    mutationFn: (req) => api.post<FactorBacktestResult>(`${BASE}/backtest`, req),
  })
}

export function useFactorStrategies() {
  return useQuery<{ count: number; strategies: FactorStrategyRecord[] }>({
    queryKey: ["factor-strategies"],
    queryFn: () => api.get(BASE),
    staleTime: 30_000,
  })
}

export function usePortfolioMethods() {
  return useQuery<{ methods: string[] }>({
    queryKey: ["factor-strategy-methods"],
    queryFn: () => api.get(`${BASE}/methods`),
    staleTime: Infinity,      // 后端常量，不会在会话中途变
  })
}

export function usePromoteFactorStrategy() {
  const qc = useQueryClient()
  return useMutation<FactorStrategyRecord, Error, PromoteRequest>({
    mutationFn: (req) => api.post<FactorStrategyRecord>(`${BASE}/promote`, req),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["factor-strategies"] })
      qc.invalidateQueries({ queryKey: ["experiments"] })
    },
  })
}

export function useDeleteFactorStrategy() {
  const qc = useQueryClient()
  return useMutation<void, Error, string>({
    mutationFn: (name) => api.delete<void>(`${BASE}/${encodeURIComponent(name)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["factor-strategies"] }),
  })
}
