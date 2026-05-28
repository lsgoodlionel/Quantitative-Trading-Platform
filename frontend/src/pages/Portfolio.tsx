import { useState } from "react"
import {
  BarChart, Bar, Cell as BarCell, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell,
} from "recharts"
import { AppShell } from "@/components/layout/AppShell"
import { PAGE_HELP } from "@/data/pageHelp"
import { useAccount, usePositions } from "@/hooks/usePositions"
import { useCreateOrder, useAttribution } from "@/hooks/useOrders"
import { useToast } from "@/components/ui/Toast"
import { Spinner } from "@/components/ui/Spinner"
import { EmptyState } from "@/components/ui/EmptyState"
import { PercentCell } from "@/components/ui/PnlCell"
import type { Market } from "@/types"

const PALETTE = [
  "#58a6ff", "#3fb950", "#f85149", "#e3b341", "#bc8cff",
  "#ff9f43", "#54a0ff", "#5f27cd", "#00d2d3", "#ff6b81",
]

const MARKET_CFGS = [
  { value: "US" as Market, label: "美股 (US)",  currency: "$",   badge: "bg-[#1f3a5f] text-[#58a6ff]" },
  { value: "HK" as Market, label: "港股 (HK)",  currency: "HK$", badge: "bg-[#2a1f4f] text-[#bc8cff]" },
  { value: "A"  as Market, label: "A股",        currency: "¥",   badge: "bg-[#4f3a1a] text-[#e3b341]" },
]

