import { useState, useMemo } from "react"
import {
  PieChart, Pie, Cell, ResponsiveContainer, Tooltip as ReTooltip,
} from "recharts"
import { AppShell } from "@/components/layout/AppShell"
import { useAccount, usePositions } from "@/hooks/usePositions"
import { useOrders } from "@/hooks/useOrders"
import { useRiskSummary } from "@/hooks/useRisk"
import { useMarketOverview } from "@/hooks/useMarketData"
import { Spinner } from "@/components/ui/Spinner"
import { StatusBadge } from "@/components/ui/StatusBadge"
import { PnlCell } from "@/components/ui/PnlCell"
import { TradingWorkflow } from "@/components/workflow/TradingWorkflow"
import { PAGE_HELP } from "@/data/pageHelp"
import type { Market, Position, LiveOrder, MarketOverviewItem } from "@/types"

// ── Constants ──────────────────────────────────────────────────

const MARKETS: Market[] = ["US", "HK", "A"]

const MARKET_CFG: Record<Market, { label: string; currency: string; flag: string; accent: string }> = {
  US: { label: "美股",  currency: "$",    flag: "🇺🇸", accent: "#58a6ff" },
  HK: { label: "港股",  currency: "HK$",  flag: "🇭🇰", accent: "#bc8cff" },
  A:  { label: "A股",   currency: "¥",    flag: "🇨🇳", accent: "#e3b341" },
}

const PIE_COLORS = [
  "#58a6ff", "#3fb950", "#e3b341", "#bc8cff", "#f78166", "#79c0ff",
  "#56d364", "#d2a8ff", "#ffa657", "#ff7b72",
]

// ── Helpers ────────────────────────────────────────────────────

function fmt(currency: string, value: number): string {
  const abs = Math.abs(value)
  const sign = value < 0 ? "-" : ""
  if (abs >= 1_000_000) return `${sign}${currency}${(abs / 1_000_000).toFixed(2)}M`
  if (abs >= 1_000)     return `${sign}${currency}${(abs / 1_000).toFixed(2)}K`
  return `${sign}${currency}${abs.toFixed(2)}`
}

function pnlColor(v: number): string {
  return v > 0 ? "text-[#3fb950]" : v < 0 ? "text-[#f85149]" : "text-[#8b949e]"
}

function pnlSign(v: number): string {
  return v >= 0 ? "+" : ""
}

// ── Sub-components ─────────────────────────────────────────────

interface StatCardProps {
  label: string
  value: string
  sub?: string
  valueClass?: string
  loading?: boolean
}

function StatCard({ label, value, sub, valueClass = "text-[#e6edf3]", loading }: StatCardProps) {
  return (
    <div className="card flex flex-col gap-1">
      <p className="text-xs text-[#8b949e]">{label}</p>
      {loading
        ? <div className="h-7 w-24 bg-[#21262d] rounded animate-pulse" />
        : <p className={`font-mono text-xl font-semibold ${valueClass}`}>{value}</p>
      }
      {sub && <p className="text-xs text-[#6e7681]">{sub}</p>}
    </div>
  )
}

// ── Position Donut Chart ───────────────────────────────────────

interface DonutSlice { name: string; value: number; color: string }

interface PositionDonutProps {
  positions: Position[]
  cash: number
  currency: string
  totalValue: number
}

