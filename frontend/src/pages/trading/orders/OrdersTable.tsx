// 订单列表：状态筛选页签 + 订单表（含撤单入口）。
import { EmptyState } from "@/components/ui/EmptyState"
import { Spinner } from "@/components/ui/Spinner"
import { StatusBadge } from "@/components/ui/StatusBadge"
import type { LiveOrder } from "@/types"
import { CANCELLABLE_STATUSES, MARKET_CFGS, STATUS_TABS, formatPrice } from "./config"

function OrderRow({
  order,
  onCancel,
}: {
  order: LiveOrder
  onCancel: (id: string) => void
}) {
  const cancellable = CANCELLABLE_STATUSES.includes(order.status)
  const isBuy = order.side === "BUY"
  const marketCfg = MARKET_CFGS.find((m) => m.value === order.market)

  return (
    <tr className="border-b border-[#21262d]/50 last:border-0 text-sm hover:bg-[#1c2128]/50 transition-colors">
      <td className="py-2.5 px-4 font-mono text-[#8b949e] text-xs">{order.order_id.slice(0, 8)}…</td>
      <td className="py-2.5 pr-3">
        <div className="flex items-center gap-1.5">
          <span className="font-mono font-medium text-[#e6edf3]">{order.symbol}</span>
          <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${marketCfg?.badge ?? "text-[#6e7681]"}`}>
            {order.market}
          </span>
        </div>
      </td>
      <td className="py-2.5 pr-3">
        <span className={`inline-flex items-center gap-1 text-xs font-semibold ${isBuy ? "text-[#3fb950]" : "text-[#f85149]"}`}>
          {isBuy ? "▲ 买入" : "▼ 卖出"}
        </span>
      </td>
      <td className="py-2.5 pr-3 text-[#8b949e] text-xs">{order.order_type === "MARKET" ? "市价" : "限价"}</td>
      <td className="py-2.5 pr-3 text-right font-mono text-[#e6edf3]">{order.qty.toLocaleString()}</td>
      <td className="py-2.5 pr-3 text-right font-mono text-[#8b949e] text-xs">
        {order.limit_price ? formatPrice(order.limit_price, order.market) : "—"}
      </td>
      <td className="py-2.5 pr-3 text-right font-mono text-xs">
        <span className={order.filled_qty > 0 ? "text-[#3fb950]" : "text-[#6e7681]"}>
          {order.filled_qty.toLocaleString()}/{order.qty.toLocaleString()}
        </span>
      </td>
      <td className="py-2.5 pr-3 text-right font-mono text-xs text-[#e6edf3]">
        {order.avg_fill_price ? formatPrice(order.avg_fill_price, order.market) : "—"}
      </td>
      <td className="py-2.5 pr-3"><StatusBadge status={order.status} /></td>
      <td className="py-2.5 pr-3 text-xs text-[#6e7681] font-mono whitespace-nowrap">
        {order.created_at.slice(0, 16).replace("T", " ")}
      </td>
      <td className="py-2.5 pl-2 pr-4">
        {cancellable && (
          <button
            onClick={() => onCancel(order.order_id)}
            className="text-xs text-[#f85149] hover:text-[#ff7b72] border border-[#f85149]/30 hover:border-[#f85149]/60 rounded px-2 py-0.5 transition-colors"
          >
            撤单
          </button>
        )}
      </td>
    </tr>
  )
}

interface OrdersTableProps {
  orders: LiveOrder[]
  statusFilter: string
  onStatusFilterChange: (status: string) => void
  isLoading: boolean
  onCancel: (id: string) => void
}

export function OrdersTable({
  orders, statusFilter, onStatusFilterChange, isLoading, onCancel,
}: OrdersTableProps) {
  const filtered = statusFilter ? orders.filter((o) => o.status === statusFilter) : orders

  return (
    <>
      {/* 状态筛选 tabs */}
      <div className="flex items-center gap-1 mb-4 border-b border-[#21262d] pb-3">
        {STATUS_TABS.map((tab) => (
          <button
            key={tab.value}
            onClick={() => onStatusFilterChange(tab.value)}
            className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${
              statusFilter === tab.value
                ? "bg-[#1f6feb]/20 text-[#58a6ff] border border-[#58a6ff]/30"
                : "text-[#6e7681] hover:text-[#e6edf3] border border-transparent"
            }`}
          >
            {tab.label}
            {tab.value === "" && orders.length > 0 && (
              <span className="ml-1 bg-[#30363d] text-[#8b949e] text-[10px] px-1.5 py-0.5 rounded-full">
                {orders.length}
              </span>
            )}
          </button>
        ))}
        <div className="ml-auto text-xs text-[#6e7681]">每 5 秒自动刷新</div>
      </div>

      <div className="card p-0">
        {isLoading && (
          <div className="flex justify-center py-12"><Spinner size="lg" /></div>
        )}
        {!isLoading && filtered.length === 0 && (
          <EmptyState
            title="暂无订单"
            description={statusFilter ? `没有"${STATUS_TABS.find((t) => t.value === statusFilter)?.label}"状态的订单` : "使用左侧面板提交第一笔订单"}
          />
        )}
        {filtered.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm min-w-[900px]">
              <thead>
                <tr className="text-[#8b949e] text-xs border-b border-[#21262d]">
                  <th className="text-left px-4 py-3">订单ID</th>
                  <th className="text-left py-3 pr-3">标的</th>
                  <th className="text-left py-3 pr-3">方向</th>
                  <th className="text-left py-3 pr-3">类型</th>
                  <th className="text-right py-3 pr-3">委托量</th>
                  <th className="text-right py-3 pr-3">委托价</th>
                  <th className="text-right py-3 pr-3">成交/委托</th>
                  <th className="text-right py-3 pr-3">成交价</th>
                  <th className="text-left py-3 pr-3">状态</th>
                  <th className="text-left py-3 pr-3">时间</th>
                  <th className="py-3 pr-4"></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((o) => (
                  <OrderRow key={o.order_id} order={o} onCancel={onCancel} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  )
}