function formatCurrency(val: number | null, currency: string): string {
  if (val == null) return "—"
  return `${currency}${val.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

export function Portfolio() {
  const [market, setMarket] = useState<Market>("US")
  const { data: account, isLoading: acctLoading } = useAccount(market)
  const { data: positions, isLoading: posLoading } = usePositions(market)
  const { data: attribution } = useAttribution(market)
  const { mutate: createOrder } = useCreateOrder()
  const { toast } = useToast()

  const isLoading = acctLoading || posLoading
  const openPositions = (positions ?? []).filter((p) => p.qty !== 0)
  const marketCfg = MARKET_CFGS.find((m) => m.value === market) ?? MARKET_CFGS[0]

  const totalMv = openPositions.reduce((s, p) => s + (p.market_value ?? 0), 0)
  const totalPnl = openPositions.reduce((s, p) => s + (p.unrealized_pnl ?? 0), 0)
  const pieData = openPositions.map((p) => ({
    name: p.symbol,
    value: Math.abs(p.market_value ?? 0),
    pct: totalMv > 0 ? ((p.market_value ?? 0) / totalMv) * 100 : 0,
  }))

  function handleClosePosition(symbol: string, qty: number) {
    createOrder(
      { symbol, market, side: "SELL", qty, order_type: "MARKET", limit_price: null },
      {
        onSuccess: () => toast(`已提交平仓订单：${symbol} × ${qty}`, "success"),
        onError: (e) => toast(e.message, "error"),
      },
    )
  }

  return (
    <AppShell title="持仓分析" help={PAGE_HELP["portfolio"]}>
      {/* 市场切换 */}
      <div className="flex items-center gap-2 mb-6">
        {MARKET_CFGS.map((m) => (
          <button
            key={m.value}
            onClick={() => setMarket(m.value)}
            className={`px-4 py-1.5 rounded-md text-sm font-medium border transition-colors ${
              market === m.value
                ? "bg-[#1f6feb]/20 text-[#58a6ff] border-[#58a6ff]/30"
                : "text-[#8b949e] border-[#30363d] hover:text-[#e6edf3] hover:border-[#58a6ff]/30"
            }`}
          >
            {m.label}
          </button>
        ))}
      </div>

      {isLoading ? (
        <div className="flex justify-center py-12"><Spinner size="lg" /></div>
      ) : (
        <div className="space-y-6">
          {/* 账户摘要 */}
          {account && (
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              {[
                { label: "账户ID", value: account.account_id.slice(0, 14) + "…" },
                { label: "组合净值", value: formatCurrency(account.portfolio_value, marketCfg.currency) },
                { label: "可用现金", value: formatCurrency(account.cash, marketCfg.currency) },
                { label: "购买力", value: formatCurrency(account.buying_power, marketCfg.currency) },
              ].map(({ label, value }) => (
                <div key={label} className="card">
                  <p className="text-xs text-[#6e7681] mb-1">{label}</p>
                  <p className="font-mono text-[#e6edf3] font-semibold text-sm">{value}</p>
                </div>
              ))}
            </div>
          )}

          {/* 浮盈汇总 */}
          {openPositions.length > 0 && (
            <div className="grid grid-cols-3 gap-4">
              <div className="card">
                <p className="text-xs text-[#6e7681] mb-1">持仓数量</p>
                <p className="font-mono text-[#e6edf3] font-semibold">{openPositions.length} 只</p>
              </div>
              <div className="card">
                <p className="text-xs text-[#6e7681] mb-1">总市值</p>
                <p className="font-mono text-[#e6edf3] font-semibold">{formatCurrency(totalMv, marketCfg.currency)}</p>
              </div>
              <div className="card">
                <p className="text-xs text-[#6e7681] mb-1">总浮盈</p>
                <p className={`font-mono font-semibold ${totalPnl >= 0 ? "text-[#3fb950]" : "text-[#f85149]"}`}>
                  {totalPnl >= 0 ? "+" : ""}{formatCurrency(totalPnl, marketCfg.currency)}
                </p>
              </div>
            </div>
          )}

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* 持仓分布饼图 */}
            <div className="card lg:col-span-1">
              <h3 className="text-sm font-semibold text-[#e6edf3] mb-4">持仓分布</h3>
              {openPositions.length === 0 ? (
                <EmptyState title="暂无持仓" />
              ) : (
                <>
                  <ResponsiveContainer width="100%" height={200}>
                    <PieChart>
                      <Pie
                        data={pieData}
                        cx="50%"
                        cy="50%"
                        innerRadius={55}
                        outerRadius={85}
                        paddingAngle={2}
                        dataKey="value"
                      >
                        {pieData.map((_, idx) => (
                          <Cell key={idx} fill={PALETTE[idx % PALETTE.length]} />
                        ))}
                      </Pie>
                      <Tooltip
                        contentStyle={{
                          background: "#161b22", border: "1px solid #30363d",
                          borderRadius: 6, fontSize: 12,
                        }}
                        formatter={(val: number) => [
                          `${marketCfg.currency}${val.toLocaleString()}`, "市值",
                        ]}
                      />
                    </PieChart>
                  </ResponsiveContainer>
                  <div className="space-y-1.5 mt-2">
                    {pieData.map((d, idx) => (
                      <div key={d.name} className="flex items-center justify-between text-xs">
                        <div className="flex items-center gap-2">
                          <span
                            className="w-2.5 h-2.5 rounded-sm shrink-0"
                            style={{ background: PALETTE[idx % PALETTE.length] }}
                          />
                          <span className="font-mono text-[#e6edf3]">{d.name}</span>
                        </div>
                        <span className="text-[#8b949e]">{d.pct.toFixed(1)}%</span>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </div>

            {/* 持仓明细表 */}
            <div className="card lg:col-span-2">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-sm font-semibold text-[#e6edf3]">持仓明细</h3>
                {openPositions.length > 0 && (
                  <span className={`text-xs px-2 py-0.5 rounded ${marketCfg.badge}`}>
                    {marketCfg.label}
                  </span>
                )}
              </div>
              {openPositions.length === 0 ? (
                <EmptyState title="暂无持仓" description="提交买入订单后持仓将显示在此处" />
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-[#8b949e] text-xs border-b border-[#21262d]">
                        <th className="text-left py-2 pr-3">标的</th>
                        <th className="text-right py-2 pr-3">持仓</th>
                        <th className="text-right py-2 pr-3">均价</th>
                        <th className="text-right py-2 pr-3">现价</th>
                        <th className="text-right py-2 pr-3">市值</th>
                        <th className="text-right py-2 pr-3">浮盈</th>
                        <th className="text-right py-2 pr-3">浮盈率</th>
                        <th className="py-2"></th>
                      </tr>
                    </thead>
                    <tbody>
                      {openPositions.map((p) => (
                        <tr
                          key={`${p.market}-${p.symbol}`}
                          className="border-b border-[#21262d]/50 last:border-0 hover:bg-[#1c2128]/50 transition-colors"
                        >
                          <td className="py-2.5 pr-3 font-mono font-medium text-[#e6edf3]">{p.symbol}</td>
                          <td className="py-2.5 pr-3 text-right font-mono text-[#e6edf3]">
                            {p.qty.toLocaleString()}
                          </td>
                          <td className="py-2.5 pr-3 text-right font-mono text-[#8b949e] text-xs">
                            {formatCurrency(p.avg_cost, marketCfg.currency)}
                          </td>
                          <td className="py-2.5 pr-3 text-right font-mono text-xs text-[#e6edf3]">
                            {formatCurrency(p.current_price, marketCfg.currency)}
                          </td>
                          <td className="py-2.5 pr-3 text-right font-mono text-xs text-[#e6edf3]">
                            {formatCurrency(p.market_value, marketCfg.currency)}
                          </td>
                          <td className="py-2.5 pr-3 text-right">
                            <span className={`font-mono text-sm font-medium ${(p.unrealized_pnl ?? 0) >= 0 ? "text-[#3fb950]" : "text-[#f85149]"}`}>
                              {(p.unrealized_pnl ?? 0) >= 0 ? "+" : ""}
                              {formatCurrency(p.unrealized_pnl, marketCfg.currency)}
                            </span>
                          </td>
                          <td className="py-2.5 pr-3 text-right">
                            <PercentCell value={p.unrealized_pnl_pct} />
                          </td>
                          <td className="py-2.5">
                            <button
                              onClick={() => handleClosePosition(p.symbol, p.qty)}
                              className="text-xs text-[#f85149] hover:text-[#ff7b72] border border-[#f85149]/30 hover:border-[#f85149]/60 rounded px-2 py-0.5 transition-colors whitespace-nowrap"
                            >
                              一键平仓
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>

          {/* ── Performance Attribution ── */}
          {attribution && attribution.positions.length > 0 && (
            <div className="space-y-4">
              {/* Attribution Summary */}
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                <div className="card">
                  <p className="text-xs text-[#6e7681] mb-1">已实现盈亏</p>
                  <p className={`font-mono font-semibold text-base ${attribution.totals.realized_pnl >= 0 ? "text-[#3fb950]" : "text-[#f85149]"}`}>
                    {attribution.totals.realized_pnl >= 0 ? "+" : ""}
                    {formatCurrency(attribution.totals.realized_pnl, marketCfg.currency)}
                  </p>
                </div>
                <div className="card">
                  <p className="text-xs text-[#6e7681] mb-1">累计手续费</p>
                  <p className="font-mono font-semibold text-base text-[#e3b341]">
                    {formatCurrency(attribution.totals.commission, marketCfg.currency)}
                  </p>
                </div>
                <div className="card">
                  <p className="text-xs text-[#6e7681] mb-1">总交易次数</p>
                  <p className="font-mono font-semibold text-base text-[#e6edf3]">{attribution.totals.trade_count} 笔</p>
                </div>
                <div className="card">
                  <p className="text-xs text-[#6e7681] mb-1">涉及标的</p>
                  <p className="font-mono font-semibold text-base text-[#e6edf3]">{attribution.totals.symbol_count} 只</p>
                </div>
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                {/* P&L Waterfall Chart */}
                <div className="card lg:col-span-2">
                  <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">各标的已实现盈亏</h3>
                  <ResponsiveContainer width="100%" height={200}>
                    <BarChart
                      data={attribution.positions.slice(0, 12).map((a) => ({
                        name: a.symbol,
                        pnl: a.realized_pnl,
                      }))}
                      margin={{ top: 4, right: 4, left: 0, bottom: 20 }}
                    >
                      <CartesianGrid strokeDasharray="3 3" stroke="#21262d" vertical={false} />
                      <XAxis
                        dataKey="name"
                        tick={{ fill: "#8b949e", fontSize: 10 }}
                        angle={-30}
                        textAnchor="end"
                        interval={0}
                      />
                      <YAxis
                        tick={{ fill: "#8b949e", fontSize: 10 }}
                        width={56}
                        tickFormatter={(v) => `${v >= 0 ? "+" : ""}${v.toFixed(0)}`}
                      />
                      <Tooltip
                        formatter={(v: number) => [
                          `${v >= 0 ? "+" : ""}${marketCfg.currency}${v.toFixed(2)}`,
                          "已实现盈亏",
                        ]}
                        contentStyle={{ background: "#161b22", border: "1px solid #30363d", fontSize: 11 }}
                        labelStyle={{ color: "#8b949e" }}
                        itemStyle={{ color: "#e6edf3" }}
                      />
                      <Bar dataKey="pnl" radius={[3, 3, 0, 0]}>
                        {attribution.positions.slice(0, 12).map((a, i) => (
                          <BarCell key={i} fill={a.realized_pnl >= 0 ? "#3fb950" : "#f85149"} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>

                {/* Attribution Table */}
                <div className="card lg:col-span-1">
                  <h3 className="text-sm font-semibold text-[#e6edf3] mb-3">绩效归因明细</h3>
                  <div className="space-y-1 overflow-y-auto max-h-52">
                    {attribution.positions.map((a) => (
                      <div key={`${a.market}-${a.symbol}`} className="flex items-center gap-2 py-1 border-b border-[#21262d]/40 last:border-0">
                        <span className="font-mono text-xs text-[#e6edf3] w-16 shrink-0">{a.symbol}</span>
                        <div className="flex-1">
                          <div
                            className="h-1.5 rounded"
                            style={{
                              width: `${Math.min(Math.abs(a.realized_pnl) / (Math.max(...attribution.positions.map((x) => Math.abs(x.realized_pnl))) || 1) * 100, 100)}%`,
                              background: a.realized_pnl >= 0 ? "#3fb950" : "#f85149",
                            }}
                          />
                        </div>
                        <span className={`text-xs font-mono shrink-0 ${a.realized_pnl >= 0 ? "text-[#3fb950]" : "text-[#f85149]"}`}>
                          {a.realized_pnl >= 0 ? "+" : ""}{a.realized_pnl.toFixed(0)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </AppShell>
  )
}