function PositionDonut({ positions, cash, currency, totalValue }: PositionDonutProps) {
  const slices = useMemo<DonutSlice[]>(() => {
    const posSlices = positions
      .filter((p) => p.qty !== 0)
      .map((p, i) => ({
        name: p.symbol,
        value: p.market_value ?? p.avg_cost * Math.abs(p.qty),
        color: PIE_COLORS[i % PIE_COLORS.length],
      }))
    if (cash > 0) {
      posSlices.push({ name: "现金", value: cash, color: "#30363d" })
    }
    return posSlices
  }, [positions, cash])

  if (slices.length === 0) {
    return (
      <div className="flex items-center justify-center h-40 text-[#6e7681] text-sm">
        暂无持仓
      </div>
    )
  }

  return (
    <div className="flex gap-6 items-center">
      {/* Donut */}
      <div className="shrink-0" style={{ width: 140, height: 140 }}>
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={slices}
              cx="50%"
              cy="50%"
              innerRadius={42}
              outerRadius={62}
              dataKey="value"
              stroke="none"
            >
              {slices.map((s, i) => (
                <Cell key={i} fill={s.color} />
              ))}
            </Pie>
            <ReTooltip
              formatter={(val: number) => [`${currency}${val.toFixed(2)}`, ""]}
              contentStyle={{ background: "#161b22", border: "1px solid #30363d", borderRadius: 6 }}
              labelStyle={{ color: "#e6edf3" }}
              itemStyle={{ color: "#8b949e" }}
            />
          </PieChart>
        </ResponsiveContainer>
      </div>
      {/* Legend */}
      <div className="flex flex-col gap-1.5 min-w-0 flex-1">
        {slices.map((s) => {
          const pct = totalValue > 0 ? (s.value / totalValue) * 100 : 0
          return (
            <div key={s.name} className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full shrink-0" style={{ background: s.color }} />
              <span className="text-xs text-[#e6edf3] font-mono truncate flex-1">{s.name}</span>
              <span className="text-xs text-[#8b949e] shrink-0">{pct.toFixed(1)}%</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Risk Panel ─────────────────────────────────────────────────

interface RiskSummary {
  date: string
  orders_today: number
  realized_pnl_today: number
  peak_portfolio_value: number
  violations?: { rule_type: string; severity: string; message: string }[]
}

function RiskPanel({ summary }: { summary: RiskSummary | undefined }) {
  if (!summary) return <div className="flex justify-center py-6"><Spinner /></div>

  const violationCount = summary.violations?.length ?? 0
  const statusColor = violationCount === 0 ? "#3fb950" : "#f85149"
  const statusLabel = violationCount === 0 ? "正常" : `${violationCount} 项违规`

  return (
    <div className="flex flex-col gap-3">
      {/* Status badge */}
      <div className="flex items-center gap-2">
        <span className="w-2 h-2 rounded-full" style={{ background: statusColor }} />
        <span className="text-sm font-medium" style={{ color: statusColor }}>{statusLabel}</span>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-2 gap-2">
        <div className="bg-[#0d1117] rounded-lg px-3 py-2">
          <p className="text-xs text-[#8b949e]">今日订单</p>
          <p className="font-mono text-base font-semibold text-[#e6edf3]">
            {summary.orders_today}
          </p>
        </div>
        <div className="bg-[#0d1117] rounded-lg px-3 py-2">
          <p className="text-xs text-[#8b949e]">日内盈亏</p>
          <p className={`font-mono text-base font-semibold ${pnlColor(summary.realized_pnl_today)}`}>
            {pnlSign(summary.realized_pnl_today)}${summary.realized_pnl_today.toFixed(2)}
          </p>
        </div>
      </div>

      {/* Violations */}
      {(summary.violations?.length ?? 0) > 0 && (
        <div className="flex flex-col gap-1 mt-1">
          {summary.violations!.slice(0, 3).map((v, i) => (
            <div key={i} className="flex items-start gap-2 text-xs bg-[#2a1b1b] rounded px-2 py-1.5">
              <span className="text-[#f85149] mt-0.5">⚑</span>
              <span className="text-[#f85149] leading-tight">{v.message}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Activity Feed (recent orders) ──────────────────────────────

function ActivityFeed({ orders, currency }: { orders: LiveOrder[]; currency: string }) {
  if (orders.length === 0) {
    return <p className="text-[#6e7681] text-sm py-4 text-center">暂无近期活动</p>
  }

  return (
    <div className="flex flex-col divide-y divide-[#21262d]">
      {orders.map((o) => {
        const price = o.avg_fill_price ?? o.limit_price
        return (
          <div key={o.order_id} className="flex items-center gap-3 py-2.5">
            {/* Side indicator */}
            <span
              className={`text-xs font-bold px-1.5 py-0.5 rounded ${
                o.side === "BUY" ? "bg-[#1a3a1a] text-[#3fb950]" : "bg-[#3a1a1a] text-[#f85149]"
              }`}
            >
              {o.side === "BUY" ? "买" : "卖"}
            </span>
            {/* Symbol + market */}
            <div className="flex-1 min-w-0">
              <span className="font-mono text-sm text-[#e6edf3]">{o.symbol}</span>
              <span className="ml-1 text-xs text-[#6e7681]">{o.market}</span>
            </div>
            {/* Qty × price */}
            <div className="text-right shrink-0">
              <p className="text-xs font-mono text-[#e6edf3]">{o.qty} 股</p>
              {price != null && (
                <p className="text-xs text-[#8b949e]">{currency}{price.toFixed(2)}</p>
              )}
            </div>
            {/* Status */}
            <StatusBadge status={o.status} />
          </div>
        )
      })}
    </div>
  )
}

// ── Account Summary Card ───────────────────────────────────────
// 用真实账户数据替换了原先的合成净值曲线（EquitySparkline 使用 Math.sin 生成假数据，已移除）

function AccountSummaryCard({
  portfolioValue,
  cash,
  buyingPower,
  openPositions,
  currency,
  accentColor,
}: {
  portfolioValue: number
  cash: number
  buyingPower: number
  openPositions: Position[]
  currency: string
  accentColor: string
}) {
  const investedValue = portfolioValue - cash
  const investedPct   = portfolioValue > 0 ? (investedValue / portfolioValue) * 100 : 0

  return (
    <div>
      <div className="flex items-baseline gap-2 mb-3">
        <span className="font-mono text-2xl font-bold text-[#e6edf3]">
          {fmt(currency, portfolioValue)}
        </span>
        <span className="text-xs text-[#6e7681]">组合总值</span>
      </div>

      {/* 资金分布 bar */}
      <div className="mb-3">
        <div className="flex items-center justify-between text-[10px] text-[#6e7681] mb-1">
          <span>持仓 {investedPct.toFixed(1)}%</span>
          <span>现金 {(100 - investedPct).toFixed(1)}%</span>
        </div>
        <div className="h-1.5 rounded-full bg-[#21262d] overflow-hidden">
          <div className="h-full rounded-full transition-all duration-500"
            style={{ width: `${Math.min(investedPct, 100)}%`, background: accentColor }} />
        </div>
      </div>

      {/* 关键数字 */}
      <div className="grid grid-cols-2 gap-2">
        <div className="bg-[#0d1117] rounded-lg px-3 py-2">
          <p className="text-[10px] text-[#6e7681]">可用资金</p>
          <p className="font-mono text-sm font-semibold text-[#e6edf3]">{fmt(currency, cash)}</p>
        </div>
        <div className="bg-[#0d1117] rounded-lg px-3 py-2">
          <p className="text-[10px] text-[#6e7681]">购买力</p>
          <p className="font-mono text-sm font-semibold text-[#e6edf3]">{fmt(currency, buyingPower)}</p>
        </div>
        <div className="bg-[#0d1117] rounded-lg px-3 py-2">
          <p className="text-[10px] text-[#6e7681]">持仓市值</p>
          <p className="font-mono text-sm font-semibold" style={{ color: accentColor }}>{fmt(currency, investedValue)}</p>
        </div>
        <div className="bg-[#0d1117] rounded-lg px-3 py-2">
          <p className="text-[10px] text-[#6e7681]">持仓标的</p>
          <p className="font-mono text-sm font-semibold text-[#e6edf3]">{openPositions.length} 个</p>
        </div>
      </div>
    </div>
  )
}

// ── Positions Table ────────────────────────────────────────────

function PositionsTable({ positions, currency }: { positions: Position[]; currency: string }) {
  const open = positions.filter((p) => p.qty !== 0)

  if (open.length === 0) {
    return <p className="text-[#6e7681] text-sm py-6 text-center">暂无持仓</p>
  }

  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-[#8b949e] text-xs border-b border-[#21262d]">
          <th className="text-left py-2 pr-3">标的</th>
          <th className="text-right py-2 pr-3">数量</th>
          <th className="text-right py-2 pr-3">均价</th>
          <th className="text-right py-2 pr-3">市值</th>
          <th className="text-right py-2">浮盈</th>
        </tr>
      </thead>
      <tbody>
        {open.map((p) => {
          const mv = p.market_value ?? p.avg_cost * Math.abs(p.qty)
          return (
            <tr key={`${p.market}-${p.symbol}`} className="border-b border-[#21262d]/40 last:border-0">
              <td className="py-2 pr-3 font-mono text-[#e6edf3]">{p.symbol}</td>
              <td className="py-2 pr-3 text-right font-mono text-[#e6edf3]">{p.qty}</td>
              <td className="py-2 pr-3 text-right font-mono text-[#8b949e]">
                {currency}{p.avg_cost.toFixed(2)}
              </td>
              <td className="py-2 pr-3 text-right font-mono text-[#e6edf3]">
                {fmt(currency, mv)}
              </td>
              <td className="py-2 text-right">
                <PnlCell value={p.unrealized_pnl} />
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// ── Market Movers ──────────────────────────────────────────────

const MARKET_ACCENT: Record<string, string> = {
  A:  "#e3b341",
  HK: "#bc8cff",
  US: "#58a6ff",
}

const MARKET_FLAG: Record<string, string> = {
  A: "🇨🇳", HK: "🇭🇰", US: "🇺🇸",
}

const MARKET_LABEL: Record<string, string> = {
  A: "A股", HK: "港股", US: "美股",
}

interface MoverRowProps { item: MarketOverviewItem; rank: number; isGainer: boolean }

function MoverRow({ item, rank, isGainer }: MoverRowProps) {
  const pct = item.change_pct
  const price = item.price
  return (
    <div className="flex items-center gap-2 py-1.5 border-b border-[#21262d]/40 last:border-0">
      <span className="text-[#6e7681] text-[10px] w-4 shrink-0">{rank}</span>
      <div className="flex-1 min-w-0">
        <p className="font-mono text-xs text-[#e6edf3] truncate">{item.name_zh ?? item.symbol}</p>
        <p className="text-[10px] text-[#6e7681] font-mono">{item.symbol}</p>
      </div>
      <div className="text-right shrink-0">
        {price != null && (
          <p className="text-xs font-mono text-[#8b949e]">{price.toFixed(2)}</p>
        )}
        {pct != null && (
          <p className={`text-xs font-mono font-semibold ${isGainer ? "text-[#3fb950]" : "text-[#f85149]"}`}>
            {isGainer ? "+" : ""}{pct.toFixed(2)}%
          </p>
        )}
      </div>
    </div>
  )
}

interface MarketSectionProps { market: string; items: MarketOverviewItem[] }

function MarketSection({ market, items }: MarketSectionProps) {
  const accent = MARKET_ACCENT[market] ?? "#58a6ff"
  const withPct = items.filter((i) => i.change_pct != null)
  const sorted = [...withPct].sort((a, b) => (b.change_pct ?? 0) - (a.change_pct ?? 0))
  const gainers = sorted.filter((i) => (i.change_pct ?? 0) > 0).slice(0, 3)
  const losers  = sorted.filter((i) => (i.change_pct ?? 0) < 0).reverse().slice(0, 3)

  const upCount   = withPct.filter((i) => (i.change_pct ?? 0) > 0).length
  const downCount = withPct.filter((i) => (i.change_pct ?? 0) < 0).length

  return (
    <div className="card flex-1 min-w-0">
      {/* Header */}
      <div className="card-header mb-3">
        <div className="flex items-center gap-2">
          <span className="text-base">{MARKET_FLAG[market]}</span>
          <h3 className="text-sm font-semibold text-[#e6edf3]">{MARKET_LABEL[market]}</h3>
        </div>
        <div className="flex items-center gap-2 text-[10px]">
          <span className="text-[#3fb950]">↑{upCount}</span>
          <span className="text-[#f85149]">↓{downCount}</span>
        </div>
      </div>

      {/* Gainers */}
      {gainers.length > 0 && (
        <div className="mb-3">
          <p className="text-[10px] text-[#3fb950] mb-1 font-medium flex items-center gap-1">
            <span style={{ color: accent }}>▲</span> 涨幅榜
          </p>
          {gainers.map((item, i) => (
            <MoverRow key={item.symbol} item={item} rank={i + 1} isGainer />
          ))}
        </div>
      )}

      {/* Losers */}
      {losers.length > 0 && (
        <div>
          <p className="text-[10px] text-[#f85149] mb-1 font-medium">▼ 跌幅榜</p>
          {losers.map((item, i) => (
            <MoverRow key={item.symbol} item={item} rank={i + 1} isGainer={false} />
          ))}
        </div>
      )}

      {gainers.length === 0 && losers.length === 0 && (
        <p className="text-[10px] text-[#6e7681] text-center py-4">暂无行情数据</p>
      )}
    </div>
  )
}

function MarketMovers() {
  const { data: overview, isLoading } = useMarketOverview()

  return (
    <div className="mb-6">
      <div className="flex items-center gap-2 mb-3">
        <h2 className="text-sm font-semibold text-[#e6edf3]">市场热点</h2>
        <span className="text-[10px] text-[#6e7681] bg-[#1c2128] px-1.5 py-0.5 rounded border border-[#30363d]">
          1分钟刷新
        </span>
      </div>
      {isLoading ? (
        <div className="flex justify-center py-6"><Spinner /></div>
      ) : overview ? (
        <div className="flex gap-4">
          {(["A", "HK", "US"] as const).map((mkt) => (
            <MarketSection key={mkt} market={mkt} items={overview[mkt] ?? []} />
          ))}
        </div>
      ) : null}
    </div>
  )
}

// ── Main Dashboard ─────────────────────────────────────────────

export function Dashboard() {
  const [market, setMarket] = useState<Market>("US")
  const cfg = MARKET_CFG[market]

  const { data: account, isLoading: acctLoading } = useAccount(market)
  const { data: positions = [] }                   = usePositions(market)
  const { data: orders = [] }                      = useOrders()
  const { data: riskSummary }                      = useRiskSummary()

  const openPositions = useMemo(() => positions.filter((p) => p.qty !== 0), [positions])

  const totalPnl = useMemo(
    () => openPositions.reduce((sum, p) => sum + (p.unrealized_pnl ?? 0), 0),
    [openPositions],
  )

  const recentOrders = useMemo(
    () => orders.filter((o) => o.market === market).slice(0, 6),
    [orders, market],
  )

  const portfolioValue = account?.portfolio_value ?? 0
  const cash = account?.cash ?? 0

  return (
    <AppShell title="仪表盘" help={PAGE_HELP.dashboard}>

      {/* ── 智能交易引导（置顶，首屏可见） ── */}
      <TradingWorkflow />

      {/* ── Market Selector ── */}
      <div className="flex gap-1 mb-5 bg-[#161b22] rounded-lg p-1 w-fit border border-[#21262d]">
        {MARKETS.map((m) => {
          const c = MARKET_CFG[m]
          return (
            <button
              key={m}
              onClick={() => setMarket(m)}
              className={`px-4 py-1.5 rounded-md text-sm font-medium transition-all ${
                market === m
                  ? "bg-[#21262d] text-[#e6edf3] shadow"
                  : "text-[#8b949e] hover:text-[#e6edf3]"
              }`}
            >
              {c.flag} {c.label}
            </button>
          )
        })}
      </div>

      {/* ── Stat Cards ── */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        {acctLoading ? (
          <div className="col-span-4 flex justify-center py-8"><Spinner /></div>
        ) : (
          <>
            <StatCard
              label="组合净值"
              value={account ? fmt(cfg.currency, account.portfolio_value) : "—"}
              sub={account ? `货币：${account.currency}` : undefined}
            />
            <StatCard
              label="可用资金"
              value={account ? fmt(cfg.currency, account.cash) : "—"}
              sub={account ? `购买力：${fmt(cfg.currency, account.buying_power)}` : undefined}
            />
            <StatCard
              label="持仓浮盈"
              value={
                totalPnl !== 0
                  ? `${pnlSign(totalPnl)}${cfg.currency}${Math.abs(totalPnl).toFixed(2)}`
                  : "—"
              }
              sub={`${openPositions.length} 个持仓`}
              valueClass={pnlColor(totalPnl)}
            />
            <StatCard
              label="今日提交"
              value={riskSummary ? String(riskSummary.orders_today) : "—"}
              sub={
                riskSummary
                  ? (riskSummary.violations?.length ?? 0) > 0
                    ? `⚑ ${riskSummary.violations!.length} 项违规`
                    : "风控正常"
                  : undefined
              }
              valueClass={
                (riskSummary?.violations?.length ?? 0) > 0
                  ? "text-[#f85149]"
                  : "text-[#e6edf3]"
              }
            />
          </>
        )}
      </div>

      {/* ── Market Movers ── */}
      <MarketMovers />

      {/* ── Middle Row: Equity + Composition + Risk ── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-6">

        {/* Account summary (real data) */}
        <div className="card lg:col-span-1">
          <div className="card-header mb-3">
            <h2 className="text-sm font-semibold text-[#e6edf3]">账户概览</h2>
            <span className="text-[10px] text-[#6e7681]">{cfg.flag} {cfg.label}</span>
          </div>
          {portfolioValue > 0
            ? <AccountSummaryCard
                portfolioValue={portfolioValue}
                cash={cash}
                buyingPower={account?.buying_power ?? cash}
                openPositions={openPositions}
                currency={cfg.currency}
                accentColor={cfg.accent}
              />
            : <p className="text-[#6e7681] text-sm py-4 text-center">暂无账户数据</p>
          }
        </div>

        {/* Position donut */}
        <div className="card lg:col-span-1">
          <div className="card-header mb-3">
            <h2 className="text-sm font-semibold text-[#e6edf3]">持仓构成</h2>
          </div>
          <PositionDonut
            positions={positions}
            cash={cash}
            currency={cfg.currency}
            totalValue={portfolioValue}
          />
        </div>

        {/* Risk panel */}
        <div className="card lg:col-span-1">
          <div className="card-header mb-3">
            <h2 className="text-sm font-semibold text-[#e6edf3]">风控状态</h2>
          </div>
          <RiskPanel summary={riskSummary} />
        </div>
      </div>

      {/* ── Bottom Row: Positions + Activity ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mb-6">

        {/* Positions table */}
        <div className="card">
          <div className="card-header mb-3">
            <h2 className="text-sm font-semibold text-[#e6edf3]">当前持仓</h2>
            <span className="text-xs text-[#8b949e]">{openPositions.length} 个</span>
          </div>
          <div className="overflow-x-auto">
            <PositionsTable positions={positions} currency={cfg.currency} />
          </div>
        </div>

        {/* Activity feed */}
        <div className="card">
          <div className="card-header mb-3">
            <h2 className="text-sm font-semibold text-[#e6edf3]">近期活动</h2>
            <span className="text-xs text-[#8b949e]">{cfg.flag} {cfg.label}</span>
          </div>
          <ActivityFeed orders={recentOrders} currency={cfg.currency} />
        </div>
      </div>

      {/* ── 快捷入口提示（对首次访问用户） ── */}

    </AppShell>
  )
}
