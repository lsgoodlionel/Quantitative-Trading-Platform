import { useState } from "react"
import { AppShell } from "@/components/layout/AppShell"
import { useOrders, useCreateOrder, useCancelOrder } from "@/hooks/useOrders"
import { Spinner } from "@/components/ui/Spinner"
import { EmptyState } from "@/components/ui/EmptyState"
import { StatusBadge } from "@/components/ui/StatusBadge"
import { useToast } from "@/components/ui/Toast"
import type { LiveOrder, Market, OrderSide, OrderType } from "@/types"

// ── 常量 ──────────────────────────────────────────────────────
const CANCELLABLE_STATUSES: LiveOrder["status"][] = ["pending_submit", "submitted", "partial"]

const MARKET_CFGS = [
  { value: "US" as Market, label: "美股", currency: "$",  badge: "bg-[#1f3a5f] text-[#58a6ff]" },
  { value: "HK" as Market, label: "港股", currency: "HK$", badge: "bg-[#2a1f4f] text-[#bc8cff]" },
  { value: "A"  as Market, label: "A股",  currency: "¥",  badge: "bg-[#4f3a1a] text-[#e3b341]" },
]

const STATUS_TABS = [
  { label: "全部", value: "" },
  { label: "挂单中", value: "submitted" },
  { label: "已成交", value: "filled" },
  { label: "已撤销", value: "cancelled" },
] as const

