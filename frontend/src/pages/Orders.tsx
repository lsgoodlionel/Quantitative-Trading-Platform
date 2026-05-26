import { useState } from "react"
import { AppShell } from "@/components/layout/AppShell"
import { useOrders, useCreateOrder, useCancelOrder } from "@/hooks/useOrders"
import { Spinner } from "@/components/ui/Spinner"
import { EmptyState } from "@/components/ui/EmptyState"
import { StatusBadge } from "@/components/ui/StatusBadge"
import { Modal } from "@/components/ui/Modal"
import { useToast } from "@/components/ui/Toast"
import type { LiveOrder, Market, OrderSide, OrderType } from "@/types"

const CANCELLABLE: LiveOrder["status"][] = ["pending_submit", "submitted", "partial"]

function OrderRow({ order, onCancel }: { order: LiveOrder; onCancel: (id: string) => void }) {
  const cancellable = CANCELLABLE.includes(order.status)
  return (
    <tr className="border-b border-[#21262d]/50 last:border-0 text-sm">
      <td className="py-2 pr-3 font-mono text-[#8b949e] text-xs">{order.order_id.slice(0, 8)}</td>
      <td className="py-2 pr-3">
        <span className="font-mono text-[#e6edf3]">{order.symbol}</span>
        <span className="ml-1 text-xs text-[#6e7681]">{order.market}</span>
      </td>
      <td className={`py-2 pr-3 font-medium ${order.side === "BUY" ? "text-[#3fb950]" : "text-[#f85149]"}`}>
        {order.side === "BUY" ? "买入" : "卖出"}
      </td>
      <td className="py-2 pr-3 text-[#8b949e] text-xs">{order.order_type}</td>
      <td className="py-2 pr-3 text-right font-mono text-[#e6edf3]">{order.qty}</td>
      <td className="py-2 pr-3 text-right font-mono text-[#8b949e]">
        {order.limit_price ? `$${order.limit_price.toFixed(2)}` : "市价"}
      </td>
      <td className="py-2 pr-3 text-right font-mono text-[#e6edf3]">
        {order.filled_qty}/{order.qty}
      </td>
      <td className="py-2 pr-3 text-right font-mono text-[#8b949e]">
        {order.avg_fill_price ? `$${order.avg_fill_price.toFixed(2)}` : "—"}
      </td>
      <td className="py-2 pr-3"><StatusBadge status={order.status} /></td>
      <td className="py-2 text-xs text-[#6e7681] font-mono">{order.created_at.slice(0, 10)}</td>
      <td className="py-2 pl-2">
        {cancellable && (
          <button
            onClick={() => onCancel(order.order_id)}
            className="text-xs text-[#f85149] hover:text-[#ff7b72] border border-[#f85149]/30 hover:border-[#f85149] rounded px-2 py-0.5 transition-colors"
          >
            撤单
          </button>
        )}
      </td>
    </tr>
  )
}

interface NewOrderForm {
  symbol: string
  market: Market
  side: OrderSide
  qty: string
  order_type: OrderType
  limit_price: string
}

