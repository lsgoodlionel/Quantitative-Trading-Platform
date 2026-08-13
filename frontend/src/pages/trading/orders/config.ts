// 订单中心共享的市场/状态定义与统计：下单面板、订单表与统计卡都要用。
import type { LiveOrder, Market } from "@/types"

export const CANCELLABLE_STATUSES: LiveOrder["status"][] = ["pending_submit", "submitted", "partial"]

export const MARKET_CFGS = [
  { value: "US" as Market, label: "美股", currency: "$",  badge: "bg-[#1f3a5f] text-[#58a6ff]" },
  { value: "HK" as Market, label: "港股", currency: "HK$", badge: "bg-[#2a1f4f] text-[#bc8cff]" },
  { value: "A"  as Market, label: "A股",  currency: "¥",  badge: "bg-[#4f3a1a] text-[#e3b341]" },
]

export const STATUS_TABS = [
  { label: "全部", value: "" },
  { label: "挂单中", value: "submitted" },
  { label: "已成交", value: "filled" },
  { label: "已撤销", value: "cancelled" },
] as const

// ── 辅助函数 ──────────────────────────────────────────────────
export function formatPrice(price: number | null, market: string): string {
  if (price == null) return "市价"
  const cfg = MARKET_CFGS.find((m) => m.value === market)
  const symbol = cfg?.currency ?? "$"
  return `${symbol}${price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

export function calcOrderStats(orders: LiveOrder[]) {
  const filled = orders.filter((o) => o.status === "filled")
  const buys = filled.filter((o) => o.side === "BUY").length
  const sells = filled.filter((o) => o.side === "SELL").length
  const pending = orders.filter((o) => CANCELLABLE_STATUSES.includes(o.status)).length
  return { total: orders.length, filled: filled.length, buys, sells, pending }
}