// ── 辅助函数 ──────────────────────────────────────────────────
function formatPrice(price: number | null, market: string): string {
  if (price == null) return "市价"
  const cfg = MARKET_CFGS.find((m) => m.value === market)
  const symbol = cfg?.currency ?? "$"
  return `${symbol}${price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function calcOrderStats(orders: LiveOrder[]) {
  const filled = orders.filter((o) => o.status === "filled")
  const buys = filled.filter((o) => o.side === "BUY").length
  const sells = filled.filter((o) => o.side === "SELL").length
  const pending = orders.filter((o) => CANCELLABLE_STATUSES.includes(o.status)).length
  return { total: orders.length, filled: filled.length, buys, sells, pending }
}

// ── 组件 ──────────────────────────────────────────────────────

interface NewOrderForm {
  symbol: string
  market: Market
  side: OrderSide
  qty: string
  order_type: OrderType
  limit_price: string
}

interface OrderEntryPanelProps {
  form: NewOrderForm
  onFormChange: (key: keyof NewOrderForm, val: string) => void
  onSubmit: (e: React.FormEvent) => void
  isSubmitting: boolean
}

function OrderEntryPanel({ form, onFormChange, onSubmit, isSubmitting }: OrderEntryPanelProps) {
  const isBuy = form.side === "BUY"
  const marketCfg = MARKET_CFGS.find((m) => m.value === form.market) ?? MARKET_CFGS[0]

  return (
    <form onSubmit={onSubmit} className="card h-fit">
      <h2 className="text-sm font-semibold text-[#e6edf3] mb-4">快速下单</h2>

      {/* 买卖方向 */}
      <div className="flex rounded-md overflow-hidden border border-[#30363d] mb-4">
        <button
          type="button"
          onClick={() => onFormChange("side", "BUY")}
          className={`flex-1 py-2 text-sm font-semibold transition-colors ${
            isBuy
              ? "bg-[#1a3a24] text-[#3fb950] border-r border-[#3fb950]/30"
              : "text-[#6e7681] hover:text-[#e6edf3] border-r border-[#30363d]"
          }`}
        >
          买入 / Buy
        </button>
        <button
          type="button"
          onClick={() => onFormChange("side", "SELL")}
          className={`flex-1 py-2 text-sm font-semibold transition-colors ${
            !isBuy
              ? "bg-[#2a1b1b] text-[#f85149]"
              : "text-[#6e7681] hover:text-[#e6edf3]"
          }`}
        >
          卖出 / Sell
        </button>
      </div>

      <div className="space-y-3">
        {/* 市场 + 标的 */}
        <div className="grid grid-cols-5 gap-2">
          <div className="col-span-2">
            <label className="label">市场</label>
            <select
              className="select w-full mt-1"
              value={form.market}
              onChange={(e) => onFormChange("market", e.target.value)}
            >
              {MARKET_CFGS.map((m) => (
                <option key={m.value} value={m.value}>{m.label}</option>
              ))}
            </select>
          </div>
          <div className="col-span-3">
            <label className="label">
              标的代码
              <span className="text-[10px] text-[#6e7681] ml-1">
                {form.market === "A" ? "如 000001" : form.market === "HK" ? "如 00700" : "如 AAPL"}
              </span>
            </label>
            <input
              className="input w-full mt-1 font-mono uppercase"
              value={form.symbol}
              onChange={(e) => onFormChange("symbol", e.target.value.toUpperCase())}
              placeholder={form.market === "A" ? "000001" : form.market === "HK" ? "00700" : "AAPL"}
            />
          </div>
        </div>

        {/* 类型 + 数量 */}
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="label">订单类型</label>
            <select
              className="select w-full mt-1"
              value={form.order_type}
              onChange={(e) => onFormChange("order_type", e.target.value)}
            >
              <option value="MARKET">市价单</option>
              <option value="LIMIT">限价单</option>
            </select>
          </div>
          <div>
            <label className="label">数量 (股)</label>
            <input
              className="input w-full mt-1 font-mono"
              type="number"
              value={form.qty}
              onChange={(e) => onFormChange("qty", e.target.value)}
              min={1}
              step={form.market === "A" ? 100 : 1}
            />
          </div>
        </div>

        {/* 限价 */}
        {form.order_type === "LIMIT" && (
          <div>
            <label className="label">限价 ({marketCfg.currency})</label>
            <input
              className="input w-full mt-1 font-mono"
              type="number"
              step="0.01"
              value={form.limit_price}
              onChange={(e) => onFormChange("limit_price", e.target.value)}
              placeholder="0.00"
            />
          </div>
        )}

        {/* 提交按钮 */}
        <button
          type="submit"
          disabled={isSubmitting}
          className={`w-full py-2.5 rounded-md text-sm font-semibold mt-2 transition-all ${
            isBuy
              ? "bg-[#1a3a24] text-[#3fb950] border border-[#3fb950]/40 hover:bg-[#1e4a2c] hover:border-[#3fb950]/60"
              : "bg-[#2a1b1b] text-[#f85149] border border-[#f85149]/40 hover:bg-[#3a1e1e] hover:border-[#f85149]/60"
          } disabled:opacity-50`}
        >
          {isSubmitting ? (
            <Spinner size="sm" className="mx-auto" />
          ) : isBuy ? (
            `确认买入`
          ) : (
            `确认卖出`
          )}
        </button>

        <p className="text-[10px] text-[#6e7681] text-center">
          纸面交易模式 · 市价单立即模拟成交
        </p>
      </div>
    </form>
  )
}

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

// ── 主页面 ────────────────────────────────────────────────────

const DEFAULT_FORM: NewOrderForm = {
  symbol: "AAPL",
  market: "US",
  side: "BUY",
  qty: "100",
  order_type: "MARKET",
  limit_price: "",
}

export function Orders() {
  const { data: orders, isLoading } = useOrders()
  const { mutate: createOrder, isPending: creating } = useCreateOrder()
  const { mutate: cancelOrder } = useCancelOrder()
  const { toast } = useToast()

  const [form, setForm] = useState<NewOrderForm>(DEFAULT_FORM)
  const [statusFilter, setStatusFilter] = useState<string>("")

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
  const filtered = statusFilter
    ? allOrders.filter((o) => o.status === statusFilter)
    : allOrders

  const stats = calcOrderStats(allOrders)

  return (
    <AppShell title="订单中心">
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

      <div className="grid grid-cols-1 xl:grid-cols-4 gap-6">
        {/* 左侧：下单面板 */}
        <div className="xl:col-span-1">
          <OrderEntryPanel
            form={form}
            onFormChange={handleFormChange}
            onSubmit={handleSubmit}
            isSubmitting={creating}
          />
        </div>

        {/* 右侧：订单列表 */}
        <div className="xl:col-span-3">
          {/* 状态筛选 tabs */}
          <div className="flex items-center gap-1 mb-4 border-b border-[#21262d] pb-3">
            {STATUS_TABS.map((tab) => (
              <button
                key={tab.value}
                onClick={() => setStatusFilter(tab.value)}
                className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${
                  statusFilter === tab.value
                    ? "bg-[#1f6feb]/20 text-[#58a6ff] border border-[#58a6ff]/30"
                    : "text-[#6e7681] hover:text-[#e6edf3] border border-transparent"
                }`}
              >
                {tab.label}
                {tab.value === "" && allOrders.length > 0 && (
                  <span className="ml-1 bg-[#30363d] text-[#8b949e] text-[10px] px-1.5 py-0.5 rounded-full">
                    {allOrders.length}
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
                      <OrderRow key={o.order_id} order={o} onCancel={handleCancel} />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      </div>
    </AppShell>
  )
}