export function Orders() {
  const { data: orders, isLoading } = useOrders()
  const { mutate: createOrder, isPending: creating } = useCreateOrder()
  const { mutate: cancelOrder } = useCancelOrder()
  const { toast } = useToast()
  const [showModal, setShowModal] = useState(false)
  const [form, setForm] = useState<NewOrderForm>({
    symbol: "AAPL", market: "US", side: "BUY", qty: "100",
    order_type: "MARKET", limit_price: "",
  })

  function handleCancel(orderId: string) {
    cancelOrder(orderId, {
      onSuccess: () => toast("撤单成功", "success"),
      onError: (e) => toast(e.message, "error"),
    })
  }

  function handleCreate(e: React.FormEvent) {
    e.preventDefault()
    const qty = parseInt(form.qty, 10)
    if (!qty || qty <= 0) { toast("请输入有效数量", "warning"); return }

    createOrder(
      {
        symbol: form.symbol.toUpperCase(),
        market: form.market,
        side: form.side,
        qty,
        order_type: form.order_type,
        limit_price: form.order_type === "LIMIT" && form.limit_price ? parseFloat(form.limit_price) : null,
      },
      {
        onSuccess: () => { setShowModal(false); toast("下单成功", "success") },
        onError: (e) => toast(e.message, "error"),
      },
    )
  }

  function updateForm(key: keyof NewOrderForm, val: string) {
    setForm((prev) => ({ ...prev, [key]: val }))
  }

  return (
    <AppShell title="订单记录">
      <div className="flex justify-between items-center mb-6">
        <p className="text-[#8b949e] text-sm">每 5 秒自动刷新</p>
        <button className="btn btn-primary" onClick={() => setShowModal(true)}>+ 新建订单</button>
      </div>

      <div className="card p-0">
        {isLoading && (
          <div className="flex justify-center py-12"><Spinner size="lg" /></div>
        )}
        {!isLoading && (!orders || orders.length === 0) && (
          <EmptyState title="暂无订单" description="点击右上角新建订单" />
        )}
        {orders && orders.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm min-w-[800px]">
              <thead>
                <tr className="text-[#8b949e] text-xs border-b border-[#21262d]">
                  <th className="text-left px-4 py-3 pr-3">订单ID</th>
                  <th className="text-left py-3 pr-3">标的</th>
                  <th className="text-left py-3 pr-3">方向</th>
                  <th className="text-left py-3 pr-3">类型</th>
                  <th className="text-right py-3 pr-3">委托量</th>
                  <th className="text-right py-3 pr-3">委托价</th>
                  <th className="text-right py-3 pr-3">成交量</th>
                  <th className="text-right py-3 pr-3">成交价</th>
                  <th className="text-left py-3 pr-3">状态</th>
                  <th className="text-left py-3">时间</th>
                  <th className="py-3"></th>
                </tr>
              </thead>
              <tbody>
                {orders.map((o) => (
                  <OrderRow key={o.order_id} order={o} onCancel={handleCancel} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* New Order Modal */}
      <Modal open={showModal} onClose={() => setShowModal(false)} title="新建订单">
        <form onSubmit={handleCreate} className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label">标的代码</label>
              <input className="input w-full mt-1 font-mono uppercase" value={form.symbol}
                onChange={(e) => updateForm("symbol", e.target.value.toUpperCase())} />
            </div>
            <div>
              <label className="label">市场</label>
              <select className="select w-full mt-1" value={form.market} onChange={(e) => updateForm("market", e.target.value)}>
                <option value="US">US</option>
                <option value="HK">HK</option>
              </select>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label">方向</label>
              <select className="select w-full mt-1" value={form.side} onChange={(e) => updateForm("side", e.target.value)}>
                <option value="BUY">买入</option>
                <option value="SELL">卖出</option>
              </select>
            </div>
            <div>
              <label className="label">类型</label>
              <select className="select w-full mt-1" value={form.order_type} onChange={(e) => updateForm("order_type", e.target.value)}>
                <option value="MARKET">市价单</option>
                <option value="LIMIT">限价单</option>
              </select>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="label">数量</label>
              <input className="input w-full mt-1 font-mono" type="number" value={form.qty}
                onChange={(e) => updateForm("qty", e.target.value)} min={1} />
            </div>
            {form.order_type === "LIMIT" && (
              <div>
                <label className="label">限价 ($)</label>
                <input className="input w-full mt-1 font-mono" type="number" step="0.01" value={form.limit_price}
                  onChange={(e) => updateForm("limit_price", e.target.value)} />
              </div>
            )}
          </div>

          <div className="flex gap-3 pt-2">
            <button type="button" className="btn btn-ghost flex-1" onClick={() => setShowModal(false)}>取消</button>
            <button
              type="submit"
              disabled={creating}
              className={`btn flex-1 font-semibold ${form.side === "BUY" ? "bg-[#1a3a24] text-[#3fb950] border border-[#3fb950]/40 hover:bg-[#1e4a2c]" : "bg-[#2a1b1b] text-[#f85149] border border-[#f85149]/40 hover:bg-[#3a1e1e]"}`}
            >
              {creating ? <Spinner size="sm" className="mx-auto" /> : form.side === "BUY" ? "确认买入" : "确认卖出"}
            </button>
          </div>
        </form>
      </Modal>
    </AppShell>
  )
}
