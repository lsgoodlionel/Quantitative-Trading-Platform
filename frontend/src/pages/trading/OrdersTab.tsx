// 订单中心 Tab（原 /orders 整页）：手动下单 / 高级算法单 + 订单列表。
// 由 pages/Orders.tsx 迁入交易页的一个 Tab（V3 · H1），逻辑未改。
import { useState } from "react"
import { useToast } from "@/components/ui/Toast"
import { useTradingMode } from "@/hooks/useBrokerConfig"
import { useCancelOrder, useCreateOrder, useOrders } from "@/hooks/useOrders"
import { usePermissions } from "@/hooks/useRbac"
import { AdvancedOrderSection } from "./orders/AdvancedOrderSection"
import { calcOrderStats } from "./orders/config"
import { LiveTradingGuide } from "./orders/LiveTradingGuide"
import { OrderEntryPanel, type NewOrderForm } from "./orders/OrderEntryPanel"
import { OrdersTable } from "./orders/OrdersTable"

const DEFAULT_FORM: NewOrderForm = {
  symbol: "AAPL",
  market: "US",
  side: "BUY",
  qty: "100",
  order_type: "MARKET",
  limit_price: "",
}

const ORDER_MODES = [
  { value: "manual", label: "📋 手动下单" },
  { value: "advanced", label: "🧊 高级算法单" },
] as const

type OrderMode = (typeof ORDER_MODES)[number]["value"]

export function OrdersTab() {
  const { data: orders, isLoading } = useOrders()
  const { mutate: createOrder, isPending: creating } = useCreateOrder()
  const { mutate: cancelOrder } = useCancelOrder()
  const { data: tradingMode } = useTradingMode()
  const { canTrade } = usePermissions()
  const { toast } = useToast()

  const [form, setForm] = useState<NewOrderForm>(DEFAULT_FORM)
  const [statusFilter, setStatusFilter] = useState<string>("")
  const [orderMode, setOrderMode] = useState<OrderMode>("manual")

  function handleFormChange(key: keyof NewOrderForm, val: string) {
    setForm((prev) => ({ ...prev, [key]: val }))
  }

  function handleCancel(orderId: string) {
    cancelOrder(orderId, {
      onSuccess: () => toast("撤单成功", "success"),
      onError: (e) => toast(e.message, "error"),
    })
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!canTrade) { toast("当前角色无下单权限", "warning"); return }
    const qty = parseInt(form.qty, 10)
    if (!qty || qty <= 0) { toast("请输入有效数量", "warning"); return }
    if (!form.symbol.trim()) { toast("请输入标的代码", "warning"); return }
    if (form.order_type === "LIMIT" && !form.limit_price) {
      toast("限价单需填写价格", "warning"); return
    }

    createOrder(
      {
        symbol: form.symbol.trim().toUpperCase(),
        market: form.market,
        side: form.side,
        qty,
        order_type: form.order_type,
        limit_price: form.order_type === "LIMIT" ? parseFloat(form.limit_price) : null,
      },
      {
        onSuccess: () => toast(`${form.side === "BUY" ? "买入" : "卖出"}订单已提交`, "success"),
        onError: (e) => toast(e.message, "error"),
      },
    )
  }

  const allOrders = orders ?? []
  const stats = calcOrderStats(allOrders)

  return (
    <>
      {/* 实盘接入引导 */}
      <LiveTradingGuide tradingMode={tradingMode} />

      {/* 交易模式横幅（已配置时的简洁状态行） */}
      {tradingMode?.configured && (
        <div className={`flex items-center gap-3 mb-4 px-4 py-2.5 rounded-lg border text-sm ${
          tradingMode.paper_mode
            ? "bg-[#1f2d45] border-[#388bfd]/30 text-[#58a6ff]"
            : "bg-[#2a1515] border-[#f85149]/40 text-[#f85149]"
        }`}>
          <span className={`inline-block w-2 h-2 rounded-full ${
            tradingMode.paper_mode ? "bg-[#58a6ff]" : "bg-[#f85149] animate-pulse"
          }`} />
          <span className="font-medium">
            {tradingMode.paper_mode ? "📋 模拟盘交易" : "💰 实盘交易 — 真实资金"}
          </span>
          <span className="text-xs opacity-70">{tradingMode.mode_label}</span>
          {tradingMode.base_url && (
            <span className="text-xs opacity-50 ml-auto font-mono truncate max-w-48">
              {tradingMode.base_url}
            </span>
          )}
        </div>
      )}

      {/* 统计卡片 */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3 mb-6">
        {[
          { label: "总订单", value: stats.total, color: "text-[#e6edf3]" },
          { label: "已成交", value: stats.filled, color: "text-[#3fb950]" },
          { label: "买入单", value: stats.buys,   color: "text-[#3fb950]" },
          { label: "卖出单", value: stats.sells,  color: "text-[#f85149]" },
          { label: "挂单中", value: stats.pending, color: "text-[#e3b341]" },
        ].map(({ label, value, color }) => (
          <div key={label} className="card py-3 text-center">
            <p className="text-xs text-[#6e7681] mb-1">{label}</p>
            <p className={`text-xl font-bold font-mono ${color}`}>{value}</p>
          </div>
        ))}
      </div>

      {/* 下单模式切换：手动下单 / 高级算法拆单 */}
      <div className="flex items-center gap-1 mb-5 bg-[#0d1117] border border-[#21262d] rounded-lg p-1 w-fit">
        {ORDER_MODES.map((m) => (
          <button
            key={m.value}
            onClick={() => setOrderMode(m.value)}
            className={`px-4 py-1.5 rounded-md text-xs font-semibold transition-colors ${
              orderMode === m.value
                ? "bg-[#1f6feb]/20 text-[#58a6ff] border border-[#58a6ff]/30"
                : "text-[#6e7681] hover:text-[#e6edf3] border border-transparent"
            }`}
          >
            {m.label}
          </button>
        ))}
      </div>

      {orderMode === "advanced" && <AdvancedOrderSection />}

      {orderMode === "manual" && (
        <div className="grid grid-cols-1 xl:grid-cols-4 gap-6">
          {/* 左侧：下单面板 */}
          <div className="xl:col-span-1">
            <OrderEntryPanel
              form={form}
              onFormChange={handleFormChange}
              onSubmit={handleSubmit}
              isSubmitting={creating}
              canTrade={canTrade}
              tradingMode={tradingMode}
            />
          </div>

          {/* 右侧：订单列表 */}
          <div className="xl:col-span-3">
            <OrdersTable
              orders={allOrders}
              statusFilter={statusFilter}
              onStatusFilterChange={setStatusFilter}
              isLoading={isLoading}
              onCancel={handleCancel}
            />
          </div>
        </div>
      )}
    </>
  )
}
