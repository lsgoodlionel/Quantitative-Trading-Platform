import { AppShell } from "@/components/layout/AppShell"
import { useAccount, usePositions } from "@/hooks/usePositions"
import { useOrders } from "@/hooks/useOrders"
import { useRiskSummary } from "@/hooks/useRisk"
import { Spinner } from "@/components/ui/Spinner"
import { StatusBadge } from "@/components/ui/StatusBadge"
import { PnlCell } from "@/components/ui/PnlCell"
import type { LiveOrder, Position } from "@/types"

function StatCard({
  label,
  value,
  sub,
  accent,
}: {
  label: string
  value: string
  sub?: string
  accent?: "up" | "down" | "neutral"
}) {
  const valueColor =
    accent === "up"
      ? "text-[#3fb950]"
      : accent === "down"
        ? "text-[#f85149]"
        : "text-[#e6edf3]"

  return (
    <div className="card">
      <p className="text-xs text-[#8b949e] mb-1">{label}</p>
      <p className={`font-mono text-2xl font-semibold ${valueColor}`}>{value}</p>
      {sub && <p className="text-xs text-[#6e7681] mt-1">{sub}</p>}
    </div>
  )
}

export function Dashboard() {
  const { data: account, isLoading: acctLoading } = useAccount()
  const { data: positions } = usePositions()
  const { data: orders } = useOrders()
  const { data: riskSummary } = useRiskSummary()

  const recentOrders: LiveOrder[] = (orders ?? []).slice(0, 5)
  const openPositions: Position[] = (positions ?? []).filter((p) => p.qty !== 0)

  const totalPnl = openPositions.reduce((sum, p) => sum + (p.unrealized_pnl ?? 0), 0)
  const pnlAccent = totalPnl > 0 ? "up" : totalPnl < 0 ? "down" : "neutral"

  return (
    <AppShell title="仪表盘">
      {/* Stats */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        {acctLoading ? (
          <div className="col-span-4 flex justify-center py-8">
            <Spinner />
          </div>
        ) : (
          <>
            <StatCard
              label="组合净值"
              value={account ? `$${account.portfolio_value.toLocaleString()}` : "—"}
              sub={account ? `货币：${account.currency}` : undefined}
            />
            <StatCard
              label="可用资金"
              value={account ? `$${account.cash.toLocaleString()}` : "—"}
              sub={account ? `购买力：$${account.buying_power.toLocaleString()}` : undefined}
            />
            <StatCard
              label="持仓浮盈"
              value={
                totalPnl !== 0
                  ? `${totalPnl >= 0 ? "+" : ""}$${totalPnl.toFixed(2)}`
                  : "—"
              }
              sub={`${openPositions.length} 个持仓`}
              accent={pnlAccent}
            />
            <StatCard
              label="今日提交"
              value={String(riskSummary?.daily_orders_submitted ?? "—")}
              sub="风控限额内"
            />
          </>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Recent Orders */}
        <div className="card">
          <div className="card-header">
            <h2 className="text-sm font-semibold text-[#e6edf3]">近期订单</h2>
          </div>
          <div className="overflow-x-auto">
            {recentOrders.length === 0 ? (
              <p className="text-[#6e7681] text-sm py-6 text-center">暂无订单</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-[#8b949e] border-b border-[#21262d] text-xs">
                    <th className="text-left py-2 pr-3">标的</th>
                    <th className="text-left py-2 pr-3">方向</th>
                    <th className="text-right py-2 pr-3">数量</th>
                    <th className="text-left py-2">状态</th>
                  </tr>
                </thead>
                <tbody>
                  {recentOrders.map((o) => (
                    <tr key={o.order_id} className="border-b border-[#21262d]/50 last:border-0">
                      <td className="py-2 pr-3 font-mono text-[#e6edf3]">
                        {o.symbol}
                        <span className="ml-1 text-[#6e7681] text-xs">{o.market}</span>
                      </td>
                      <td className={`py-2 pr-3 font-medium ${o.side === "BUY" ? "text-[#3fb950]" : "text-[#f85149]"}`}>
                        {o.side === "BUY" ? "买入" : "卖出"}
                      </td>
                      <td className="py-2 pr-3 text-right font-mono text-[#e6edf3]">{o.qty}</td>
                      <td className="py-2"><StatusBadge status={o.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>

        {/* Open Positions */}
        <div className="card">
          <div className="card-header">
            <h2 className="text-sm font-semibold text-[#e6edf3]">当前持仓</h2>
          </div>
          <div className="overflow-x-auto">
            {openPositions.length === 0 ? (
              <p className="text-[#6e7681] text-sm py-6 text-center">暂无持仓</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-[#8b949e] border-b border-[#21262d] text-xs">
                    <th className="text-left py-2 pr-3">标的</th>
                    <th className="text-right py-2 pr-3">数量</th>
                    <th className="text-right py-2 pr-3">均价</th>
                    <th className="text-right py-2">浮盈</th>
                  </tr>
                </thead>
                <tbody>
                  {openPositions.map((p) => (
                    <tr key={`${p.market}-${p.symbol}`} className="border-b border-[#21262d]/50 last:border-0">
                      <td className="py-2 pr-3 font-mono text-[#e6edf3]">
                        {p.symbol}
                        <span className="ml-1 text-[#6e7681] text-xs">{p.market}</span>
                      </td>
                      <td className="py-2 pr-3 text-right font-mono text-[#e6edf3]">{p.qty}</td>
                      <td className="py-2 pr-3 text-right font-mono text-[#e6edf3]">
                        ${p.avg_cost.toFixed(2)}
                      </td>
                      <td className="py-2 text-right">
                        <PnlCell value={p.unrealized_pnl} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>
    </AppShell>
  )
}
