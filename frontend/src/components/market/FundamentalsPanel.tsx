import { useState, useMemo } from "react"
import type { Market } from "@/types"
import {
  useFundamentals,
  type FundamentalsBundle,
  type IncomeStatement,
  type BalanceSheet,
  type CashFlow,
  type FinancialRatios,
  type KeyMetrics,
} from "@/hooks/useFundamentals"

// ── 数值格式化 ────────────────────────────────────────────────

/** 大额金额 → 亿/万 单位（中文习惯）。 */
function fmtAmount(v: number | null, currency: string | null): string {
  if (v == null) return "—"
  const sym = currency === "USD" ? "$" : currency === "HKD" ? "HK$" : currency === "CNY" ? "¥" : ""
  const abs = Math.abs(v)
  const sign = v < 0 ? "-" : ""
  if (abs >= 1e8) return `${sign}${sym}${(abs / 1e8).toFixed(2)}亿`
  if (abs >= 1e4) return `${sign}${sym}${(abs / 1e4).toFixed(2)}万`
  return `${sign}${sym}${abs.toFixed(2)}`
}

function fmtPct(v: number | null): string {
  return v == null ? "—" : `${(v * 100).toFixed(2)}%`
}

function fmtNum(v: number | null, digits = 2): string {
  return v == null ? "—" : v.toFixed(digits)
}

function periodLabel(p: { period_ending: string | null; fiscal_year: number | null }): string {
  if (p.period_ending) return p.period_ending.slice(0, 10)
  return p.fiscal_year ? String(p.fiscal_year) : "—"
}

// ── 关键指标卡片 ──────────────────────────────────────────────

interface MetricTile {
  label: string
  value: string
}

function buildTiles(m: KeyMetrics | null): MetricTile[] {
  if (!m) return []
  return [
    { label: "市值", value: fmtAmount(m.market_cap, m.currency) },
    { label: "最新价", value: m.price != null ? fmtNum(m.price) : "—" },
    { label: "市盈率 TTM", value: fmtNum(m.pe_ratio) },
    { label: "预期市盈率", value: fmtNum(m.forward_pe) },
    { label: "市净率", value: fmtNum(m.pb_ratio) },
    { label: "市销率", value: fmtNum(m.ps_ratio) },
    { label: "EPS", value: fmtNum(m.eps) },
    { label: "股息率", value: fmtPct(m.dividend_yield) },
    { label: "贝塔", value: fmtNum(m.beta) },
    { label: "52周高", value: fmtNum(m.fifty_two_week_high) },
    { label: "52周低", value: fmtNum(m.fifty_two_week_low) },
    { label: "PEG", value: fmtNum(m.peg_ratio) },
  ]
}

function MetricsCard({ metrics }: { metrics: KeyMetrics | null }) {
  const tiles = buildTiles(metrics)
  if (!tiles.length) return null
  return (
    <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-6 gap-2">
      {tiles.map((t) => (
        <div key={t.label} className="card py-2 px-3">
          <div className="text-[11px] text-[#6e7681]">{t.label}</div>
          <div className="text-sm text-[#e6edf3] tabular-nums mt-0.5">{t.value}</div>
        </div>
      ))}
    </div>
  )
}

// ── 报表表格（按期次列展开）──────────────────────────────────

type StatementTab = "income" | "balance" | "cashflow" | "ratios"

interface RowSpec<T> {
  label: string
  get: (r: T) => number | null
  kind?: "amount" | "pct" | "num"
}

const INCOME_ROWS: RowSpec<IncomeStatement>[] = [
  { label: "营业收入", get: (r) => r.revenue, kind: "amount" },
  { label: "营业成本", get: (r) => r.cost_of_revenue, kind: "amount" },
  { label: "毛利润", get: (r) => r.gross_profit, kind: "amount" },
  { label: "营业利润", get: (r) => r.operating_income, kind: "amount" },
  { label: "税前利润", get: (r) => r.pretax_income, kind: "amount" },
  { label: "净利润", get: (r) => r.net_income, kind: "amount" },
  { label: "基本EPS", get: (r) => r.basic_eps, kind: "num" },
  { label: "稀释EPS", get: (r) => r.diluted_eps, kind: "num" },
]

const BALANCE_ROWS: RowSpec<BalanceSheet>[] = [
  { label: "总资产", get: (r) => r.total_assets, kind: "amount" },
  { label: "流动资产", get: (r) => r.current_assets, kind: "amount" },
  { label: "现金及等价物", get: (r) => r.cash_and_equivalents, kind: "amount" },
  { label: "存货", get: (r) => r.inventory, kind: "amount" },
  { label: "总负债", get: (r) => r.total_liabilities, kind: "amount" },
  { label: "流动负债", get: (r) => r.current_liabilities, kind: "amount" },
  { label: "股东权益", get: (r) => r.total_equity, kind: "amount" },
  { label: "留存收益", get: (r) => r.retained_earnings, kind: "amount" },
]

const CASHFLOW_ROWS: RowSpec<CashFlow>[] = [
  { label: "经营现金流", get: (r) => r.operating_cash_flow, kind: "amount" },
  { label: "资本支出", get: (r) => r.capital_expenditure, kind: "amount" },
  { label: "自由现金流", get: (r) => r.free_cash_flow, kind: "amount" },
  { label: "投资现金流", get: (r) => r.investing_cash_flow, kind: "amount" },
  { label: "筹资现金流", get: (r) => r.financing_cash_flow, kind: "amount" },
  { label: "现金净变动", get: (r) => r.net_change_in_cash, kind: "amount" },
]

