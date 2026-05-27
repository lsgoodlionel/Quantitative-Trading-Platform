import { useState, useMemo } from "react"
import {
  PieChart, Pie, Cell, ResponsiveContainer, Tooltip as ReTooltip,
  AreaChart, Area, XAxis, YAxis, CartesianGrid,
} from "recharts"
import { AppShell } from "@/components/layout/AppShell"
import { useAccount, usePositions } from "@/hooks/usePositions"
import { useOrders } from "@/hooks/useOrders"
import { useRiskSummary } from "@/hooks/useRisk"
import { Spinner } from "@/components/ui/Spinner"
import { StatusBadge } from "@/components/ui/StatusBadge"
import { PnlCell } from "@/components/ui/PnlCell"
import type { Market, Position, LiveOrder } from "@/types"

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
  daily_orders_submitted: number
  peak_equity: number
  current_equity: number | null
  daily_realized_pnl: number
  violations: { rule_type: string; severity: string; message: string }[]
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
            {summary.daily_orders_submitted}
          </p>
        </div>
        <div className="bg-[#0d1117] rounded-lg px-3 py-2">
          <p className="text-xs text-[#8b949e]">日内盈亏</p>
          <p className={`font-mono text-base font-semibold ${pnlColor(summary.daily_realized_pnl)}`}>
            {pnlSign(summary.daily_realized_pnl)}${summary.daily_realized_pnl.toFixed(2)}
          </p>
        </div>
      </div>

      {/* Violations */}
      {summary.violations?.length > 0 && (
        <div className="flex flex-col gap-1 mt-1">
          {summary.violations.slice(0, 3).map((v, i) => (
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

// ── Sparkline (simulated intraday equity) ─────────────────────

function EquitySparkline({
  portfolioValue,
  currency,
  accentColor,
}: {
  portfolioValue: number
  currency: string
  accentColor: string
}) {
  // Generate a synthetic intraday sparkline from portfolio value with small noise
  const points = useMemo(() => {
    const base = portfolioValue * 0.992
    return Array.from({ length: 20 }, (_, i) => {
      const noise = (Math.sin(i * 1.5) * 0.004 + Math.cos(i * 0.8) * 0.003) * portfolioValue
      const trend = (i / 19) * portfolioValue * 0.008
      return { t: `${8 + Math.floor(i * 0.4)}:${String((i * 17) % 60).padStart(2, "0")}`, v: base + noise + trend }
    })
  }, [portfolioValue])

  const change = points[points.length - 1].v - points[0].v
  const changePct = (change / points[0].v) * 100

  return (
    <div>
      <div className="flex items-baseline gap-2 mb-2">
        <span className="font-mono text-2xl font-bold text-[#e6edf3]">
          {fmt(currency, portfolioValue)}
        </span>
        <span className={`text-sm font-mono ${pnlColor(change)}`}>
          {pnlSign(change)}{changePct.toFixed(2)}%
        </span>
      </div>
      <div style={{ height: 60 }}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={points} margin={{ top: 2, right: 0, left: 0, bottom: 2 }}>
            <defs>
              <linearGradient id="equityGrad" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={accentColor} stopOpacity={0.3} />
                <stop offset="95%" stopColor={accentColor} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#21262d" vertical={false} />
            <XAxis dataKey="t" hide />
            <YAxis domain={["auto", "auto"]} hide />
            <ReTooltip
              formatter={(v: number) => [`${currency}${v.toFixed(2)}`, "净值"]}
              contentStyle={{ background: "#161b22", border: "1px solid #30363d", borderRadius: 6, fontSize: 11 }}
              labelStyle={{ color: "#8b949e" }}
              itemStyle={{ color: "#e6edf3" }}
            />
            <Area
              type="monotone"
              dataKey="v"
              stroke={accentColor}
              strokeWidth={1.5}
              fill="url(#equityGrad)"
              dot={false}
              isAnimationActive={false}
            />
          </AreaChart>
        </ResponsiveContainer>
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
    <AppShell title="仪表盘">

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
              value={String(riskSummary?.daily_orders_submitted ?? "—")}
              sub={
                riskSummary
                  ? riskSummary.violations?.length > 0
                    ? `⚑ ${riskSummary.violations.length} 项违规`
                    : "风控正常"
                  : undefined
              }
              valueClass={
                riskSummary?.violations?.length
                  ? "text-[#f85149]"
                  : "text-[#e6edf3]"
              }
            />
          </>
        )}
      </div>

      {/* ── Middle Row: Equity + Composition + Risk ── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-6">

        {/* Equity sparkline */}
        <div className="card lg:col-span-1">
          <div className="card-header mb-3">
            <h2 className="text-sm font-semibold text-[#e6edf3]">净值走势（模拟日内）</h2>
          </div>
          {portfolioValue > 0
            ? <EquitySparkline portfolioValue={portfolioValue} currency={cfg.currency} accentColor={cfg.accent} />
            : <p className="text-[#6e7681] text-sm py-4 text-center">暂无数据</p>
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
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">

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

    </AppShell>
  )
}
