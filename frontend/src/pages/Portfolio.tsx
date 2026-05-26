import { useState } from "react"
import { AppShell } from "@/components/layout/AppShell"
import { useAccount, usePositions } from "@/hooks/usePositions"
import { Spinner } from "@/components/ui/Spinner"
import { EmptyState } from "@/components/ui/EmptyState"
import { PnlCell, PercentCell } from "@/components/ui/PnlCell"
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer } from "recharts"
import type { Market } from "@/types"

const PALETTE = [
  "#58a6ff", "#3fb950", "#f85149", "#e3b341", "#bc8cff",
  "#ff9f43", "#54a0ff", "#5f27cd", "#00d2d3", "#ff6b81",
]

export function Portfolio() {
  const [market, setMarket] = useState<Market>("US")
  const { data: account, isLoading: acctLoading } = useAccount(market)
  const { data: positions, isLoading: posLoading } = usePositions(market)

  const isLoading = acctLoading || posLoading
  const openPositions = (positions ?? []).filter((p) => p.qty !== 0)

  const totalMv = openPositions.reduce((s, p) => s + (p.market_value ?? 0), 0)
  const pieData = openPositions.map((p) => ({
    name: p.symbol,
    value: p.market_value ?? 0,
    pct: totalMv > 0 ? ((p.market_value ?? 0) / totalMv) * 100 : 0,
  }))

  return (
    <AppShell title="持仓分析">
      {/* Market selector */}
      <div className="flex items-center gap-3 mb-6">
        {(["US", "HK"] as Market[]).map((m) => (
          <button
            key={m}
            onClick={() => setMarket(m)}
            className={`px-4 py-1.5 rounded-md text-sm font-medium border transition-colors ${
              market === m
                ? "bg-[#1f6feb]/20 text-[#58a6ff] border-[#58a6ff]/30"
                : "text-[#8b949e] border-[#30363d] hover:text-[#e6edf3] hover:border-[#58a6ff]/30"
            }`}
          >
            {m}
          </button>
        ))}
      </div>

      {isLoading ? (
        <div className="flex justify-center py-12"><Spinner size="lg" /></div>
      ) : (
        <div className="space-y-6">
          {/* Account Summary */}
          {account && (
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              {[
                { label: "账户ID", value: account.account_id.slice(0, 12) + "…" },
                { label: "组合净值", value: `$${account.portfolio_value.toLocaleString()}` },
                { label: "可用资金", value: `$${account.cash.toLocaleString()}` },
                { label: "购买力", value: `$${account.buying_power.toLocaleString()}` },
              ].map(({ label, value }) => (
                <div key={label} className="card">
                  <p className="text-xs text-[#6e7681] mb-1">{label}</p>
                  <p className="font-mono text-[#e6edf3] font-semibold">{value}</p>
                </div>
              ))}
            </div>
          )}

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* Allocation Pie */}
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
                        innerRadius={60}
                        outerRadius={90}
                        paddingAngle={2}
                        dataKey="value"
                      >
                        {pieData.map((_, idx) => (
                          <Cell key={idx} fill={PALETTE[idx % PALETTE.length]} />
                        ))}
                      </Pie>
                      <Tooltip
                        contentStyle={{ background: "#161b22", border: "1px solid #30363d", borderRadius: 6, fontSize: 12 }}
                        formatter={(val: number) => [`$${val.toLocaleString()}`, "市值"]}
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

            {/* Positions Table */}
            <div className="card lg:col-span-2">
              <h3 className="text-sm font-semibold text-[#e6edf3] mb-4">持仓明细</h3>
              {openPositions.length === 0 ? (
                <EmptyState title="暂无持仓" />
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
                        <th className="text-right py-2">浮盈率</th>
                      </tr>
                    </thead>
                    <tbody>
                      {openPositions.map((p) => (
                        <tr key={`${p.market}-${p.symbol}`} className="border-b border-[#21262d]/50 last:border-0">
                          <td className="py-2 pr-3 font-mono text-[#e6edf3]">{p.symbol}</td>
                          <td className="py-2 pr-3 text-right font-mono text-[#e6edf3]">{p.qty}</td>
                          <td className="py-2 pr-3 text-right font-mono text-[#8b949e]">${p.avg_cost.toFixed(2)}</td>
                          <td className="py-2 pr-3 text-right font-mono text-[#e6edf3]">
                            {p.current_price ? `$${p.current_price.toFixed(2)}` : "—"}
                          </td>
                          <td className="py-2 pr-3 text-right font-mono text-[#e6edf3]">
                            {p.market_value ? `$${p.market_value.toLocaleString()}` : "—"}
                          </td>
                          <td className="py-2 pr-3 text-right"><PnlCell value={p.unrealized_pnl} /></td>
                          <td className="py-2 text-right"><PercentCell value={p.unrealized_pnl_pct} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </AppShell>
  )
}