const RATIO_ROWS: RowSpec<FinancialRatios>[] = [
  { label: "毛利率", get: (r) => r.gross_margin, kind: "pct" },
  { label: "营业利润率", get: (r) => r.operating_margin, kind: "pct" },
  { label: "净利率", get: (r) => r.net_margin, kind: "pct" },
  { label: "ROE", get: (r) => r.return_on_equity, kind: "pct" },
  { label: "ROA", get: (r) => r.return_on_assets, kind: "pct" },
  { label: "流动比率", get: (r) => r.current_ratio, kind: "num" },
  { label: "速动比率", get: (r) => r.quick_ratio, kind: "num" },
  { label: "产权比率", get: (r) => r.debt_to_equity, kind: "num" },
  { label: "资产周转率", get: (r) => r.asset_turnover, kind: "num" },
]

function fmtCell(v: number | null, kind: RowSpec<unknown>["kind"], currency: string | null): string {
  if (kind === "pct") return fmtPct(v)
  if (kind === "num") return fmtNum(v)
  return fmtAmount(v, currency)
}

function StatementTable<T extends { period_ending: string | null; fiscal_year: number | null }>({
  rows,
  periods,
  currency,
}: {
  rows: RowSpec<T>[]
  periods: T[]
  currency: string | null
}) {
  if (!periods.length) {
    return <div className="text-[#6e7681] text-xs text-center py-6">暂无该报表数据</div>
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs border-collapse">
        <thead>
          <tr className="text-[#6e7681] border-b border-[#21262d]">
            <th className="text-left font-normal py-1.5 pr-3 sticky left-0 bg-[#0d1117]">项目</th>
            {periods.map((p, i) => (
              <th key={i} className="text-right font-normal py-1.5 px-3 tabular-nums">
                {periodLabel(p)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.label} className="border-b border-[#161b22] hover:bg-[#161b22]">
              <td className="text-left py-1.5 pr-3 text-[#8b949e] sticky left-0 bg-[#0d1117]">
                {row.label}
              </td>
              {periods.map((p, i) => (
                <td key={i} className="text-right py-1.5 px-3 text-[#e6edf3] tabular-nums">
                  {fmtCell(row.get(p), row.kind, currency)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── 主面板 ────────────────────────────────────────────────────

const TABS: { key: StatementTab; label: string }[] = [
  { key: "income", label: "利润表" },
  { key: "balance", label: "资产负债" },
  { key: "cashflow", label: "现金流" },
  { key: "ratios", label: "财务比率" },
]

function StatementSection({ tab, data }: { tab: StatementTab; data: FundamentalsBundle }) {
  const currency = data.currency
  if (tab === "income") return <StatementTable rows={INCOME_ROWS} periods={data.income} currency={currency} />
  if (tab === "balance") return <StatementTable rows={BALANCE_ROWS} periods={data.balance} currency={currency} />
  if (tab === "cashflow") return <StatementTable rows={CASHFLOW_ROWS} periods={data.cashflow} currency={currency} />
  return <StatementTable rows={RATIO_ROWS} periods={data.ratios} currency={currency} />
}

export interface FundamentalsPanelProps {
  symbol: string
  market: Market
  /** 返回最近 N 期报表，默认 5。 */
  limit?: number
}

/**
 * 基本面卡片（关键指标 + 三大报表 + 财务比率）。
 *
 * 自包含、可直接放入任意页面（如 Market.tsx），仅需 symbol + market。
 * 数据来源: GET /api/v1/fundamentals/{symbol} → useFundamentals。
 */
export function FundamentalsPanel({ symbol, market, limit = 5 }: FundamentalsPanelProps) {
  const [tab, setTab] = useState<StatementTab>("income")
  const { data, isLoading, isError, error } = useFundamentals({ symbol, market, limit })

  const title = useMemo(() => {
    if (!data) return symbol
    return data.name ? `${symbol} · ${data.name}` : symbol
  }, [data, symbol])

  if (isLoading) {
    return <div className="text-[#6e7681] text-xs text-center py-6">正在加载基本面数据…</div>
  }
  if (isError) {
    return (
      <div className="text-[#f85149] text-xs text-center py-6">
        基本面数据加载失败：{error instanceof Error ? error.message : "未知错误"}
      </div>
    )
  }
  if (!data) return null

  return (
    <div className="space-y-3">
      <div className="flex items-baseline justify-between">
        <h3 className="text-sm text-[#e6edf3]">{title}</h3>
        <span className="text-[11px] text-[#6e7681]">{data.currency ?? ""} · 年度</span>
      </div>

      <MetricsCard metrics={data.metrics} />

      {data.warnings.length > 0 && (
        <div className="text-[11px] text-[#d29922] bg-[#341a00]/40 rounded px-2 py-1">
          部分数据不可用：{data.warnings.join("；")}
        </div>
      )}

      <div className="card">
        <div className="flex gap-1.5 mb-3 border-b border-[#21262d] pb-2">
          {TABS.map((t) => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`px-2.5 py-1 text-xs rounded transition-colors ${
                tab === t.key
                  ? "bg-[#1c2a3a] text-[#e6edf3]"
                  : "text-[#8b949e] hover:bg-[#1c2128]"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
        <StatementSection tab={tab} data={data} />
      </div>
    </div>
  )
}
