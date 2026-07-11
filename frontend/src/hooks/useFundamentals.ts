import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import type { Market } from "@/types"

// ── 基本面数据类型（对齐后端 app/data/providers/models.py）────────

export interface IncomeStatement {
  period_ending: string | null
  fiscal_year: number | null
  fiscal_period: string | null
  currency: string | null
  revenue: number | null
  cost_of_revenue: number | null
  gross_profit: number | null
  operating_expense: number | null
  operating_income: number | null
  ebitda: number | null
  ebit: number | null
  interest_expense: number | null
  pretax_income: number | null
  income_tax_expense: number | null
  net_income: number | null
  basic_eps: number | null
  diluted_eps: number | null
  basic_shares: number | null
  diluted_shares: number | null
}

export interface BalanceSheet {
  period_ending: string | null
  fiscal_year: number | null
  fiscal_period: string | null
  currency: string | null
  total_assets: number | null
  current_assets: number | null
  cash_and_equivalents: number | null
  inventory: number | null
  total_liabilities: number | null
  current_liabilities: number | null
  total_debt: number | null
  long_term_debt: number | null
  total_equity: number | null
  retained_earnings: number | null
  shares_outstanding: number | null
}

export interface CashFlow {
  period_ending: string | null
  fiscal_year: number | null
  fiscal_period: string | null
  currency: string | null
  operating_cash_flow: number | null
  capital_expenditure: number | null
  free_cash_flow: number | null
  investing_cash_flow: number | null
  financing_cash_flow: number | null
  dividends_paid: number | null
  net_change_in_cash: number | null
}

export interface FinancialRatios {
  period_ending: string | null
  fiscal_year: number | null
  fiscal_period: string | null
  current_ratio: number | null
  quick_ratio: number | null
  debt_to_equity: number | null
  gross_margin: number | null
  operating_margin: number | null
  net_margin: number | null
  return_on_assets: number | null
  return_on_equity: number | null
  asset_turnover: number | null
}

export interface KeyMetrics {
  symbol: string | null
  currency: string | null
  price: number | null
  market_cap: number | null
  enterprise_value: number | null
  pe_ratio: number | null
  forward_pe: number | null
  peg_ratio: number | null
  pb_ratio: number | null
  ps_ratio: number | null
  eps: number | null
  forward_eps: number | null
  book_value: number | null
  dividend_yield: number | null
  dividend_rate: number | null
  beta: number | null
  shares_outstanding: number | null
  fifty_two_week_high: number | null
  fifty_two_week_low: number | null
  name?: string | null
}

export interface FundamentalsBundle {
  symbol: string
  market: string
  currency: string | null
  name: string | null
  income: IncomeStatement[]
  balance: BalanceSheet[]
  cashflow: CashFlow[]
  ratios: FinancialRatios[]
  metrics: KeyMetrics | null
  warnings: string[]
}

export type FundamentalsSection = "income" | "balance" | "cashflow" | "ratios" | "metrics"

export interface FundamentalsParams {
  symbol: string
  market: Market
  limit?: number
  sections?: FundamentalsSection[]
}

/**
 * 拉取单标的基本面数据（分节）。
 *
 * enabled=!!symbol，避免空标的触发请求；基本面数据变动慢，staleTime 设 1 小时。
 */
export function useFundamentals({ symbol, market, limit = 5, sections }: FundamentalsParams) {
  const query = new URLSearchParams({ market, limit: String(limit) })
  if (sections?.length) query.set("sections", sections.join(","))

  return useQuery<FundamentalsBundle>({
    queryKey: ["fundamentals", market, symbol, limit, sections?.join(",") ?? "all"],
    queryFn: () =>
      api.get<FundamentalsBundle>(
        `/api/v1/fundamentals/${encodeURIComponent(symbol)}?${query.toString()}`,
      ),
    enabled: !!symbol,
    staleTime: 60 * 60 * 1000,
  })
}
